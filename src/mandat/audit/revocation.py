"""Отзыв сертификатов TSA: OCSP (RFC 6960), при неудаче — CRL (RFC 5280), кеш, таймауты, политика.

Проверяется каждый сертификат цепочки, кроме корня: лист TSA и промежуточные CA. Сначала OCSP по адресу
из Authority Information Access: запрос с CertID на SHA-1 (профиль RFC 5019) и nonce (RFC 8954); ответ
подписан издателем или делегированным ответчиком (выпущен издателем, EKU OCSPSigning, RFC 6960,
разд. 4.2.2.2); CertID, thisUpdate/nextUpdate и nonce, если ответчик его вернул, сверяются. Если OCSP
нет, он недоступен или ответил ``unknown`` — CRL из CRL Distribution Points: издатель и подпись CRL,
свежесть по nextUpdate.

Отзыв влияет на метку по RFC 3161, разд. 4: при компрометации ключа (keyCompromise, cACompromise,
aACompromise или причина не указана) недоверие ко всем меткам; при другой причине — только к меткам,
выпущенным не раньше даты отзыва.

Политика ``MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION``: ``off`` — не проверять; ``soft`` (по умолчанию) —
отозванный отвергается, недоступный статус — предупреждение в лог; ``hard`` — недоступный статус тоже
отказ. Ответы кешируются до nextUpdate, но не дольше ``..._REVOCATION_CACHE`` секунд (3600); таймаут
запроса — ``..._REVOCATION_TIMEOUT`` секунд (5).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("mandat.anchor.revocation")
TimedOpener = Callable[[urllib.request.Request, float], Any]
MAX_RESPONSE = 4 * 1024 * 1024
MAX_URLS = 3  # адресов OCSP и CRL на сертификат: длинный список не превращается в серию таймаутов
MODES = ("off", "soft", "hard")
COMPROMISE = {"key_compromise", "ca_compromise", "aa_compromise", "unspecified"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправления не выполняются: адрес из сертификата не уводит запрос на другой хост или схему (ftp)."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise OSError(f"перенаправление {code} не поддержано: {newurl}")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
    return _OPENER.open(request, timeout=timeout)  # адрес из проверенного сертификата, без редиректов


@dataclass(frozen=True)
class Status:
    state: str  # good | revoked | unknown
    source: str
    revoked_at: datetime | None = None
    reason: str | None = None


def affects(status: Status, issued_at: datetime) -> bool:
    """Отзыв делает метку недоверенной (RFC 3161, разд. 4)."""
    if status.state != "revoked":
        return False
    if (status.reason or "unspecified") in COMPROMISE:
        return True
    return status.revoked_at is None or status.revoked_at <= issued_at


def _urls(cert: Any, kind: str) -> list[str]:
    from cryptography import x509
    from cryptography.x509.oid import AuthorityInformationAccessOID

    out: list[str] = []
    try:
        if kind == "ocsp":
            aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
            names = [d.access_location for d in aia if d.access_method == AuthorityInformationAccessOID.OCSP]
        else:
            cdp = cert.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
            names = [n for point in cdp for n in (point.full_name or [])]
    except x509.ExtensionNotFound:
        return out
    for name in names:
        if isinstance(name, x509.UniformResourceIdentifier) and name.value.startswith(("http://", "https://")):
            out.append(name.value)
    return list(dict.fromkeys(out))[:MAX_URLS]


def _verify_signature(public_key: Any, signature: bytes, data: bytes, algorithm: Any) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

    if isinstance(public_key, rsa.RSAPublicKey):
        public_key.verify(signature, data, padding.PKCS1v15(), algorithm)
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(signature, data, ec.ECDSA(algorithm))
    elif isinstance(public_key, ed25519.Ed25519PublicKey):
        public_key.verify(signature, data)
    else:
        raise ValueError("тип ключа подписи статуса не поддержан")


def _key_hash(cert: Any, algorithm: str) -> bytes:
    from asn1crypto import keys  # type: ignore[import-untyped]  # extra [tsa]
    from cryptography.hazmat.primitives import serialization

    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.new(algorithm, keys.PublicKeyInfo.load(spki)["public_key"].contents[1:]).digest()


class RevocationChecker:
    def __init__(
        self,
        mode: str = "soft",
        opener: TimedOpener | None = None,
        timeout: float = 5.0,
        cache_seconds: int = 3600,
        clock: Callable[[], datetime] | None = None,
        skew: timedelta = timedelta(minutes=5),
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"политика отзыва TSA: {' | '.join(MODES)}")
        self.mode, self.timeout, self.cache_seconds, self.skew = mode, timeout, cache_seconds, skew
        self._open = opener or _urlopen
        self._now = clock or (lambda: datetime.now(UTC))
        self._cache: dict[Any, tuple[datetime, Any]] = {}
        self.fetches = 0

    # --- сеть и кеш ---

    def _fetch(self, url: str, data: bytes | None = None, content_type: str | None = None) -> bytes:
        headers = {"content-type": content_type} if content_type else {}
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")  # noqa: S310
        self.fetches += 1
        with self._open(request, self.timeout) as response:
            if int(getattr(response, "status", 200)) >= 300:
                raise OSError(f"ответ {response.status}")
            raw = bytes(response.read(MAX_RESPONSE + 1))
        if len(raw) > MAX_RESPONSE:
            raise OSError("ответ о статусе слишком большой")
        return raw

    def _cached(self, key: Any) -> Any:
        hit = self._cache.get(key)
        if hit and hit[0] > self._now():
            return hit[1]
        self._cache.pop(key, None)
        return None

    def _store(self, key: Any, value: Any, next_update: datetime | None) -> None:
        limit = self._now() + timedelta(seconds=self.cache_seconds)
        self._cache[key] = (min(limit, next_update) if next_update else limit, value)

    def _fresh(self, this_update: datetime, next_update: datetime | None) -> bool:
        now = self._now()
        if this_update > now + self.skew:
            return False
        if next_update is None:
            return this_update >= now - timedelta(seconds=self.cache_seconds) - self.skew
        return next_update >= now - self.skew

    # --- OCSP ---

    def _ocsp(self, cert: Any, issuer: Any, url: str) -> Status:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509 import ocsp
        from cryptography.x509.oid import ExtendedKeyUsageOID

        nonce = secrets.token_bytes(16)
        request = (
            ocsp.OCSPRequestBuilder()
            .add_certificate(cert, issuer, hashes.SHA1())  # noqa: S303 - CertID по профилю RFC 5019
            .add_extension(x509.OCSPNonce(nonce), critical=False)
            .build()
        )
        raw = self._fetch(url, request.public_bytes(serialization.Encoding.DER), "application/ocsp-request")
        response = ocsp.load_der_ocsp_response(raw)
        if response.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
            raise ValueError(f"OCSP: {response.response_status.name}")
        signer = issuer
        if not self._responder_is(issuer, response):
            delegated = [c for c in response.certificates if self._responder_is(c, response)]
            if not delegated:
                raise ValueError("OCSP подписан не издателем и не делегированным ответчиком")
            signer = delegated[0]
            signer.verify_directly_issued_by(issuer)
            eku = signer.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if ExtendedKeyUsageOID.OCSP_SIGNING not in eku:
                raise ValueError("у ответчика OCSP нет EKU OCSPSigning")
            now = self._now()
            if not signer.not_valid_before_utc <= now <= signer.not_valid_after_utc:
                raise ValueError("сертификат ответчика OCSP недействителен")
        _verify_signature(
            signer.public_key(), response.signature, response.tbs_response_bytes, response.signature_hash_algorithm
        )
        try:
            echoed = response.extensions.get_extension_for_class(x509.OCSPNonce).value.nonce
        except x509.ExtensionNotFound:
            echoed = None  # RFC 8954: ответчик вправе не возвращать nonce (готовые ответы, RFC 5019)
        if echoed is not None and echoed != nonce:
            raise ValueError("OCSP: nonce не совпадает")
        name_hash = hashlib.sha1(issuer.subject.public_bytes()).digest()  # noqa: S324 - CertID SHA-1
        key_hash = _key_hash(issuer, "sha1")
        for single in response.responses:
            if (single.serial_number, single.issuer_name_hash, single.issuer_key_hash) != (
                cert.serial_number,
                name_hash,
                key_hash,
            ):
                continue
            if not self._fresh(single.this_update_utc, single.next_update_utc):
                raise ValueError("OCSP: ответ устарел")
            status = single.certificate_status
            if status == ocsp.OCSPCertStatus.GOOD:
                result = Status("good", "ocsp")
            elif status == ocsp.OCSPCertStatus.REVOKED:
                reason = single.revocation_reason.name if single.revocation_reason else None
                result = Status("revoked", "ocsp", single.revocation_time_utc, reason)
            else:
                result = Status("unknown", "ocsp")
            if result.state != "unknown":
                self._store(("status", cert.fingerprint(hashes.SHA256())), result, single.next_update_utc)
            return result
        raise ValueError("OCSP: в ответе нет статуса этого сертификата")

    @staticmethod
    def _responder_is(cert: Any, response: Any) -> bool:
        if response.responder_key_hash is not None:
            return bool(_key_hash(cert, "sha1") == response.responder_key_hash)
        return bool(response.responder_name == cert.subject)

    # --- CRL ---

    def _crl(self, cert: Any, issuer: Any, url: str) -> Status:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        # Ключ кеша — адрес и издатель: один адрес CRL у листа и у CA не подменяет список чужого издателя.
        key = ("crl", url, issuer.fingerprint(hashes.SHA256()))
        crl = self._cached(key)
        if crl is None:
            raw = self._fetch(url)
            crl = x509.load_pem_x509_crl(raw) if raw.lstrip().startswith(b"-----") else x509.load_der_x509_crl(raw)
            if crl.issuer != issuer.subject or not crl.is_signature_valid(issuer.public_key()):
                raise ValueError("CRL выпущен не издателем сертификата")
            if not self._fresh(crl.last_update_utc, crl.next_update_utc):
                raise ValueError("CRL устарел")
            self._store(key, crl, crl.next_update_utc)
        entry = crl.get_revoked_certificate_by_serial_number(cert.serial_number)
        if entry is None:
            return Status("good", "crl")
        try:
            reason = entry.extensions.get_extension_for_class(x509.CRLReason).value.reason.name
        except x509.ExtensionNotFound:
            reason = None
        result = Status("revoked", "crl", entry.revocation_date_utc, reason)
        self._store(("status", cert.fingerprint(hashes.SHA256())), result, crl.next_update_utc)
        return result

    # --- вместе ---

    def status(self, cert: Any, issuer: Any) -> Status:
        from cryptography.hazmat.primitives import hashes

        cached = self._cached(("status", cert.fingerprint(hashes.SHA256())))
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        errors = []
        for kind, method in (("ocsp", self._ocsp), ("crl", self._crl)):
            for url in _urls(cert, kind):
                try:
                    result = method(cert, issuer, url)
                except Exception as exc:  # noqa: BLE001 - недоступный источник: пробуем следующий
                    errors.append(f"{kind} {url}: {type(exc).__name__}: {exc}")
                    continue
                if result.state != "unknown":
                    return result
                errors.append(f"{kind} {url}: unknown")
        return Status("unknown", "; ".join(errors) or "нет адресов OCSP и CRL")

    def problem(self, chain: Sequence[Any], issued_at: datetime) -> str | None:
        """Цепочка — от листа к корню. Причина отказа или ``None``."""
        if self.mode == "off":
            return None
        for cert, issuer in zip(chain, chain[1:], strict=False):
            result = self.status(cert, issuer)
            subject = cert.subject.rfc4514_string()
            if affects(result, issued_at):
                return f"сертификат {subject} отозван ({result.source}, {result.reason or 'причина не указана'})"
            if result.state == "unknown":
                text = f"статус отзыва {subject} неизвестен: {result.source}"
                if self.mode == "hard":
                    return text
                log.warning("%s (политика soft)", text)
        return None


def from_env(environ: dict[str, str] | None = None) -> RevocationChecker:
    import os

    env = os.environ if environ is None else environ
    mode = env.get("MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION", "soft").strip().lower() or "soft"
    timeout = float(env.get("MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION_TIMEOUT", "5"))
    cache = int(env.get("MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION_CACHE", "3600"))
    if not 0 < timeout <= 60 or cache < 0:
        raise ValueError("таймаут проверки отзыва TSA — (0, 60] с, кеш — не меньше 0 с")
    return RevocationChecker(mode, timeout=timeout, cache_seconds=cache)
