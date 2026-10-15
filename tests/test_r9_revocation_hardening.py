"""R3 14.10.2026: проверка отзыва TSA — кеш CRL по издателю, без перенаправлений, предел адресов."""

from __future__ import annotations

import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID
from test_r3_anchor import Response, _ec_key
from test_r5_tlog import CRL_URL, Responder, RevPki

from mandat.audit import revocation

OCSP = AuthorityInformationAccessOID.OCSP


class PerIssuerCrl(Responder):
    """OCSP недоступен; CRL по одному адресу отдаётся издателем, чей сертификат проверяется."""

    def __call__(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        if url == CRL_URL:
            issuer = "mid" if self.calls.count(CRL_URL) == 1 else "root"
            return Response(self.crl(issuer))
        raise OSError("ocsp down")


def test_crl_cache_is_keyed_by_issuer():
    """Раньше CRL от mid из кеша проверял и сам mid: отзыв mid в CRL корня не был виден."""
    pki = RevPki()
    responder = PerIssuerCrl(pki)
    responder.revoked[pki.mid.serial_number] = (datetime.now(UTC) - timedelta(hours=1), x509.ReasonFlags.ca_compromise)
    checker = revocation.RevocationChecker(opener=responder)
    problem = checker.problem([pki.leaf, pki.mid, pki.root], datetime.now(UTC))
    assert problem and "CN=mid" in problem
    assert responder.calls.count(CRL_URL) == 2


def test_urls_are_deduplicated_and_capped():
    key = _ec_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "x")])
    urls = [f"http://ocsp{i}.example/" for i in range(10)] + ["http://ocsp0.example/", "ftp://x/", "file:///etc"]
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.AuthorityInformationAccess(
                [x509.AccessDescription(OCSP, x509.UniformResourceIdentifier(u)) for u in urls]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    assert revocation._urls(cert, "ocsp") == [f"http://ocsp{i}.example/" for i in range(revocation.MAX_URLS)]


@pytest.fixture
def redirecting_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/crl"
    server.shutdown()
    server.server_close()


def test_default_opener_does_not_follow_redirects(redirecting_server):
    with pytest.raises(OSError, match="перенаправление 302"):
        revocation._urlopen(urllib.request.Request(redirecting_server), 5)
    checker = revocation.RevocationChecker()
    with pytest.raises(OSError, match="перенаправление"):
        checker._fetch(redirecting_server)
