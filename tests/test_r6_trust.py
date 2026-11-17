"""R6: косигнатуры свидетелей (C2SP tlog-cosignature) и TrustedRoot/SigningConfig Sigstore через TUF."""

from __future__ import annotations

import base64
import functools
import hashlib
import http.server
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from test_r3_anchor import _ec_key, _ec_pem
from test_r5_tlog import FakeRekorV2, _pem_pub

from mandat.audit import anchor, tlog, trust


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "MANDAT_JOURNAL_ANCHOR_WITNESSES",
        "MANDAT_JOURNAL_ANCHOR_WITNESS_QUORUM",
        "MANDAT_JOURNAL_ANCHOR_REKOR_STATE",
        "MANDAT_SIGSTORE_REKOR_URL",
        "MANDAT_SIGSTORE_OFFLINE",
        "MANDAT_SIGSTORE_TUF_CACHE",
        "MANDAT_SIGSTORE_TRUSTED_ROOT",
        "MANDAT_SIGSTORE_SIGNING_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)


# --- свидетели -------------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class WitnessKey:
    def __init__(self, name: str):
        self.name, self.key = name, ed25519.Ed25519PrivateKey.generate()
        self.raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key_id = tlog.witness_key_id(name, self.raw)

    @property
    def vkey(self) -> str:
        return self.name + "+" + self.key_id.hex() + "+" + _b64(b"\x04" + self.raw)

    def cosign(self, text: str, stamp: int | None = None, *, key_id: bytes | None = None, key=None) -> str:
        body = text.partition("\n\n")[0]
        when = int(time.time()) if stamp is None else stamp
        sig = (key or self.key).sign(f"cosignature/v1\ntime {when}\n{body}\n".encode())
        raw = (key_id or self.key_id) + when.to_bytes(8, "big") + sig
        return text + f"\u2014 {self.name} {base64.b64encode(raw).decode()}\n"


def _log_checkpoint():
    key = ed25519.Ed25519PrivateKey.generate()
    from test_r5_tlog import ed_checkpoint

    return key, ed_checkpoint(key, "log.example", 7, b"\x07" * 32)


def test_parse_vkey():
    w = WitnessKey("witness.example/a")
    parsed = tlog.parse_vkey(w.vkey)
    assert (parsed.name, parsed.key, parsed.key_id) == (w.name, w.raw, w.key_id)
    real = "witness.navigli.sunlight.geomys.org+a3e00fe2+BNy/co4C1Hn1p+INwJrfUlgz7W55dSZReusH/GhUhJ/G"
    assert tlog.parse_vkey(real).key_id.hex() == "a3e00fe2"
    with pytest.raises(ValueError, match="формате"):
        tlog.parse_vkey("no-plus")
    with pytest.raises(ValueError, match="0x04"):
        tlog.parse_vkey(w.name + "+" + w.key_id.hex() + "+" + _b64(b"\x01" + w.raw))
    with pytest.raises(ValueError, match="id ключа"):
        tlog.parse_vkey(w.name + "+00000000+" + _b64(b"\x04" + w.raw))


def test_quorum_of_cosignatures():
    log_key, text = _log_checkpoint()
    a, b, c = WitnessKey("a.example"), WitnessKey("b.example"), WitnessKey("c.example")
    policy = tlog.WitnessPolicy(tuple(tlog.parse_vkey(w.vkey) for w in (a, b, c)), 2)
    pem = _pem_pub(log_key)
    one = a.cosign(text)
    with pytest.raises(ValueError, match="1 из 2"):
        tlog.verify_checkpoint(one, pem, witnesses=policy)
    two = b.cosign(a.cosign(a.cosign(text)))  # повтор одного свидетеля не считается дважды
    assert tlog.verify_checkpoint(two, pem, witnesses=policy)[1] == 7
    stranger = WitnessKey("a.example")  # то же имя, другой ключ
    assert policy.cosigned_by(*_split(stranger.cosign(text))) == set()


def _split(text: str):
    body, _, sigs = text.partition("\n\n")
    return body, sigs


@pytest.mark.parametrize("damage", ["future", "key-id", "signature", "short", "base64", "other-body"])
def test_bad_cosignatures_do_not_count(damage):
    _, text = _log_checkpoint()
    w = WitnessKey("w.example")
    policy = tlog.WitnessPolicy((tlog.parse_vkey(w.vkey),), 1)
    if damage == "future":
        signed = w.cosign(text, int(time.time()) + 3600)
    elif damage == "key-id":
        signed = w.cosign(text, key_id=b"\x00" * 4)
    elif damage == "signature":
        signed = w.cosign(text, key=ed25519.Ed25519PrivateKey.generate())
    elif damage == "short":
        signed = text + f"\u2014 w.example {base64.b64encode(w.key_id + b'x').decode()}\n"
    elif damage == "base64":
        signed = text + "\u2014 w.example !!!\n"
    else:
        signed = w.cosign(text.replace("\n7\n", "\n8\n"))
        signed = text + signed.rsplit("\n", 2)[-2] + "\n"
    assert policy.problem(signed) is not None
    assert policy.problem(w.cosign(text)) is None


def test_witness_policy_validation_and_env():
    w = WitnessKey("w.example")
    with pytest.raises(ValueError, match="кворум"):
        tlog.WitnessPolicy((tlog.parse_vkey(w.vkey),), 2)
    assert tlog.witness_policy_from_env({}) is None
    v = WitnessKey("v.example")
    policy = tlog.witness_policy_from_env(
        {"MANDAT_JOURNAL_ANCHOR_WITNESSES": f"{w.vkey}\n{v.vkey}", "MANDAT_JOURNAL_ANCHOR_WITNESS_QUORUM": "2"}
    )
    assert policy and policy.quorum == 2 and [x.name for x in policy.witnesses] == ["w.example", "v.example"]


class WitnessedRekorV2(FakeRekorV2):
    def __init__(self, witnesses):
        super().__init__()
        self.witnesses = witnesses

    def checkpoint(self) -> str:
        text = super().checkpoint()
        for w in self.witnesses:
            text = w.cosign(text)
        return text


def test_rekor_v2_requires_witness_quorum():
    a, b = WitnessKey("a.example"), WitnessKey("b.example")
    fake = WitnessedRekorV2([a])
    policy = tlog.WitnessPolicy((tlog.parse_vkey(a.vkey), tlog.parse_vkey(b.vkey)), 1)
    sink = anchor.RekorV2Sink(
        "https://rekor2.example",
        _ec_pem(),
        fake,
        log_key_pem=fake.log_key_pem,
        state=tlog.CheckpointState(),
        witnesses=policy,
    )
    stmt = {"segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    strict = tlog.WitnessPolicy(policy.witnesses, 2)
    sink.witnesses = strict
    assert not sink.check(stmt, receipt)
    with pytest.raises(OSError):
        sink.publish({"segment": 2})


# --- TrustedRoot / SigningConfig ---------------------------------------------------------------------

V1_URL, V2_URL = "https://rekor.example", "https://log2026-1.rekor.example"


def _der(key) -> str:
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(spki).decode()


def _trusted_root(v1_key, v2_key, *, tsa_cert_der: bytes | None = None, v2_end: str | None = None) -> dict:
    v2_valid = {"start": "2025-09-23T00:00:00Z", **({"end": v2_end} if v2_end else {})}
    root = {
        "mediaType": "application/vnd.dev.sigstore.trustedroot+json;version=0.1",
        "tlogs": [
            {
                "baseUrl": V1_URL,
                "publicKey": {
                    "rawBytes": _der(v1_key),
                    "keyDetails": "PKIX_ECDSA_P256_SHA_256",
                    "validFor": {"start": "2021-01-12T11:53:27Z"},
                },
            },
            {
                "baseUrl": V2_URL + "/",
                "publicKey": {"rawBytes": _der(v2_key), "keyDetails": "PKIX_ED25519", "validFor": v2_valid},
            },
        ],
        "timestampAuthorities": [],
    }
    if tsa_cert_der:
        root["timestampAuthorities"] = [
            {"certChain": {"certificates": [{"rawBytes": base64.b64encode(tsa_cert_der).decode()}]}}
        ]
    return root


def _signing(v2_start="2025-10-01T00:00:00Z", v2_major=2, extra=()) -> dict:
    return {
        "mediaType": "application/vnd.dev.sigstore.signingconfig.v0.2+json",
        "rekorTlogUrls": [
            *extra,
            {"url": V2_URL, "majorApiVersion": v2_major, "validFor": {"start": v2_start}, "operator": "x"},
            {"url": V1_URL, "majorApiVersion": 1, "validFor": {"start": "2021-01-12T11:53:27Z"}, "operator": "x"},
        ],
        "tsaUrls": [
            {"url": "https://tsa.example", "majorApiVersion": 1, "validFor": {"start": "2025-07-04T00:00:00Z"}}
        ],
    }


def test_trusted_root_and_selection():
    from test_r3_anchor import Pki

    v1, v2 = _ec_key(), ed25519.Ed25519PrivateKey.generate()
    pki = Pki()
    root = trust.TrustedRoot.from_json(
        _trusted_root(v1, v2, tsa_cert_der=pki.root.public_bytes(serialization.Encoding.DER))
    )
    assert root.log(V2_URL).key_details == "PKIX_ED25519"
    assert anchor.load_roots(root.tsa_roots_pem)[0] == pki.root
    with pytest.raises(ValueError, match="нет в TrustedRoot"):
        root.log("https://unknown.example")
    expired = trust.TrustedRoot.from_json(_trusted_root(v1, v2, v2_end="2026-01-01T00:00:00Z"))
    with pytest.raises(ValueError, match="недействителен"):
        expired.log(V2_URL)
    with pytest.raises(ValueError, match="TrustedRoot"):
        trust.TrustedRoot.from_json({"mediaType": "x"})

    signing = trust.SigningConfig.from_json(_signing())
    assert signing.select_rekor().url == V2_URL and signing.tsa[0].url == "https://tsa.example"
    later = trust.SigningConfig.from_json(_signing(v2_start="2099-01-01T00:00:00Z"))
    assert later.select_rekor().url == V1_URL  # v2 ещё не начал действовать
    future_api = trust.SigningConfig.from_json(_signing(v2_major=3))
    assert future_api.select_rekor().major == 1  # неизвестная версия API пропускается
    ended = {
        "url": "https://old.example",
        "majorApiVersion": 2,
        "validFor": {"start": "2020-01-01T00:00:00Z", "end": "2021-01-01T00:00:00Z"},
    }
    assert trust.SigningConfig.from_json(_signing(extra=[ended])).select_rekor().url == V2_URL
    with pytest.raises(ValueError, match="v0.2"):
        trust.SigningConfig.from_json({"mediaType": "v0.1"})
    with pytest.raises(ValueError, match="нет действующего"):
        trust.SigningConfig.from_json(
            {"mediaType": "application/vnd.dev.sigstore.signingconfig.v0.2+json"}
        ).select_rekor()

    t = trust.Trust(root, signing, "test")
    assert trust.rekor_target(t)[:2] == (V2_URL, 2)
    assert trust.rekor_target(t, V1_URL)[:2] == (V1_URL, 1)
    assert trust.rekor_target(trust.Trust(root, None, "test"), V2_URL)[:2] == (V2_URL, 2)  # по типу ключа
    with pytest.raises(ValueError, match="SigningConfig"):
        trust.rekor_target(trust.Trust(root, None, "test"))


def _write_trust(directory: Path, v1, v2) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / trust.TRUSTED_ROOT).write_text(json.dumps(_trusted_root(v1, v2)))
    (directory / trust.SIGNING_CONFIG).write_text(json.dumps(_signing()))
    return directory / trust.TRUSTED_ROOT, directory / trust.SIGNING_CONFIG


def test_load_tuf_cache_and_offline_fallbacks(tmp_path, caplog):
    v1, v2 = _ec_key(), ed25519.Ed25519PrivateKey.generate()
    env = {"MANDAT_SIGSTORE_TUF_CACHE": str(tmp_path / "cache")}
    seen = []

    def refresh(cache, url, bootstrap):
        seen.append((url, json.loads(bootstrap)["signed"]["_type"]))
        _write_trust(cache / "targets", v1, v2)

    loaded = trust.load(env, refresh)
    assert loaded.source == "tuf" and seen == [(trust.TUF_URL, "root")]
    assert loaded.signing and loaded.signing.select_rekor().url == V2_URL

    def down(cache, url, bootstrap):
        raise OSError("no network")

    assert trust.load(env, down).source == "кеш (TUF недоступен)"
    assert "TUF Sigstore недоступен" in caplog.text
    assert trust.load({**env, "MANDAT_SIGSTORE_OFFLINE": "1"}, down).source == "кеш (офлайн по настройке)"
    empty = {"MANDAT_SIGSTORE_TUF_CACHE": str(tmp_path / "none"), "MANDAT_SIGSTORE_OFFLINE": "1"}
    with pytest.raises(ValueError, match="нет TrustedRoot"):
        trust.load(empty)
    root_file, config_file = _write_trust(tmp_path / "admin", v1, v2)
    admin = trust.load({**empty, "MANDAT_SIGSTORE_TRUSTED_ROOT": str(root_file)})
    assert admin.source == "файл администратора" and admin.signing is None
    admin = trust.load(
        {**empty, "MANDAT_SIGSTORE_TRUSTED_ROOT": str(root_file), "MANDAT_SIGSTORE_SIGNING_CONFIG": str(config_file)}
    )
    assert admin.signing is not None
    custom = tmp_path / "root.json"
    custom.write_text(json.dumps({"signed": {"_type": "custom"}}))
    trust.load(
        {**env, "MANDAT_SIGSTORE_TUF_ROOT": str(custom), "MANDAT_SIGSTORE_TUF_URL": "https://tuf.example/"}, refresh
    )
    assert seen[-1] == ("https://tuf.example", "custom")


def test_bundled_root_is_sigstore():
    root = json.loads(trust.bundled_tuf_root())
    assert root["signed"]["_type"] == "root" and "root" in root["signed"]["roles"]


def test_configured_sigstore_sink(tmp_path, monkeypatch):
    v1, v2 = _ec_key(), ed25519.Ed25519PrivateKey.generate()
    _write_trust(tmp_path / "cache" / "targets", v1, v2)
    key = tmp_path / "k.pem"
    key.write_bytes(_ec_pem())
    w = WitnessKey("w.example")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_REKOR_KEY", str(key))
    monkeypatch.setenv("MANDAT_SIGSTORE_TUF_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("MANDAT_SIGSTORE_OFFLINE", "1")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_WITNESSES", w.vkey)
    (sink,) = anchor.configured_sinks("sigstore")
    assert isinstance(sink, anchor.RekorV2Sink) and sink.url == V2_URL and sink.witnesses is not None
    assert sink.log_key == _pem_pub(v2)
    (sink,) = anchor.configured_sinks(f"sigstore:{V1_URL}")
    assert isinstance(sink, anchor.RekorSink) and sink.log_key == _pem_pub(v1)
    monkeypatch.delenv("MANDAT_JOURNAL_ANCHOR_REKOR_KEY")
    with pytest.raises(ValueError, match="REKOR_KEY"):
        anchor.configured_sinks("sigstore")


# --- настоящий клиент TUF против локального репозитория ------------------------------------------------


def _tuf_repo(directory: Path, files: dict[str, bytes]) -> bytes:
    """Репозиторий TUF (root/targets/snapshot/timestamp, Ed25519), без consistent snapshot."""
    from securesystemslib.signer import CryptoSigner
    from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp
    from tuf.api.serialization.json import JSONSerializer

    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=7)
    signers = {role: CryptoSigner.generate_ed25519() for role in ("root", "targets", "snapshot", "timestamp")}
    root = Metadata(Root(expires=expires, consistent_snapshot=False))
    for role, signer in signers.items():
        root.signed.add_key(signer.public_key, role)
    targets = Metadata(Targets(expires=expires))
    (directory / "targets").mkdir(parents=True)
    for name, data in files.items():
        (directory / "targets" / name).write_bytes(data)
        targets.signed.targets[name] = TargetFile.from_data(name, data)
    snapshot = Metadata(Snapshot(expires=expires))
    snapshot.signed.meta["targets.json"] = MetaFile(version=1)
    timestamp = Metadata(Timestamp(expires=expires))
    timestamp.signed.snapshot_meta = MetaFile(version=1)
    out = {"1.root.json": root, "targets.json": targets, "snapshot.json": snapshot, "timestamp.json": timestamp}
    for name, md in out.items():
        md.sign(signers[md.signed.type])
        md.to_file(str(directory / name), JSONSerializer(compact=True))
    return (directory / "1.root.json").read_bytes()


def test_tuf_refresh_against_local_repository(tmp_path):
    v1, v2 = _ec_key(), ed25519.Ed25519PrivateKey.generate()
    files = {
        trust.TRUSTED_ROOT: json.dumps(_trusted_root(v1, v2)).encode(),
        trust.SIGNING_CONFIG: json.dumps(_signing()).encode(),
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    bootstrap = _tuf_repo(repo, files)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(repo))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        cache = tmp_path / "cache"
        trust._tuf_refresh(cache, url, bootstrap)
        loaded = trust.load({"MANDAT_SIGSTORE_TUF_CACHE": str(cache), "MANDAT_SIGSTORE_OFFLINE": "1"})
        assert loaded.root.log(V2_URL).key_pem == _pem_pub(v2)
        # Подмена цели на сервере: хеш не совпадает с подписанным targets.json — клиент TUF отказывает.
        (repo / "targets" / trust.TRUSTED_ROOT).write_bytes(b'{"mediaType": "evil"}')
        other = tmp_path / "cache2"
        with pytest.raises(Exception):  # noqa: B017 - tuf.api.exceptions.LengthOrHashMismatchError
            trust._tuf_refresh(other, url, bootstrap)
        assert not (other / "targets" / trust.TRUSTED_ROOT).exists()
        assert hashlib.sha256((cache / "targets" / trust.TRUSTED_ROOT).read_bytes()).digest()
    finally:
        server.shutdown()
