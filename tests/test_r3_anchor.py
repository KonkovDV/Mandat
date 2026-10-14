"""Внешний якорь журнала (audit/anchor.py): file, webhook, RFC 3161, Rekor — с подставными приёмниками."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asn1crypto import algos, cms, core, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from mandat.audit import anchor, journal
from mandat.services import webhooks

KEY = "k" * 32


@pytest.fixture(autouse=True)
def _segments(monkeypatch):
    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "3")
    for name in ("MANDAT_JOURNAL_ANCHOR", "MANDAT_JOURNAL_ANCHOR_POLICY", "MANDAT_JOURNAL_ANCHOR_SECRET"):
        monkeypatch.delenv(name, raising=False)
    journal._TAILS.clear()


def _fill(path: Path, n: int, sinks, start: int = 0, tag: str = "c") -> None:
    for i in range(start, start + n):
        journal.append(path, action="event", actor="a", role="operator", case_id=f"{tag}{i}", key=KEY, anchors=sinks)


class Response(io.BytesIO):
    def __init__(self, data: bytes = b"", status: int = 200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- файл и журнал ----------------------------------------------------------------------------


def test_file_anchor_per_sealed_segment(tmp_path):
    sink = anchor.FileSink(tmp_path / "elsewhere" / "anchors.jsonl")
    path = tmp_path / "log" / "j.jsonl"
    _fill(path, 7, [sink])
    receipts = anchor.read_receipts(path)
    assert sorted(receipts) == [(1, sink.name), (2, sink.name)]
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok
    stmt = anchor.statement("j.jsonl", 1, journal.read_journal(path)[:3])
    assert stmt["first_seq"] == 1 and stmt["last_seq"] == 3 and len(stmt["merkle_root"]) == 64
    assert anchor.digest(stmt) == receipts[(1, sink.name)]["sha256"]


def test_rewritten_history_with_key_is_caught_by_anchor(tmp_path):
    """Root с ключом переписал журнал целиком: цепочка, HMAC и свидетель сходятся, якорь — нет."""
    sink = anchor.FileSink(tmp_path / "remote" / "anchors.jsonl")
    path = tmp_path / "log" / "j.jsonl"
    _fill(path, 6, [sink])
    forged = tmp_path / "forged" / "j.jsonl"
    _fill(forged, 6, [], tag="forged")
    for item in [*journal.sealed_segments(path), journal.witness_path(path)]:
        item.chmod(0o600)
        item.unlink()
    for item in [*journal.sealed_segments(forged), journal.witness_path(forged)]:
        shutil.copy(item, path.parent / item.name)
    assert journal.verify_journal(path, key=KEY, anchors=[]).ok  # без якоря подделку не видно
    check = journal.verify_journal(path, key=KEY, anchors=[sink])
    assert not check.ok and "не совпадает с якорем" in check.reason


def test_missing_receipt_or_remote_line_fails(tmp_path):
    sink = anchor.FileSink(tmp_path / "remote.jsonl")
    path = tmp_path / "j.jsonl"
    _fill(path, 3, [sink])
    sink.path.write_text("not json\n", encoding="utf-8")
    assert "не совпадает" in journal.verify_journal(path, key=KEY, anchors=[sink]).reason
    sink.path.unlink()
    assert not sink.check({"a": 1}, {"sha256": anchor.digest({"a": 1})})
    anchor.receipts_path(path).unlink()
    assert "не закреплён" in journal.verify_journal(path, key=KEY, anchors=[sink]).reason


def test_unavailable_sink_does_not_stop_journal_and_catches_up(tmp_path, caplog):
    class Down:
        name = "down"

        def publish(self, stmt):
            raise OSError("нет сети")

        def check(self, stmt, receipt):
            return True

    path = tmp_path / "j.jsonl"
    _fill(path, 3, [Down()])
    assert "не опубликован" in caplog.text
    assert "не закреплён" in journal.verify_journal(path, key=KEY, anchors=[Down()]).reason
    sink = anchor.FileSink(tmp_path / "remote.jsonl")
    _fill(path, 3, [sink], start=3)  # следующее закрытие публикует и пропущенный сегмент
    assert sorted(i for i, _ in anchor.read_receipts(path)) == [1, 2]
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok


def test_env_anchor_used_by_append_and_verify(tmp_path, monkeypatch):
    remote = tmp_path / "remote.jsonl"
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR", f"file:{remote}")
    path = tmp_path / "j.jsonl"
    _fill(path, 3, None)
    assert remote.exists() and journal.verify_journal(path, key=KEY).ok
    remote.unlink()
    assert not journal.verify_journal(path, key=KEY).ok


def test_receipts_skip_bad_lines(tmp_path):
    path = tmp_path / "j.jsonl"
    anchor.receipts_path(path).write_text('garbage\n{"segment": "x"}\n', encoding="utf-8")
    assert anchor.read_receipts(path) == {}
    with pytest.raises(ValueError):
        anchor.statement("j", 1, [])


# --- настройка и политика -----------------------------------------------------------------------


def test_configured_sinks(tmp_path, monkeypatch):
    assert anchor.configured_sinks("") == []
    assert isinstance(anchor.configured_sinks(f"file:{tmp_path}/a")[0], anchor.FileSink)
    with pytest.raises(ValueError, match="SECRET"):
        anchor.configured_sinks("webhook:https://h.example/a")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_SECRET", webhooks.new_secret())
    assert anchor.configured_sinks("webhook:https://h.example/a")[0].name == "webhook:https://h.example/a"
    assert isinstance(anchor.configured_sinks("tsa:https://tsa.example")[0], anchor.TsaSink)
    with pytest.raises(ValueError, match="REKOR_KEY"):
        anchor.configured_sinks("rekor:https://rekor.example")
    key = tmp_path / "rekor.pem"
    key.write_bytes(_ec_pem())
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_KEY", str(key))
    assert isinstance(anchor.configured_sinks("rekor:https://rekor.example")[0], anchor.RekorSink)
    with pytest.raises(ValueError, match="неизвестный"):
        anchor.configured_sinks("ftp:x")
    with pytest.raises(ValueError, match="https"):
        anchor.TsaSink("http://tsa.example")


@pytest.mark.parametrize(
    ("env", "pol", "fails"), [("demo", "fail", False), ("pilot", "warn", False), ("prod", "fail", True)]
)
def test_missing_anchor_policy(monkeypatch, caplog, env, pol, fails):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_POLICY", pol)
    problem = anchor.missing_anchor_problem()
    assert (problem is not None) is fails
    if env == "pilot":
        assert "якорь журнала не задан" in caplog.text
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR", "file:/x")
    assert anchor.missing_anchor_problem() is None
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_POLICY", "bogus")
    assert anchor.policy() == "warn"


def test_serve_refuses_without_anchor_when_policy_fail(monkeypatch, capsys):
    from mandat import cli

    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_POLICY", "fail")
    monkeypatch.setattr("mandat.api.principals.access_problem", lambda: None)
    monkeypatch.setattr("mandat.services.env_policy.key_problem", lambda: None)
    assert cli.main(["serve"]) == 2
    assert "якорь" in capsys.readouterr().err
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR", "webhook:https://h.example")  # без секрета
    assert cli.main(["serve"]) == 2
    assert "SECRET" in capsys.readouterr().err


# --- webhook ------------------------------------------------------------------------------------


def test_webhook_sink_signs_and_rejects_errors():
    secret = webhooks.new_secret()
    sent = []

    def opener(request):
        sent.append(request)
        return Response(status=202)

    sink = anchor.WebhookSink("https://h.example/anchor", secret, opener)
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 1}
    receipt = sink.publish(stmt)
    headers = {k.lower(): v for k, v in sent[0].header_items()}
    assert webhooks.signature_ok(secret, headers, sent[0].data, datetime.now(UTC))
    assert sink.check(stmt, receipt) and not sink.check({"x": 1}, receipt)
    with pytest.raises(OSError):
        anchor.WebhookSink("https://h.example", secret, lambda r: Response(status=500)).publish(stmt)


# --- RFC 3161 -----------------------------------------------------------------------------------


def _ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _ec_pem() -> bytes:
    return _ec_key().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _cert(key):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test tsa")])
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


class FakeTsa:
    def __init__(self, status: str = "granted", nonce_shift: int = 0, imprint: bytes | None = None):
        self.key, self.status, self.nonce_shift, self.imprint = _ec_key(), status, nonce_shift, imprint
        self.cert = _cert(self.key)
        self.cert_der = self.cert.public_bytes(serialization.Encoding.DER)

    def __call__(self, request):
        req = tsp.TimeStampReq.load(request.data)
        assert request.get_header("Content-type") == "application/timestamp-query"
        assert req["message_imprint"]["hash_algorithm"]["algorithm"].native == "sha256"
        if self.status != "granted":
            status = tsp.PKIStatusInfo({"status": self.status}).dump()
            return Response(anchor._der(0x30, status))
        info = tsp.TSTInfo(
            {
                "version": 1,
                "policy": "1.2.3.4",
                "message_imprint": {
                    "hash_algorithm": {"algorithm": "sha256"},
                    "hashed_message": self.imprint or req["message_imprint"]["hashed_message"].native,
                },
                "serial_number": 7,
                "gen_time": datetime.now(UTC),
                "nonce": req["nonce"].native + self.nonce_shift,
            }
        )
        content = info.dump()
        attrs = cms.CMSAttributes(
            [
                {"type": "content_type", "values": ["tst_info"]},
                {"type": "message_digest", "values": [hashlib.sha256(content).digest()]},
            ]
        )
        signature = self.key.sign(attrs.dump(), ec.ECDSA(hashes.SHA256()))
        cert = asn1_x509.Certificate.load(self.cert_der)
        signed = cms.SignedData(
            {
                "version": "v3",
                "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
                "encap_content_info": {"content_type": "tst_info", "content": core.ParsableOctetString(content)},
                "certificates": [cert],
                "signer_infos": [
                    {
                        "version": "v1",
                        "sid": cms.SignerIdentifier(
                            {
                                "issuer_and_serial_number": {
                                    "issuer": cert.issuer,
                                    "serial_number": cert.serial_number,
                                }
                            }
                        ),
                        "digest_algorithm": {"algorithm": "sha256"},
                        "signed_attrs": attrs,
                        "signature_algorithm": {"algorithm": "sha256_ecdsa"},
                        "signature": signature,
                    }
                ],
            }
        )
        token = cms.ContentInfo({"content_type": "signed_data", "content": signed})
        return Response(tsp.TimeStampResp({"status": {"status": "granted"}, "time_stamp_token": token}).dump())


def test_tsa_request_der_is_valid():
    digest = hashlib.sha256(b"x").digest()
    req = tsp.TimeStampReq.load(anchor.tsa_request(digest, 2**62))
    assert req["version"].native == "v1" and req["nonce"].native == 2**62 and req["cert_req"].native is True
    assert req["message_imprint"]["hashed_message"].native == digest
    long = anchor._der(0x04, b"a" * 300)
    assert long[:4] == b"\x04\x82\x01\x2c"


def test_tsa_sink_roundtrip_and_pinning(tmp_path):
    tsa = FakeTsa()
    pinned = hashlib.sha256(tsa.cert_der).hexdigest()
    sink = anchor.TsaSink("https://tsa.example", pinned, tsa)
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 3}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    assert not sink.check({**stmt, "segment": 4}, receipt)
    assert not sink.check(stmt, {**receipt, "token": "!!"})
    assert not sink.check(stmt, {**receipt, "nonce": receipt["nonce"] + 1})
    other = anchor.TsaSink("https://tsa.example", "0" * 64, tsa)
    assert not other.check(stmt, receipt)
    with pytest.raises(OSError, match="закреплённым"):
        other.publish(stmt)
    path = tmp_path / "j.jsonl"
    _fill(path, 3, [sink])
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeTsa(status="rejection"), "отказал"),
        (FakeTsa(nonce_shift=1), "nonce"),
        (FakeTsa(imprint=b"\x00" * 32), "другой хеш"),
    ],
)
def test_tsa_sink_rejects_bad_responses(fake, message):
    with pytest.raises(OSError, match=message):
        anchor.TsaSink("https://tsa.example", None, fake).publish({"segment": 1})


def test_tsa_token_signature_and_garbage():
    tsa = FakeTsa()
    stmt = {"segment": 1}
    receipt = anchor.TsaSink("https://tsa.example", None, tsa).publish(stmt)
    token = bytearray(base64.b64decode(receipt["token"]))
    token[-5] ^= 0xFF  # порча подписи
    imprint = bytes.fromhex(anchor.digest(stmt))
    assert anchor.verify_tsa_token(bytes(token), imprint, None, None) is not None
    assert "не разбирается" in anchor.verify_tsa_token(b"\x30\x00", imprint, None, None)


def test_tsa_needs_extra(monkeypatch):
    import builtins

    real = builtins.__import__

    def deny(name, *a, **k):
        if name.startswith("asn1crypto"):
            raise ModuleNotFoundError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(RuntimeError, match=r"mandat\[tsa\]"):
        anchor._asn1()


# --- Rekor --------------------------------------------------------------------------------------


class FakeRekor:
    def __init__(self):
        self.entries: dict[str, dict] = {}

    def __call__(self, request):
        if request.get_method() == "POST":
            entry = json.loads(request.data)
            spec = entry["spec"]
            public = serialization.load_pem_public_key(base64.b64decode(spec["signature"]["publicKey"]["content"]))
            from cryptography.hazmat.primitives.asymmetric import utils

            public.verify(
                base64.b64decode(spec["signature"]["content"]),
                bytes.fromhex(spec["data"]["hash"]["value"]),
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
            uuid = hashlib.sha256(request.data).hexdigest()
            self.entries[uuid] = {"body": base64.b64encode(request.data).decode(), "logIndex": len(self.entries)}
            return Response(json.dumps({uuid: self.entries[uuid]}).encode(), status=201)
        uuid = request.full_url.rsplit("/", 1)[1]
        if uuid not in self.entries:
            raise OSError("404")
        return Response(json.dumps({uuid: self.entries[uuid]}).encode())


def test_rekor_sink_roundtrip(tmp_path):
    rekor = FakeRekor()
    sink = anchor.RekorSink("https://rekor.example", _ec_pem(), rekor)
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 1}
    receipt = sink.publish(stmt)
    assert receipt["log_index"] == 0 and sink.check(stmt, receipt)
    assert not sink.check({"segment": 2}, receipt)
    assert not sink.check(stmt, {**receipt, "uuid": "missing"})
    path = tmp_path / "j.jsonl"
    _fill(path, 3, [sink])
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok
    rekor.entries.clear()
    assert not journal.verify_journal(path, key=KEY, anchors=[sink]).ok


def test_rekor_requires_ec_key():
    from cryptography.hazmat.primitives.asymmetric import ed25519

    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(ValueError, match="P-256"):
        anchor.RekorSink("https://rekor.example", pem, FakeRekor()).publish({"segment": 1})


def test_tsa_garbage_response():
    with pytest.raises(OSError, match="не разбирается"):
        anchor.TsaSink("https://tsa.example", None, lambda r: Response(b"\x01\x02")).publish({"segment": 1})


# --- R2-PERF-3: несколько процессов пишут один журнал ---------------------------------------------


def _other_process(path: Path, n: int, start: int) -> None:
    """Запись «другим процессом»: свой кэш хвоста, наш возвращается после."""
    mine = dict(journal._TAILS)
    journal._TAILS.clear()
    _fill(path, n, [], start=start, tag="other")
    journal._TAILS.clear()
    journal._TAILS.update(mine)


def test_catch_up_checks_only_new_records(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    _fill(path, 2, [])
    _other_process(path, 5, 2)  # в том числе закрытие сегмента чужим процессом
    calls = []
    real = journal.verify_journal
    monkeypatch.setattr(journal, "verify_journal", lambda *a, **k: calls.append(1) or real(*a, **k))
    _fill(path, 1, [], start=7)
    assert calls == []  # полной проверки не было
    assert real(path, key=KEY).records == 8


@pytest.mark.parametrize("damage", ["prefix", "sealed", "witness", "chain"])
def test_catch_up_falls_back_to_full_check(tmp_path, damage):
    path = tmp_path / "j.jsonl"
    _fill(path, 4, [])  # сегмент 1 закрыт, в активном одна запись
    _other_process(path, 1, 4)
    if damage == "prefix":
        text = path.read_text()
        path.write_text(text.replace('"c3"', '"cX"', 1))
    elif damage == "sealed":
        seg = journal.sealed_segments(path)[0]
        seg.chmod(0o600)
        seg.write_text(seg.read_text().replace('"c0"', '"cXY"'))
    elif damage == "witness":
        journal.witness_path(path).write_text('{"records": 1, "hash": "0"}\n')
    else:
        lines = path.read_text().splitlines()
        path.write_text("\n".join([lines[0], lines[1].replace('"other4"', '"otherX"')]) + "\n")
    with pytest.raises(journal.JournalError):
        _fill(path, 1, [], start=9)


def test_catch_up_without_cache_or_files(tmp_path):
    path = tmp_path / "j.jsonl"
    assert journal._catch_up(path, KEY) is None
    _fill(path, 3, [])  # активный файл после закрытия отсутствует
    journal._TAILS[journal._cache_key(path)] = (("x", b"", journal._marker("other"), ()), (3, "h"), 0)
    assert journal._catch_up(path, KEY) is None


# --- TCP_NODELAY ---------------------------------------------------------------------------------


def test_nodelay_protocol_sets_option():
    import socket

    from mandat import cli
    from mandat.api import nodelay

    assert cli.server_options()["http"] == "mandat.api.nodelay:NoDelayH11Protocol"
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    server.bind(("127.0.0.1", 0))
    server.listen()
    client = socket.create_connection(server.getsockname())
    conn, _ = server.accept()

    class Transport:
        def __init__(self, sock):
            self.sock = sock

        def get_extra_info(self, name):
            return self.sock if name == "socket" else None

    assert conn.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 0
    nodelay.set_nodelay(Transport(conn))
    assert conn.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
    nodelay.set_nodelay(Transport(None))
    conn.close()
    nodelay.set_nodelay(Transport(conn))  # закрытый сокет: OSError проглатывается
    client.close()
    server.close()


def test_nodelay_protocol_connection_made(monkeypatch):
    from mandat.api import nodelay

    seen = []
    monkeypatch.setattr(nodelay, "set_nodelay", seen.append)
    monkeypatch.setattr(nodelay.H11Protocol, "connection_made", lambda self, t: seen.append("super"))
    proto = nodelay.NoDelayH11Protocol.__new__(nodelay.NoDelayH11Protocol)
    proto.connection_made("transport")
    assert seen == ["transport", "super"]
