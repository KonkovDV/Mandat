"""R5: Rekor v2 (rekor-tiles), согласованность checkpoint (RFC 6962/9162), SET Rekor v1,
отзыв сертификатов TSA (OCSP → CRL), read-back вебхук-якоря. Всё на подставных серверах."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509 import ocsp
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, NameOID
from test_r3_anchor import KEY, FakeRekor, FakeTsa, Response, _ec_key, _ec_pem, _fill

from mandat.audit import anchor, journal, merkle, revocation, tlog
from mandat.services import webhooks


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "3")
    for name in (
        "MANDAT_JOURNAL_ANCHOR",
        "MANDAT_JOURNAL_ANCHOR_POLICY",
        "MANDAT_JOURNAL_ANCHOR_SECRET",
        "MANDAT_JOURNAL_ANCHOR_REKOR_STATE",
        "MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK",
        "MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION",
    ):
        monkeypatch.delenv(name, raising=False)
    journal._TAILS.clear()


def _leaves(n: int, tag: str = "x") -> list[bytes]:
    return [merkle.leaf_hash(f"{tag}{i}".encode()) for i in range(n)]


# --- RFC 6962 / 9162: доказательства согласованности ---------------------------------------------------


def test_consistency_proofs_all_small_trees():
    leaves = _leaves(70)
    h = tlog.leaves_hasher(leaves)
    for size2 in range(1, 70):
        root2 = merkle.root(leaves[:size2])
        for size1 in range(1, size2 + 1):
            proof = tlog.consistency_proof(size1, size2, h)
            root1 = merkle.root(leaves[:size1])
            assert tlog.verify_consistency(size1, size2, root1, root2, proof), (size1, size2)
            if size1 < size2:
                assert not tlog.verify_consistency(size1, size2, b"\x00" * 32, root2, proof)
                assert not tlog.verify_consistency(size1, size2, root1, b"\x00" * 32, proof)
                assert not tlog.verify_consistency(size1, size2, root1, root2, [*proof, b"\x00" * 32])
                if proof:
                    assert not tlog.verify_consistency(size1, size2, root1, root2, proof[:-1])
        for index in range(size2):
            assert tlog.inclusion_path(index, 0, size2, h) == merkle.inclusion_proof(index, leaves[:size2])


def test_consistency_edge_cases():
    leaves = _leaves(8)
    r3, r8 = merkle.root(leaves[:3]), merkle.root(leaves)
    assert tlog.verify_consistency(0, 8, b"", r8, [])
    assert not tlog.verify_consistency(0, 8, b"", r8, [r3])
    assert not tlog.verify_consistency(5, 3, r8, r3, [])
    assert not tlog.verify_consistency(-1, 3, r8, r3, [])
    assert tlog.verify_consistency(8, 8, r8, r8, []) and not tlog.verify_consistency(8, 8, r8, r3, [])
    assert not tlog.verify_consistency(3, 8, r3, r8, [])  # не степень двойки: пустое доказательство не годится
    with pytest.raises(ValueError):
        tlog.consistency_proof(0, 4, tlog.leaves_hasher(leaves))
    proof = tlog.consistency_proof(3, 8, tlog.leaves_hasher(leaves))
    assert not tlog.verify_consistency(3, 4, r3, r8, proof)  # sn не дошёл до нуля / лишние хеши


# --- тайлы (C2SP tlog-tiles) -------------------------------------------------------------------------


def _tiles(leaves: list[bytes]) -> dict[str, bytes]:
    store: dict[str, bytes] = {}
    level, hashes_ = 0, list(leaves)
    while hashes_:
        for t in range((len(hashes_) + 255) // 256):
            chunk = hashes_[t * 256 : (t + 1) * 256]
            store[tlog.tile_path(level, t, len(chunk))] = b"".join(chunk)
        hashes_ = [merkle.root(hashes_[i * 256 : (i + 1) * 256]) for i in range(len(hashes_) // 256)]
        level += 1
    return store


def test_tile_paths():
    assert tlog.tile_path(0, 0) == "tile/0/000"
    assert tlog.tile_path(0, 1234067) == "tile/0/x001/x234/067"
    assert tlog.tile_path(1, 5, 17) == "tile/1/005.p/17"


@pytest.mark.parametrize("size", [1, 255, 256, 257, 700, 65536 + 300])
def test_tile_reader_matches_tree(size):
    leaves = _leaves(size)
    store = _tiles(leaves)
    reader = tlog.TileReader(store.__getitem__, size)
    assert reader.hasher()(0, size) == merkle.root(leaves)
    for size1 in {1, min(255, size), min(256, size), size // 2 or 1, size}:
        proof = tlog.tile_prover(store.__getitem__)(size1, size)
        assert tlog.verify_consistency(size1, size, merkle.root(leaves[:size1]), merkle.root(leaves), proof)


def test_tile_reader_falls_back_to_full_tile():
    leaves = _leaves(512)
    full = _tiles(leaves)  # у дерева 512 оба тайла уровня 0 полные
    full[tlog.tile_path(1, 0, 1)] = full[tlog.tile_path(1, 0, 2)][:32]
    reader = tlog.TileReader(full.__getitem__, 300)  # тайлу «.p/44» на сервере уже нет
    assert reader.hasher()(0, 300) == merkle.root(leaves[:300])
    with pytest.raises(KeyError):
        tlog.TileReader({}.__getitem__, 256).node_at(0, 0)


def test_tile_reader_rejects_bad_tiles():
    leaves = _leaves(300)
    store = _tiles(leaves)
    short = {k: v[:-32] for k, v in store.items()}
    with pytest.raises(ValueError, match="короче"):
        tlog.TileReader(short.__getitem__, 300).node_at(0, 299)
    with pytest.raises(ValueError, match="за пределами"):
        tlog.TileReader(store.__getitem__, 300).node_at(0, 600)


# --- checkpoint: Ed25519, свидетели, формат ----------------------------------------------------------


def _pem_pub(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def ed_checkpoint(key, origin: str, size: int, root: bytes, extra: str = "", name: str | None = None) -> str:
    body = f"{origin}\n{size}\n{base64.b64encode(root).decode()}\n{extra}"
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    signer = name or origin
    key_id = hashlib.sha256(signer.encode() + b"\n\x01" + raw).digest()[:4]
    witness = base64.b64encode(b"\x01\x02\x03\x04" + b"\x00" * 72).decode()
    line = base64.b64encode(key_id + key.sign(body.encode())).decode()
    return body + f"\n\u2014 witness.example {witness}\n\u2014 {signer} {line}\n"


def test_ed25519_checkpoint_with_witnesses_and_extensions():
    key = ed25519.Ed25519PrivateKey.generate()
    text = ed_checkpoint(key, "log.example", 9, b"\x05" * 32, extra="ext line\n")
    assert tlog.verify_checkpoint(text, _pem_pub(key), origin="log.example") == ("log.example", 9, b"\x05" * 32)
    with pytest.raises(ValueError, match="другого журнала"):
        tlog.verify_checkpoint(text, _pem_pub(key), origin="other.example")
    with pytest.raises(ValueError, match="нет подписи"):
        tlog.verify_checkpoint(text, _pem_pub(ed25519.Ed25519PrivateKey.generate()))
    bad = text.replace("\n9\n", "\n09\n")
    with pytest.raises(ValueError, match="десятичное"):
        tlog.verify_checkpoint(bad, _pem_pub(key))
    short_root = f"log.example\n1\n{base64.b64encode(b'x').decode()}\n\n"
    with pytest.raises(ValueError, match="SHA-256"):
        tlog.verify_checkpoint(short_root, _pem_pub(key))
    forged = text.replace("\n9\n", "\n10\n")
    with pytest.raises(Exception):  # noqa: B017 - InvalidSignature из cryptography
        tlog.verify_checkpoint(forged, _pem_pub(key))
    rsa_pem = _pem_pub(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    with pytest.raises(ValueError, match="ECDSA или Ed25519"):
        tlog.verify_checkpoint(text, rsa_pem)


# --- состояние checkpoint: раздвоение и откат ---------------------------------------------------------


def test_checkpoint_state_persists_and_detects_forks(tmp_path):
    leaves = _leaves(20)
    prove = lambda a, b: tlog.consistency_proof(a, b, tlog.leaves_hasher(leaves))  # noqa: E731
    state = tlog.CheckpointState(tmp_path / "state" / "tlog.json")
    assert state.get("log") is None
    assert state.observe("log", 5, merkle.root(leaves[:5]), prove) is None
    assert state.observe("log", 12, merkle.root(leaves[:12]), prove) is None
    again = tlog.CheckpointState(tmp_path / "state" / "tlog.json")
    assert again.get("log") == (12, merkle.root(leaves[:12]))
    assert again.observe("log", 7, merkle.root(leaves[:7]), prove) is None  # старый checkpoint: proof 7 → 12
    assert again.get("log")[0] == 12
    assert "раздвоение" in again.observe("log", 12, b"\x00" * 32, prove)
    forked = [*leaves[:3], merkle.leaf_hash(b"rewritten"), *leaves[4:]]
    fork_prove = lambda a, b: tlog.consistency_proof(a, b, tlog.leaves_hasher(forked))  # noqa: E731
    assert "не согласован" in again.observe("log", 20, merkle.root(forked), fork_prove)
    assert again.get("log")[0] == 12  # несогласованный не принят

    def broken(a, b):
        raise OSError("down")

    assert "нет доказательства" in again.observe("log", 20, merkle.root(leaves), broken)
    (tmp_path / "state" / "tlog.json").write_text("[]")
    assert tlog.CheckpointState(tmp_path / "state" / "tlog.json").get("log") is None


# --- Rekor v1: мониторинг согласованности ------------------------------------------------------------


def test_rekor_v1_monitors_consistency(tmp_path):
    rekor = FakeRekor()
    state = tlog.CheckpointState(tmp_path / "rekor.json")
    sink = anchor.RekorSink("https://rekor.example", _ec_pem(), rekor, log_key_pem=rekor.log_key_pem, state=state)
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    for i in range(5):
        sink.publish({"segment": 10 + i})
    assert sink.check(stmt, receipt)
    assert state.get(FakeRekor.ORIGIN)[0] == 6
    assert rekor.proof_queries and rekor.proof_queries[-1]["treeID"] == ["42"]
    assert rekor.live_reads == 2


def test_rekor_v1_detects_rewritten_log(tmp_path, caplog):
    rekor = FakeRekor()
    sink = anchor.RekorSink(
        "https://rekor.example", _ec_pem(), rekor, log_key_pem=rekor.log_key_pem, state=tlog.CheckpointState()
    )
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    for i in range(3):
        sink.publish({"segment": 10 + i})
    assert sink.check(stmt, receipt)
    # Журнал переписал чужую запись и растёт дальше: наша запись и её доказательство целы, но история другая.
    rekor.bodies[2] = b'{"rewritten": true}'
    sink.publish({"segment": 99})
    with caplog.at_level(logging.ERROR, logger="mandat.anchor"):
        assert not sink.check(stmt, receipt)
    assert "не согласован" in caplog.text


def test_rekor_v1_live_checkpoint_unavailable():
    rekor = FakeRekor()
    sink = anchor.RekorSink(
        "https://rekor.example", _ec_pem(), rekor, log_key_pem=rekor.log_key_pem, state=tlog.CheckpointState()
    )
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)

    def flaky(request):
        if request.full_url.endswith("/api/v1/log"):
            raise OSError("down")
        return rekor(request)

    sink._open = flaky
    assert "не получен" in sink.consistency_problem((FakeRekor.ORIGIN, 1, merkle.root(_leaves(0)[:0] or [b"x" * 32])))
    assert not sink.check(stmt, receipt)


# --- Rekor v2 (rekor-tiles) --------------------------------------------------------------------------


class FakeRekorV2:
    """rekor-tiles в памяти: POST /api/v2/log/entries, /checkpoint и /tile/... по C2SP tlog-tiles."""

    ORIGIN = "rekor2.example"

    def __init__(self):
        self.log_key = ed25519.Ed25519PrivateKey.generate()
        self.bodies: list[bytes] = []
        self.tamper: str | None = None
        self.posts: list[dict] = []

    @property
    def log_key_pem(self) -> bytes:
        return _pem_pub(self.log_key)

    def leaves(self) -> list[bytes]:
        return [merkle.leaf_hash(b) for b in self.bodies]

    def checkpoint(self) -> str:
        leaves = self.leaves()
        return ed_checkpoint(self.log_key, self.ORIGIN, len(leaves), merkle.root(leaves))

    def add(self, body: bytes) -> int:
        self.bodies.append(body)
        return len(self.bodies) - 1

    def tle(self, index: int) -> dict:
        leaves = self.leaves()
        proof = merkle.inclusion_proof(index, leaves)
        body = self.bodies[index]
        kind = {"kind": "hashedrekord", "version": "0.0.2"}
        if self.tamper == "kind":
            kind = {"kind": "dsse", "version": "0.0.2"}
        if self.tamper == "proof" and proof:
            proof[0] = b"\x00" * 32
        checkpoint = self.checkpoint()
        if self.tamper == "origin":
            checkpoint = ed_checkpoint(self.log_key, "evil.example", len(leaves), merkle.root(leaves))
        if self.tamper == "body":
            body = body.replace(b"SHA2_256", b"SHA2_512")
        return {
            "logIndex": str(index),
            "logId": {"keyId": "ignored"},
            "kindVersion": kind,
            "integratedTime": "0",
            "inclusionPromise": None,
            "inclusionProof": {
                "logIndex": str(index),
                "rootHash": "ignored",
                "treeSize": "ignored",
                "hashes": [base64.b64encode(h).decode() for h in proof],
                "checkpoint": {"envelope": checkpoint},
            },
            "canonicalizedBody": base64.b64encode(body).decode(),
        }

    def __call__(self, request):
        from cryptography.hazmat.primitives.asymmetric import utils

        url = request.full_url
        if request.get_method() == "POST":
            assert url.endswith("/api/v2/log/entries")
            req = json.loads(request.data)["hashedRekordRequestV002"]
            self.posts.append(req)
            verifier = req["signature"]["verifier"]
            assert verifier["keyDetails"] == "PKIX_ECDSA_P256_SHA_256"
            public = serialization.load_der_public_key(base64.b64decode(verifier["publicKey"]["rawBytes"]))
            public.verify(
                base64.b64decode(req["signature"]["content"]),
                base64.b64decode(req["digest"]),
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
            entry = {
                "apiVersion": "0.0.2",
                "kind": "hashedrekord",
                "spec": {
                    "hashedRekordV002": {
                        "data": {"algorithm": "SHA2_256", "digest": req["digest"]},
                        "signature": req["signature"],
                    }
                },
            }
            index = self.add(anchor.canonical(entry))
            return Response(json.dumps(self.tle(index)).encode(), status=201)
        path = url.split("rekor2.example/", 1)[1]
        if path == "checkpoint":
            if self.tamper == "live-down":
                raise OSError("down")
            return Response(self.checkpoint().encode())
        store = _tiles(self.leaves())
        if path in store:
            return Response(store[path])
        # Частичный тайл прежнего размера дерева: журнал хранит его (tlog-tiles), хеши — префикс нынешнего.
        base, _, width = path.partition(".p/")
        for key, data in store.items():
            if key.split(".p/")[0] == base and width and len(data) >= 32 * int(width):
                return Response(data[: 32 * int(width)])
        raise OSError(f"404 {path}")


def _v2(fake: FakeRekorV2, state=None, log_key=True) -> anchor.RekorV2Sink:
    return anchor.RekorV2Sink(
        "https://rekor2.example", _ec_pem(), fake, log_key_pem=fake.log_key_pem if log_key else None, state=state
    )


def test_rekor_v2_roundtrip_and_journal(tmp_path):
    fake = FakeRekorV2()
    sink = _v2(fake)
    assert sink.name == "rekor2:https://rekor2.example" and sink.origin == "rekor2.example"
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 1}
    receipt = sink.publish(stmt)
    for i in range(4):
        sink.publish({"segment": 10 + i})
    assert receipt["log_index"] == 0 and sink.check(stmt, receipt)  # офлайн: квитанция — весь TLE
    assert not sink.check({"segment": 2}, receipt)
    assert not sink.check(stmt, {**receipt, "tle": {}})
    assert not _v2(fake, log_key=False).check(stmt, receipt)
    assert _v2(fake, log_key=False).publish({"segment": 5})["tle"]  # без ключа журнала — без проверки при записи
    assert base64.b64decode(fake.posts[0]["digest"]).hex() == anchor.digest(stmt)
    path = tmp_path / "j.jsonl"
    _fill(path, 6, [sink])
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok


@pytest.mark.parametrize("tamper", ["kind", "proof", "origin", "body"])
def test_rekor_v2_rejects_tampered_entries(tamper):
    fake = FakeRekorV2()
    sink = _v2(fake)
    for i in range(3):
        sink.publish({"segment": 20 + i})
    fake.tamper = tamper
    with pytest.raises(OSError, match="не проверяется"):
        sink.publish({"segment": 1})


def test_rekor_v2_foreign_log_key():
    fake = FakeRekorV2()
    receipt = _v2(fake).publish({"segment": 1})
    other = anchor.RekorV2Sink(
        "https://rekor2.example", _ec_pem(), fake, log_key_pem=_pem_pub(ed25519.Ed25519PrivateKey.generate())
    )
    assert not other.check({"segment": 1}, receipt)


def test_rekor_v2_body_kind_must_match():
    fake = FakeRekorV2()
    receipt = _v2(fake).publish({"segment": 1})
    tle = json.loads(json.dumps(receipt["tle"]))
    tle["canonicalizedBody"] = base64.b64encode(b'{"kind":"dsse","apiVersion":"0.0.2"}').decode()
    with pytest.raises(ValueError, match="другого вида"):
        anchor.verify_rekor_v2_entry(tle, fake.log_key_pem, None)


def test_rekor_v2_consistency_over_tiles(tmp_path):
    fake = FakeRekorV2()
    state = tlog.CheckpointState(tmp_path / "v2.json")
    sink = _v2(fake, state=state)
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    for i in range(300):  # > 256 записей: нужны тайлы уровня 1 и частичные тайлы
        fake.add(f"other {i}".encode())
    assert sink.check(stmt, receipt)
    assert state.get("rekor2.example")[0] == 301
    for i in range(40):
        fake.add(f"more {i}".encode())
    assert sink.check(stmt, receipt) and state.get("rekor2.example")[0] == 341
    fake.bodies[100] = b"rewritten"  # история переписана: тайлы другие, корень другой
    fake.add(b"next")
    assert not sink.check(stmt, receipt)
    assert state.get("rekor2.example")[0] == 341


def test_rekor_v2_live_checkpoint_down():
    fake = FakeRekorV2()
    sink = _v2(fake, state=tlog.CheckpointState())
    receipt = sink.publish({"segment": 1})
    fake.tamper = "live-down"
    assert not sink.check({"segment": 1}, receipt)


def test_configured_rekor2_and_state(tmp_path, monkeypatch):
    key = tmp_path / "k.pem"
    key.write_bytes(_ec_pem())
    log_key = tmp_path / "log.pem"
    log_key.write_bytes(FakeRekorV2().log_key_pem)
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_KEY", str(key))
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_PUBKEY", str(log_key))
    (sink,) = anchor.configured_sinks("rekor2:https://log2025-1.rekor.sigstore.dev")
    assert isinstance(sink, anchor.RekorV2Sink) and sink.state is None
    assert sink.origin == "log2025-1.rekor.sigstore.dev"
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_STATE", str(tmp_path / "s.json"))
    (v1,) = anchor.configured_sinks("rekor:https://rekor.example")
    assert isinstance(v1, anchor.RekorSink) and v1.state is not None and v1.state.path == tmp_path / "s.json"


# --- отзыв сертификатов TSA ---------------------------------------------------------------------------

OCSP_URL = "http://ocsp.example/"
CRL_URL = "http://crl.example/ca.crl"


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _issue(subject, subject_key, issuer, issuer_key, *, ca, eku=None, urls=True, ocsp_signing=False):
    now = datetime.now(UTC)
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(subject_key.public_key())
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
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(subject_key.public_key()), critical=False)
    )
    if subject != issuer:
        b = b.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage(eku), critical=True)
    if urls and subject != issuer:
        b = b.add_extension(
            x509.AuthorityInformationAccess(
                [x509.AccessDescription(AuthorityInformationAccessOID.OCSP, x509.UniformResourceIdentifier(OCSP_URL))]
            ),
            critical=False,
        ).add_extension(
            x509.CRLDistributionPoints(
                [x509.DistributionPoint([x509.UniformResourceIdentifier(CRL_URL)], None, None, None)]
            ),
            critical=False,
        )
    return b.sign(issuer_key, hashes.SHA256())


class RevPki:
    """Корень → промежуточный CA → TSA; у TSA и CA адреса OCSP и CRL."""

    def __init__(self, urls=True):
        self.root_key, self.mid_key, self.key = _ec_key(), _ec_key(), _ec_key()
        self.root = _issue("root", self.root_key, "root", self.root_key, ca=True)
        self.mid = _issue("mid", self.mid_key, "root", self.root_key, ca=True, urls=urls)
        self.leaf = _issue(
            "tsa", self.key, "mid", self.mid_key, ca=False, eku=[ExtendedKeyUsageOID.TIME_STAMPING], urls=urls
        )
        self.extra = [self.mid.public_bytes(serialization.Encoding.DER)]
        self.issuers = {"mid": (self.mid, self.mid_key), "root": (self.root, self.root_key)}


class Responder:
    """OCSP и CRL в одном подставном сервере. ``revoked``: серийный номер → (время, причина)."""

    def __init__(self, pki: RevPki):
        self.pki = pki
        self.revoked: dict[int, tuple[datetime, x509.ReasonFlags | None]] = {}
        self.ocsp_mode = "ok"  # ok | down | unknown | stale | bad-nonce | delegated | delegated-no-eku | error
        self.crl_mode = "ok"  # ok | down | pem | wrong-issuer | stale
        self.calls: list[str] = []

    def _issuer_of(self, cert):
        return self.pki.issuers[cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value]

    def _cert_by_serial(self, serial):
        return next(c for c in (self.pki.leaf, self.pki.mid) if c.serial_number == serial)

    def ocsp(self, data: bytes) -> bytes:
        req = ocsp.load_der_ocsp_request(data)
        if self.ocsp_mode == "error":
            return ocsp.OCSPResponseBuilder.build_unsuccessful(ocsp.OCSPResponseStatus.TRY_LATER).public_bytes(
                serialization.Encoding.DER
            )
        cert = self._cert_by_serial(req.serial_number)
        issuer, issuer_key = self._issuer_of(cert)
        now = datetime.now(UTC)
        this, nxt = now - timedelta(minutes=1), now + timedelta(hours=1)
        if self.ocsp_mode == "stale":
            this, nxt = now - timedelta(days=3), now - timedelta(days=2)
        if self.ocsp_mode == "unknown":
            status, rev_time, reason = ocsp.OCSPCertStatus.UNKNOWN, None, None
        elif cert.serial_number in self.revoked:
            rev_time, reason = self.revoked[cert.serial_number]
            status = ocsp.OCSPCertStatus.REVOKED
        else:
            status, rev_time, reason = ocsp.OCSPCertStatus.GOOD, None, None
        builder = ocsp.OCSPResponseBuilder().add_response(
            cert=cert,
            issuer=issuer,
            algorithm=hashes.SHA1(),  # noqa: S303
            cert_status=status,
            this_update=this,
            next_update=nxt,
            revocation_time=rev_time,
            revocation_reason=reason,
        )
        nonce = req.extensions.get_extension_for_class(x509.OCSPNonce).value.nonce
        builder = builder.add_extension(
            x509.OCSPNonce(b"x" * 16 if self.ocsp_mode == "bad-nonce" else nonce), critical=False
        )
        signer_cert, signer_key = issuer, issuer_key
        if self.ocsp_mode.startswith("delegated"):
            signer_key = _ec_key()
            eku = (
                [ExtendedKeyUsageOID.OCSP_SIGNING]
                if self.ocsp_mode == "delegated"
                else [ExtendedKeyUsageOID.CODE_SIGNING]
            )
            iss_cn = issuer.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            signer_cert = _issue("responder", signer_key, iss_cn, issuer_key, ca=False, eku=eku, urls=False)
            builder = builder.certificates([signer_cert])
        builder = builder.responder_id(ocsp.OCSPResponderEncoding.HASH, signer_cert)
        return builder.sign(signer_key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)

    def crl(self, issuer_cn: str = "mid") -> bytes:
        issuer, key = self.pki.issuers[issuer_cn]
        if self.crl_mode == "wrong-issuer":
            key = _ec_key()
        now = datetime.now(UTC)
        last, nxt = now - timedelta(minutes=1), now + timedelta(hours=1)
        if self.crl_mode == "stale":
            last, nxt = now - timedelta(days=3), now - timedelta(days=2)
        b = x509.CertificateRevocationListBuilder().issuer_name(issuer.subject).last_update(last).next_update(nxt)
        for serial, (when, reason) in self.revoked.items():
            rb = x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(when)
            if reason is not None:
                rb = rb.add_extension(x509.CRLReason(reason), critical=False)
            b = b.add_revoked_certificate(rb.build())
        crl = b.sign(key, hashes.SHA256())
        return crl.public_bytes(serialization.Encoding.PEM if self.crl_mode == "pem" else serialization.Encoding.DER)

    def __call__(self, request, timeout):
        assert 0 < timeout <= 60
        url = request.full_url
        self.calls.append(url)
        if url == OCSP_URL:
            assert request.get_header("Content-type") == "application/ocsp-request"
            if self.ocsp_mode == "down":
                raise OSError("ocsp down")
            return Response(self.ocsp(request.data))
        if url == CRL_URL:
            if self.crl_mode == "down":
                raise OSError("crl down")
            if self.crl_mode == "http-500":
                return Response(b"", status=500)
            # CRL у листа выпускает mid; у mid — root. Один адрес на всё: отдаём по запрошенному последним.
            return Response(self.crl(self.next_crl_issuer))
        raise OSError("404")

    next_crl_issuer = "mid"


def _checker(responder, mode="soft", **kw) -> revocation.RevocationChecker:
    return revocation.RevocationChecker(mode, opener=responder, **kw)


def _status(pki: RevPki, responder: Responder, **kw) -> revocation.Status:
    return _checker(responder, **kw).status(pki.leaf, pki.mid)


def test_ocsp_good_revoked_and_cache():
    pki = RevPki()
    responder = Responder(pki)
    checker = _checker(responder)
    assert checker.status(pki.leaf, pki.mid) == revocation.Status("good", "ocsp")
    assert checker.status(pki.leaf, pki.mid).state == "good" and checker.fetches == 1  # из кеша
    responder.revoked[pki.leaf.serial_number] = (datetime.now(UTC) - timedelta(hours=1), x509.ReasonFlags.superseded)
    assert _status(pki, responder).state == "revoked"
    assert _status(pki, responder).reason == "superseded"


def test_cache_expires_with_clock():
    pki = RevPki()
    responder = Responder(pki)
    now = [datetime.now(UTC)]
    checker = _checker(responder, cache_seconds=60, clock=lambda: now[0])
    checker.status(pki.leaf, pki.mid)
    now[0] += timedelta(seconds=61)
    checker.status(pki.leaf, pki.mid)
    assert checker.fetches == 2


@pytest.mark.parametrize(
    ("ocsp_mode", "source"),
    [
        ("down", "crl"),
        ("unknown", "crl"),
        ("stale", "crl"),
        ("bad-nonce", "crl"),
        ("error", "crl"),
        ("delegated", "ocsp"),
        ("delegated-no-eku", "crl"),
    ],
)
def test_ocsp_failures_fall_back_to_crl(ocsp_mode, source):
    pki = RevPki()
    responder = Responder(pki)
    responder.ocsp_mode = ocsp_mode
    result = _status(pki, responder)
    assert (result.state, result.source) == ("good", source)


@pytest.mark.parametrize("crl_mode", ["ok", "pem"])
def test_crl_revocation(crl_mode):
    pki = RevPki()
    responder = Responder(pki)
    responder.ocsp_mode, responder.crl_mode = "down", crl_mode
    responder.revoked[pki.leaf.serial_number] = (datetime.now(UTC) - timedelta(days=1), x509.ReasonFlags.key_compromise)
    result = _status(pki, responder)
    assert (result.state, result.source, result.reason) == ("revoked", "crl", "key_compromise")
    responder.revoked[pki.leaf.serial_number] = (datetime.now(UTC) - timedelta(days=1), None)
    assert _status(pki, responder).reason is None


@pytest.mark.parametrize("crl_mode", ["down", "wrong-issuer", "stale", "http-500"])
def test_status_unknown_when_nothing_answers(crl_mode):
    pki = RevPki()
    responder = Responder(pki)
    responder.ocsp_mode, responder.crl_mode = "down", crl_mode
    result = _status(pki, responder)
    assert result.state == "unknown" and "ocsp" in result.source and "crl" in result.source


def test_no_urls_unknown():
    pki = RevPki(urls=False)
    assert _checker(Responder(pki)).status(pki.leaf, pki.mid).source == "нет адресов OCSP и CRL"


def test_rfc3161_revocation_semantics():
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    rev = revocation.Status
    assert not revocation.affects(rev("good", "ocsp"), issued)
    assert revocation.affects(rev("revoked", "ocsp", issued + timedelta(days=9), "key_compromise"), issued)
    assert revocation.affects(rev("revoked", "ocsp", issued + timedelta(days=9), None), issued)
    assert not revocation.affects(rev("revoked", "ocsp", issued + timedelta(days=9), "superseded"), issued)
    assert revocation.affects(rev("revoked", "ocsp", issued - timedelta(days=1), "superseded"), issued)
    assert revocation.affects(rev("revoked", "ocsp", None, "cessation_of_operation"), issued)


def test_policy_soft_hard_off(caplog):
    pki = RevPki()
    responder = Responder(pki)
    responder.ocsp_mode = responder.crl_mode = "down"
    chain = [pki.leaf, pki.mid, pki.root]
    now = datetime.now(UTC)
    with caplog.at_level(logging.WARNING, logger="mandat.anchor.revocation"):
        assert _checker(responder, "soft").problem(chain, now) is None
    assert "неизвестен" in caplog.text
    assert "неизвестен" in _checker(responder, "hard").problem(chain, now)
    assert _checker(responder, "off").problem(chain, now) is None
    responder.ocsp_mode = "ok"
    responder.revoked[pki.leaf.serial_number] = (now, x509.ReasonFlags.key_compromise)
    assert "отозван" in _checker(responder, "soft").problem(chain, now - timedelta(days=1))
    with pytest.raises(ValueError, match="политика"):
        revocation.RevocationChecker("maybe")


def test_intermediate_revocation_checked():
    pki = RevPki()
    responder = Responder(pki)
    responder.revoked[pki.mid.serial_number] = (datetime.now(UTC), x509.ReasonFlags.ca_compromise)
    problem = _checker(responder).problem([pki.leaf, pki.mid, pki.root], datetime.now(UTC))
    assert problem and "CN=mid" in problem


def test_response_size_limit():
    class Huge(Response):
        def read(self, n=-1):
            return b"x" * (revocation.MAX_RESPONSE + 1)

    checker = revocation.RevocationChecker(opener=lambda r, t: Huge())
    with pytest.raises(OSError, match="слишком"):
        checker._fetch(CRL_URL)


def test_from_env(monkeypatch):
    assert revocation.from_env({}).mode == "soft"
    c = revocation.from_env(
        {
            "MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION": "HARD",
            "MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION_TIMEOUT": "2.5",
            "MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION_CACHE": "10",
        }
    )
    assert (c.mode, c.timeout, c.cache_seconds) == ("hard", 2.5, 10)
    with pytest.raises(ValueError, match="таймаут"):
        revocation.from_env({"MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION_TIMEOUT": "0"})
    with pytest.raises(ValueError, match="политика"):
        revocation.from_env({"MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION": "maybe"})
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION", "off")
    assert revocation.from_env().mode == "off"


def test_tsa_sink_with_revocation_end_to_end(tmp_path, monkeypatch):
    pki = RevPki()
    pki.root_key_unused = None
    tsa = FakeTsa(pki=pki)  # FakeTsa кладёт pki.extra (промежуточный CA) в метку
    responder = Responder(pki)
    sink = anchor.TsaSink("https://tsa.example", None, tsa, roots=[pki.root], revocation=_checker(responder, "hard"))
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    assert OCSP_URL in responder.calls
    responder.revoked[pki.leaf.serial_number] = (datetime.now(UTC), x509.ReasonFlags.key_compromise)
    revoked = anchor.TsaSink("https://tsa.example", None, tsa, roots=[pki.root], revocation=_checker(responder))
    assert not revoked.check(stmt, receipt)
    with pytest.raises(OSError, match="отозван"):
        revoked.publish(stmt)
    roots = tmp_path / "roots.pem"
    roots.write_bytes(pki.root.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_TSA_ROOTS", str(roots))
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_TSA_REVOCATION", "hard")
    (configured,) = anchor.configured_sinks("tsa:https://tsa.example")
    assert configured.revocation is not None and configured.revocation.mode == "hard"


# --- read-back вебхук-якоря ----------------------------------------------------------------------------


class Receiver:
    """Получатель вебхук-якоря с контрактом read-back: хранит утверждения и отдаёт их по хешу."""

    def __init__(self, secret: str):
        self.secret = secret
        self.stored: dict[str, dict] = {}
        self.mode = "ok"  # ok | unsigned | wrong-id | other-statement | other-delivery | garbage | status | stale
        self.last_answer: tuple[dict, bytes] | None = None

    def __call__(self, request):
        headers = {k.lower(): v for k, v in request.header_items()}
        if request.get_method() == "POST":
            assert webhooks.signature_ok(self.secret, headers, request.data, datetime.now(UTC))
            stmt = json.loads(request.data)
            self.stored[anchor.digest(stmt)] = {"statement": stmt, "delivery_id": headers["webhook-id"]}
            return Response(status=202)
        assert webhooks.signature_ok(self.secret, headers, b"", datetime.now(UTC))  # запрос подписан нами
        want = request.full_url.rsplit("/", 1)[1]
        if self.mode == "replay" and self.last_answer:
            return _HeaderResponse(*self.last_answer)
        if want not in self.stored:
            raise OSError("404")
        row = self.stored[want]
        stmt = row["statement"] if self.mode != "other-statement" else {**row["statement"], "segment": -1}
        payload = {"sha256": want, "statement": stmt, "delivery_id": row["delivery_id"]}
        if self.mode == "other-delivery":
            payload["delivery_id"] = "anchor-0000"
        body = b"{not json" if self.mode == "garbage" else anchor.canonical(payload)
        rid = headers["webhook-id"] if self.mode != "wrong-id" else "readback-0000"
        when = datetime.now(UTC) - (timedelta(hours=1) if self.mode == "stale" else timedelta())
        signed = webhooks.sign_headers(self.secret, rid, body, when)
        if self.mode == "unsigned":
            signed = {}
        self.last_answer = (signed, body)
        return _HeaderResponse(signed, body, status=203 if self.mode == "status" else 200)


class _HeaderResponse(Response):
    def __init__(self, headers: dict, body: bytes, status: int = 200):
        super().__init__(body, status)
        self.headers = headers


def _webhook(receiver: Receiver) -> anchor.WebhookSink:
    return anchor.WebhookSink(
        "https://h.example/anchor", receiver.secret, receiver, readback="https://h.example/anchor/{sha256}"
    )


def test_webhook_readback_roundtrip(tmp_path):
    receiver = Receiver(webhooks.new_secret())
    sink = _webhook(receiver)
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    assert not sink.check({"segment": 9}, receipt)
    path = tmp_path / "j.jsonl"
    _fill(path, 6, [sink])
    assert journal.verify_journal(path, key=KEY, anchors=[sink]).ok
    receiver.stored.clear()  # получатель потерял или удалил запись — локальной квитанции мало
    assert not journal.verify_journal(path, key=KEY, anchors=[sink]).ok


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("unsigned", "подпись"),
        ("wrong-id", "подпись"),
        ("stale", "подпись"),
        ("other-statement", "другое утверждение"),
        ("other-delivery", "другую доставку"),
        ("garbage", "не разбирается"),
        ("status", "ответил 203"),
    ],
)
def test_webhook_readback_rejects(mode, message):
    receiver = Receiver(webhooks.new_secret())
    sink = _webhook(receiver)
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    receiver.mode = mode
    assert message in sink.remote_problem(stmt, receipt)
    assert not sink.check(stmt, receipt)


def test_webhook_readback_replay_and_missing():
    receiver = Receiver(webhooks.new_secret())
    sink = _webhook(receiver)
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    receiver.mode = "replay"  # старый подписанный ответ к новому запросу не подходит
    assert "подпись" in sink.remote_problem(stmt, receipt)
    receiver.mode = "ok"
    assert "недоступен" in sink.remote_problem({"segment": 2}, {**receipt, "sha256": anchor.digest({"segment": 2})})


def test_webhook_readback_config(monkeypatch):
    with pytest.raises(ValueError, match="sha256"):
        anchor.WebhookSink("https://h.example", "s", readback="https://h.example/anchor")
    with pytest.raises(ValueError, match="https"):
        anchor.WebhookSink("https://h.example", "s", readback="http://h.example/{sha256}")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_SECRET", webhooks.new_secret())
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK", "https://h.example/a/{sha256}")
    (sink,) = anchor.configured_sinks("webhook:https://h.example/a")
    assert sink.readback == "https://h.example/a/{sha256}"


def test_response_signature_helper():
    secret = webhooks.new_secret()
    now = datetime.now(UTC)
    headers = webhooks.sign_headers(secret, "readback-1", b"{}", now)
    assert webhooks.response_signature_ok(secret, headers, b"{}", now, "readback-1")
    assert not webhooks.response_signature_ok(secret, headers, b"{}", now, "readback-2")
    assert not webhooks.response_signature_ok(secret, headers, b"{ }", now, "readback-1")
    assert not webhooks.response_signature_ok(secret, {**headers, "webhook-timestamp": "x"}, b"{}", now, "readback-1")
