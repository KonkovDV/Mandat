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
    for item in [*journal.sealed_segments(path), journal.witness_path(path), journal.manifest_path(path)]:
        item.chmod(0o600)
        item.unlink()
    for item in [*journal.sealed_segments(forged), journal.witness_path(forged), journal.manifest_path(forged)]:
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
    with pytest.raises(ValueError, match="TSA_ROOTS"):
        anchor.configured_sinks("tsa:https://tsa.example")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_TSA_CERT", "ab" * 32)
    assert isinstance(anchor.configured_sinks("tsa:https://tsa.example")[0], anchor.TsaSink)
    monkeypatch.delenv("MANDAT_JOURNAL_ANCHOR_TSA_CERT")
    roots = tmp_path / "roots.pem"
    roots.write_bytes(_cert(_ec_key()).public_bytes(serialization.Encoding.PEM))
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_TSA_ROOTS", str(roots))
    assert anchor.configured_sinks("tsa:https://tsa.example")[0].roots
    roots.write_bytes(b"")
    with pytest.raises(ValueError):
        anchor.configured_sinks("tsa:https://tsa.example")
    with pytest.raises(ValueError, match="REKOR_KEY"):
        anchor.configured_sinks("rekor:https://rekor.example")
    key = tmp_path / "rekor.pem"
    key.write_bytes(_ec_pem())
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_KEY", str(key))
    with pytest.raises(ValueError, match="REKOR_PUBKEY"):
        anchor.configured_sinks("rekor:https://rekor.example")
    log_key = tmp_path / "rekor-log.pub"
    log_key.write_bytes(
        _ec_key().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_PUBKEY", str(log_key))
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


def _ca_cert(subject, issuer, key, signer, *, ca, eku=None, eku_critical=True, intermediate=False):
    from cryptography.x509.oid import ExtendedKeyUsageOID  # noqa: F401 - для читателя: OID EKU ниже

    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=not ca,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if subject != issuer:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False
        )
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), critical=eku_critical)
    return builder.sign(signer, hashes.SHA256())


class Pki:
    """Корень → (промежуточный) → сертификат TSA с EKU timeStamping."""

    def __init__(self, *, eku=None, eku_critical=True, intermediate=False):
        from cryptography.x509.oid import ExtendedKeyUsageOID

        self.root_key, self.key = _ec_key(), _ec_key()
        self.root = _ca_cert("root", "root", self.root_key, self.root_key, ca=True)
        issuer, signer, self.extra = "root", self.root_key, []
        if intermediate:
            mid_key = _ec_key()
            mid = _ca_cert("mid", "root", mid_key, self.root_key, ca=True)
            self.extra = [mid.public_bytes(serialization.Encoding.DER)]
            issuer, signer = "mid", mid_key
        usage = [ExtendedKeyUsageOID.TIME_STAMPING] if eku is None else eku
        self.leaf = _ca_cert("tsa", issuer, self.key, signer, ca=False, eku=usage, eku_critical=eku_critical)


class FakeTsa:
    def __init__(
        self, status: str = "granted", nonce_shift: int = 0, imprint: bytes | None = None, pki: Pki | None = None
    ):
        self.status, self.nonce_shift, self.imprint, self.pki = status, nonce_shift, imprint, pki
        self.wrong_cert = False
        self.key = pki.key if pki else _ec_key()
        self.cert = pki.leaf if pki else _cert(self.key)
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
        embedded = asn1_x509.Certificate.load(
            _ca_cert("other", "other", (k := _ec_key()), k, ca=False).public_bytes(serialization.Encoding.DER)
            if self.wrong_cert
            else self.cert_der
        )
        signed = cms.SignedData(
            {
                "version": "v3",
                "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
                "encap_content_info": {"content_type": "tst_info", "content": core.ParsableOctetString(content)},
                "certificates": [
                    embedded,
                    *(asn1_x509.Certificate.load(d) for d in (self.pki.extra if self.pki else [])),
                ],
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
        anchor.TsaSink("https://tsa.example", _pin(fake), fake).publish({"segment": 1})


def _pin(fake) -> str:
    return hashlib.sha256(fake.cert_der).hexdigest()


def test_tsa_token_signature_and_garbage():
    tsa = FakeTsa()
    stmt = {"segment": 1}
    receipt = anchor.TsaSink("https://tsa.example", _pin(tsa), tsa).publish(stmt)
    token = bytearray(base64.b64decode(receipt["token"]))
    token[-5] ^= 0xFF  # порча подписи
    imprint = bytes.fromhex(anchor.digest(stmt))
    assert anchor.verify_tsa_token(bytes(token), imprint, None, _pin(tsa)) is not None
    assert "не разбирается" in anchor.verify_tsa_token(b"\x30\x00", imprint, None, _pin(tsa))
    # без доверенного корня и закрепления метка не принимается, даже целая
    good = base64.b64decode(receipt["token"])
    assert "ни доверенный корень" in anchor.verify_tsa_token(good, imprint, None, None)


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
    """Журнал Rekor в памяти: дерево Меркла RFC 6962 над телами записей и подписанный checkpoint."""

    ORIGIN = "rekor.example - 42"

    def __init__(self):
        self.log_key = _ec_key()
        self.bodies: list[bytes] = []
        self.entries: dict[str, int] = {}
        self.tamper: str | None = None

    @property
    def log_key_pem(self) -> bytes:
        return self.log_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def checkpoint(self, size: int, root: bytes, key=None, name="rekor.example") -> str:
        from mandat.audit import merkle  # noqa: F401

        body = f"{self.ORIGIN}\n{size}\n{base64.b64encode(root).decode()}\n"
        signer = key or self.log_key
        spki = signer.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        sig = signer.sign(body.encode(), ec.ECDSA(hashes.SHA256()))
        line = base64.b64encode(hashlib.sha256(spki).digest()[:4] + sig).decode()
        return body + f"\n\u2014 {name} {line}\n"

    def entry(self, uuid: str) -> dict:
        from mandat.audit import merkle

        index = self.entries[uuid]
        leaves = [merkle.leaf_hash(b) for b in self.bodies]
        root = merkle.root(leaves)
        proof = [h.hex() for h in merkle.inclusion_proof(index, leaves)]
        checkpoint = self.checkpoint(len(leaves), root)
        if self.tamper == "proof" and proof:
            proof[0] = "00" * 32
        if self.tamper == "root":
            root = b"\x01" * 32
        if self.tamper == "foreign-key":
            checkpoint = self.checkpoint(len(leaves), root, key=_ec_key())
        if self.tamper == "size":
            checkpoint = self.checkpoint(len(leaves) + 1, root)
        body = self.bodies[index]
        if self.tamper == "body":
            body = body.replace(b"sha256", b"sha257")
        return {
            "body": base64.b64encode(body).decode(),
            "logIndex": index,
            "verification": {
                "inclusionProof": {
                    "logIndex": index,
                    "treeSize": len(leaves),
                    "rootHash": root.hex(),
                    "hashes": proof,
                    "checkpoint": checkpoint,
                }
            },
        }

    def __call__(self, request):
        from mandat.audit import merkle

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
            self.bodies.append(request.data)
            uuid = "2" * 16 + merkle.leaf_hash(request.data).hex()
            self.entries[uuid] = len(self.bodies) - 1
            return Response(json.dumps({uuid: self.entry(uuid)}).encode(), status=201)
        uuid = request.full_url.rsplit("/", 1)[1]
        if uuid not in self.entries:
            raise OSError("404")
        return Response(json.dumps({uuid: self.entry(uuid)}).encode())


def _rekor(rekor: FakeRekor) -> anchor.RekorSink:
    return anchor.RekorSink("https://rekor.example", _ec_pem(), rekor, log_key_pem=rekor.log_key_pem)


def test_rekor_sink_roundtrip(tmp_path):
    rekor = FakeRekor()
    sink = _rekor(rekor)
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 1}
    receipt = sink.publish(stmt)
    for i in range(4):  # чужие записи после нашей: дерево растёт, доказательство меняется
        _rekor(rekor).publish({"segment": 100 + i})
    assert receipt["log_index"] == 0 and sink.check(stmt, receipt)
    assert not sink.check({"segment": 2}, receipt)
    assert not sink.check(stmt, {**receipt, "uuid": "missing"})
    assert not anchor.RekorSink("https://rekor.example", _ec_pem(), rekor).check(stmt, receipt)  # нет ключа журнала
    path = tmp_path / "j.jsonl"
    _fill(path, 3, [sink])
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok
    rekor.entries.clear()
    assert not journal.verify_journal(path, key=KEY, anchors=[sink]).ok


@pytest.mark.parametrize("tamper", ["proof", "root", "foreign-key", "size", "body"])
def test_rekor_rejects_bad_proof_or_checkpoint(tamper):
    rekor = FakeRekor()
    sink = _rekor(rekor)
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    for i in range(3):
        _rekor(rekor).publish({"segment": 10 + i})
    assert sink.check(stmt, receipt)
    rekor.tamper = tamper
    assert not sink.check(stmt, receipt)


def test_checkpoint_format_errors():
    rekor = FakeRekor()
    with pytest.raises(ValueError, match="signed note"):
        anchor.verify_checkpoint("one line", rekor.log_key_pem)
    text = rekor.checkpoint(1, b"\x00" * 32, name="other.example")
    with pytest.raises(ValueError, match="нет подписи"):
        anchor.verify_checkpoint(text, rekor.log_key_pem)
    from cryptography.hazmat.primitives.asymmetric import ed25519

    ed = ed25519.Ed25519PrivateKey.generate().public_key()
    pem = ed.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ValueError, match="ECDSA"):
        anchor.verify_checkpoint(rekor.checkpoint(1, b"\x00" * 32), pem)
    origin, size, root = anchor.verify_checkpoint(rekor.checkpoint(5, b"\x07" * 32), rekor.log_key_pem)
    assert (origin, size, root) == (FakeRekor.ORIGIN, 5, b"\x07" * 32)
    with pytest.raises(ValueError, match="uuid"):
        anchor.verify_rekor_entry("0" * 80, {"body": base64.b64encode(b"x").decode()}, rekor.log_key_pem)


# --- цепочка TSA до корня -------------------------------------------------------------------------


@pytest.mark.parametrize("intermediate", [False, True])
def test_tsa_chain_to_configured_root(tmp_path, intermediate):
    pki = Pki(intermediate=intermediate)
    tsa = FakeTsa(pki=pki)
    sink = anchor.TsaSink("https://tsa.example", None, tsa, roots=[pki.root])
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    stranger = anchor.TsaSink("https://tsa.example", None, tsa, roots=[Pki().root])
    assert not stranger.check(stmt, receipt)
    with pytest.raises(OSError, match="доверенного корня"):
        stranger.publish(stmt)


@pytest.mark.parametrize(
    ("eku", "critical", "message"),
    [("server", True, "только для меток"), ("ts", False, "только для меток"), ("none", True, "нет расширения")],
)
def test_tsa_chain_requires_timestamping_eku(eku, critical, message):
    from cryptography.x509.oid import ExtendedKeyUsageOID

    usage = {"server": [ExtendedKeyUsageOID.SERVER_AUTH], "ts": [ExtendedKeyUsageOID.TIME_STAMPING], "none": None}[eku]
    pki = Pki(eku=usage or [ExtendedKeyUsageOID.TIME_STAMPING], eku_critical=critical)
    if usage is None:
        pki.leaf = _ca_cert("tsa", "root", pki.key, pki.root_key, ca=False)
    problem = anchor.tsa_chain_problem(
        pki.leaf.public_bytes(serialization.Encoding.DER), [], [pki.root], datetime.now(UTC)
    )
    assert problem is not None and message in problem


def test_tsa_chain_checks_time():
    pki = Pki()
    leaf = pki.leaf.public_bytes(serialization.Encoding.DER)
    assert anchor.tsa_chain_problem(leaf, [], [pki.root], datetime.now(UTC)) is None
    assert anchor.tsa_chain_problem(leaf, [], [pki.root], datetime.now(UTC) + timedelta(days=90)) is not None


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
    journal._TAILS[journal._cache_key(path)] = (
        ("x", b"", journal._marker("other"), (None, None, None)),
        (3, "h"),
        0,
        (0, ""),
    )
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


# --- R4: манифест сегментов -------------------------------------------------------------------


def test_manifest_lists_sealed_segments(tmp_path):
    path = tmp_path / "j.jsonl"
    _fill(path, 7, [])
    rows = journal.read_manifest(path)
    assert [(r["segment"], r["first_seq"], r["last_seq"]) for r in rows] == [(1, 1, 3), (2, 4, 6)]
    seg = journal.sealed_segments(path)[0]
    assert rows[0]["sha256"] == hashlib.sha256(seg.read_bytes()).hexdigest()
    assert journal.manifest_problem(path) is None


def test_manifest_tamper_detected(tmp_path):
    path = tmp_path / "j.jsonl"
    _fill(path, 6, [])
    manifest = journal.manifest_path(path)
    manifest.write_text(manifest.read_text().replace('"first_seq": 4', '"first_seq": 5'))
    assert "не совпадает с манифестом" in journal.verify_journal(path, key=KEY).reason
    manifest.write_text("[1]\n")
    assert "не разбирается" in journal.verify_journal(path, key=KEY).reason
    manifest.write_text("{bad\n")
    assert "не разбирается" in journal.verify_journal(path, key=KEY).reason


def test_legacy_journal_without_manifest_is_repaired(tmp_path):
    path = tmp_path / "j.jsonl"
    _fill(path, 7, [])
    journal.manifest_path(path).unlink()
    assert "не совпадает с файлами" in journal.verify_journal(path, key=KEY).reason
    journal._TAILS.clear()
    _fill(path, 2, [], start=7)  # полная проверка восстанавливает манифест, закрытие продолжает нумерацию
    assert [r["segment"] for r in journal.read_manifest(path)] == [1, 2, 3]
    assert journal.verify_journal(path, key=KEY).ok
    assert journal.repair_manifest(path) == 0


def test_crash_between_seal_and_manifest(tmp_path):
    path = tmp_path / "j.jsonl"
    _fill(path, 6, [])
    manifest = journal.manifest_path(path)
    manifest.write_text(manifest.read_text().splitlines()[0] + "\n")  # строка про сегмент 2 не записана
    journal._TAILS.clear()
    _fill(path, 3, [], start=6)
    assert [r["segment"] for r in journal.read_manifest(path)] == [1, 2, 3]
    assert journal.verify_journal(path, key=KEY).ok


def test_catch_up_rejects_rewritten_manifest(tmp_path):
    path = tmp_path / "j.jsonl"
    _fill(path, 4, [])
    _other_process(path, 3, 4)  # чужой процесс закрывает сегмент 2
    manifest = journal.manifest_path(path)
    rows = manifest.read_text().splitlines()
    manifest.write_text(rows[0].replace('"segment": 1', '"segment": 1 ') + "\n" + rows[1] + "\n")
    assert journal._catch_up(path, KEY) is None


def test_manifest_tail_edge_cases(tmp_path):
    path = tmp_path / "j.jsonl"
    assert journal._manifest_tail(path) is None
    journal.manifest_path(path).write_text("\n")
    assert journal._manifest_tail(path) is None
    journal.manifest_path(path).write_text("[1]\n")
    assert journal._manifest_tail(path) is None
    assert journal._manifest_entry(_empty(tmp_path), 1)["first_seq"] is None


def _empty(tmp_path: Path) -> Path:
    target = tmp_path / "empty.jsonl.000001"
    target.write_text("")
    return target


def test_tsa_token_without_signer_certificate():
    tsa = FakeTsa()
    tsa.wrong_cert = True
    with pytest.raises(OSError, match="сертификата подписанта"):
        anchor.TsaSink("https://tsa.example", _pin(tsa), tsa).publish({"segment": 1})
