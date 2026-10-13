"""Удостоверение: подпись отдельно, принятие отдельно; идентификатор представителя не словарный хеш."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mandat.audit import attestation
from mandat.audit.status_list import StatusRegistry
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)


def _report(profile: str = "strict") -> dict:
    m = synth.build("ok", 1).inline_manifest()
    return verify_case(load_case_inline(m), profile=profile).as_dict()


def _key():
    key = attestation.Ed25519PrivateKey.generate()
    return key, key.public_key()


def _problems(cred, public, **kw):
    kw.setdefault("now", NOW + timedelta(minutes=1))
    return attestation.validate_attestation(cred, [public], **kw)


def test_valid_credential_has_no_problems():
    key, public = _key()
    cred = attestation.issue(_report(), key, now=NOW)
    assert attestation.verify_signature(cred, public)
    assert _problems(cred, public) == []


def test_representative_inn_is_not_a_plain_hash():
    key, _ = _key()
    report = _report()
    inn = report["requester"]["inn"]
    plain = hashlib.sha256(inn.encode()).hexdigest()
    cred = attestation.issue(report, key, now=NOW, pseudonym_key=b"k" * 32)
    text = attestation.dump(cred)
    assert plain not in text and inn not in text
    token = cred["credentialSubject"]["representativeToken"]
    other = attestation.issue(report, key, now=NOW, pseudonym_key=b"x" * 32)["credentialSubject"]
    assert other["representativeToken"] != token  # другой издатель или арендатор — другой токен
    assert "representativeToken" not in attestation.issue(report, key, now=NOW)["credentialSubject"]


def test_other_issuer_with_valid_signature_is_rejected():
    key, public = _key()
    other, _ = _key()
    cred = attestation.issue(_report(), other, now=NOW)
    assert attestation.verify_signature(cred, other.public_key())
    assert any("ключ" in p for p in _problems(cred, public))


@pytest.mark.parametrize(
    ("field", "value", "word"),
    [
        ("proofPurpose", "authentication", "назначение"),
        ("cryptosuite", "eddsa-rdfc-2022", "криптонабор"),
    ],
)
def test_wrong_proof_parameters_are_rejected(field, value, word):
    key, public = _key()
    cred = attestation.issue(_report(), key, now=NOW)
    proof = cred["proof"]
    proof[field] = value
    document = {k: v for k, v in cred.items() if k != "proof"}
    config = {k: v for k, v in proof.items() if k != "proofValue"}
    proof["proofValue"] = "z" + attestation.b58encode(key.sign(attestation._hash_data(document, config)))
    assert attestation.verify_signature(cred, public)
    assert any(word in p for p in _problems(cred, public))


def test_wrong_issuer_is_rejected():
    key, public = _key()
    report = _report()
    cred = attestation.issue(report, key, now=NOW)
    cred.pop("proof")
    cred["issuer"] = "did:web:evil.example"
    signed = attestation.sign(cred, key, now=NOW)
    assert any("издатель" in p for p in _problems(signed, public))


@pytest.mark.parametrize("shift", [timedelta(minutes=-5), timedelta(days=400)])
def test_not_yet_valid_or_expired(shift):
    key, public = _key()
    cred = attestation.issue(_report(), key, now=NOW)
    assert any("срок" in p for p in _problems(cred, public, now=NOW + shift))


def test_demo_report_is_not_accepted_unless_allowed():
    key, public = _key()
    cred = attestation.issue(_report("demo"), key, now=NOW)
    assert any("demo" in p for p in _problems(cred, public))
    assert _problems(cred, public, allow_demo=True) == []


def test_tampered_subject_fails_signature():
    key, public = _key()
    cred = attestation.issue(_report(), key, now=NOW)
    cred["credentialSubject"]["verdict"] = "confirmed" if cred["credentialSubject"]["verdict"] != "confirmed" else "x"
    assert not attestation.verify_signature(cred, public)
    assert any("подпись" in p for p in _problems(cred, public))


@pytest.mark.parametrize(("purpose", "state"), [("revocation", "отозвано"), ("suspension", "приостановлено")])
def test_revoked_or_suspended_is_rejected(tmp_path: Path, purpose, state):
    key, public = _key()
    registry = StatusRegistry.open(tmp_path / "status")
    report = _report()
    index = registry.allocate(
        {"org_inn": report["organization"]["inn"], "verdict": report["verdict"], "poa_numbers": []}
    )
    cred = attestation.issue(report, key, now=NOW, status=registry.credential_status(index))
    registry.save(key, now=NOW)
    assert _problems(cred, public, status_dir=tmp_path / "status") == []
    registry = StatusRegistry.open(tmp_path / "status")
    registry.mark(index, purpose, "test")
    registry.save(key, now=NOW)
    assert any(state in p for p in _problems(cred, public, status_dir=tmp_path / "status"))


def test_unknown_status_is_rejected(tmp_path: Path):
    key, public = _key()
    registry = StatusRegistry.open(tmp_path / "status")
    report = _report()
    index = registry.allocate(
        {"org_inn": report["organization"]["inn"], "verdict": report["verdict"], "poa_numbers": []}
    )
    cred = attestation.issue(report, key, now=NOW, status=registry.credential_status(index))
    # списки не сохранены: статус неизвестен
    assert any("статус" in p for p in _problems(cred, public, status_dir=tmp_path / "status"))


def test_signature_uses_jcs():
    from mandat.audit.jcs import canonicalize
    from mandat.data import canonical_json

    document = {"\ufb33": 1, "\U0001f600": 2}
    config = {"a": 1}
    expected = (
        hashlib.sha256(canonicalize(config).encode()).digest()
        + hashlib.sha256(canonicalize(document).encode()).digest()
    )
    assert canonical_json(document) != canonicalize(document)
    assert attestation._hash_data(document, config) == expected
    key, public = _key()
    assert attestation.verify_signature(attestation.sign(document, key, now=NOW), public)


def _legacy() -> tuple[dict, object]:
    import json

    data = json.loads((Path(__file__).parent / "data" / "legacy_attestation.json").read_text(encoding="utf-8"))
    return data["credential"], attestation.public_key_from_multibase(data["publicKeyMultibase"])


def test_attestation_signed_before_rfc8785_still_verifies():
    # Выдано прежним кодом (запись sort_keys, без validUntil, хеш ИНН представителя). Байты подписи те же.
    cred, public = _legacy()
    assert attestation.verify_signature(cred, public)
    tampered = {**cred, "credentialSubject": {**cred["credentialSubject"], "verdict": "refuted"}}
    assert not attestation.verify_signature(tampered, public)


def test_attestation_signed_before_rfc8785_is_not_accepted_without_validity_end():
    cred, public = _legacy()
    problems = attestation.validate_attestation(cred, [public], now=NOW, allow_demo=True)
    assert any("срок действия не указан" in p for p in problems)


def test_new_key_files_are_created_private_and_never_overwritten(tmp_path, monkeypatch):
    import os
    import stat

    from mandat.audit import attestation

    old = os.umask(0o022)
    try:
        attestation.load_or_create_key(tmp_path / "keys" / "k.pem")
        attestation.load_or_create_pseudonym_key(tmp_path / "keys" / "h.key")
    finally:
        os.umask(old)
    for name in ("k.pem", "h.key"):
        assert stat.S_IMODE((tmp_path / "keys" / name).stat().st_mode) == 0o600
    real_open = os.open
    seen: list[int] = []

    def spy(path, flags, mode=0o777, *args, **kwargs):
        seen.append(flags)
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    attestation.load_or_create_pseudonym_key(tmp_path / "keys" / "h2.key")
    assert seen and all(flag & os.O_CREAT and flag & os.O_EXCL for flag in seen)


@pytest.mark.parametrize("size", [0, 16, 31])
def test_short_hmac_key_is_rejected(tmp_path, size):
    from mandat.audit import attestation

    path = tmp_path / "h.key"
    path.write_bytes(b"k" * size)
    with pytest.raises(ValueError):
        attestation.load_or_create_pseudonym_key(path)


def test_key_file_is_written_verbatim_and_concurrent_creation_reuses_it(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from mandat.audit import attestation

    data = bytes(range(256))
    path = tmp_path / "raw.key"
    attestation._write_private(path, data)
    assert path.read_bytes() == data

    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    real_open = os.open
    seen: list[int] = []

    def spy(p, flags, mode=0o777, *args, **kwargs):
        seen.append(flags)
        return real_open(p, flags & ~0x8000, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    attestation._write_private(tmp_path / "bin.key", data)
    assert seen and all(flag & 0x8000 for flag in seen)

    first = attestation.load_or_create_pseudonym_key(tmp_path / "h.key")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert attestation.load_or_create_pseudonym_key(tmp_path / "h.key") == first


def test_key_is_published_only_when_complete(tmp_path, monkeypatch):
    """Читатель не видит недописанный ключ: под именем ключа файл появляется уже полным,
    а при сбое записи не остаётся ни ключа, ни временного файла."""
    import os

    from mandat.audit import attestation

    data = bytes(range(256)) * 4
    path = tmp_path / "k.key"
    real_link = os.link
    seen: list[tuple[bool, bytes]] = []

    def spy_link(src, dst, *args, **kwargs):
        seen.append((os.path.exists(dst), open(src, "rb").read()))
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", spy_link)
    assert attestation._write_private(path, data) is True
    assert seen == [(False, data)]
    assert path.read_bytes() == data
    assert [p.name for p in tmp_path.iterdir()] == ["k.key"]
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600

    assert attestation._write_private(path, b"other") is False
    assert path.read_bytes() == data
    assert [p.name for p in tmp_path.iterdir()] == ["k.key"]

    broken = tmp_path / "broken.key"

    def failing_fsync(_fd):
        raise OSError("диск отказал")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="диск отказал"):
        attestation._write_private(broken, data)
    assert not broken.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["k.key"]
