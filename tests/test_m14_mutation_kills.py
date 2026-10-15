"""M-14: тесты против выживших мутантов mutmut в attestation.py и status_list.py.

Цель — ветвление и данные (границы, ключи, флаги), а не тексты сообщений.
Результат прогона и оставшиеся выжившие — docs/testing/MUTATION.md.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.status_list import (
    MAX_LIST_BYTES,
    MIN_BITS,
    StatusRegistry,
    check_status,
    decode_bits,
    encode_bits,
    list_url,
    set_bit,
    status_max_age_for,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _stand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANDAT_ENV", raising=False)


def _report(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "verdict": "confirmed",
        "profile": "strict",
        "organization": {"inn": "7707083893"},
        "requester": {"inn": "123456789012"},
        "operation": {"code": "payment_sign", "date": NOW.date().isoformat()},
        "chain": {"poa_numbers": ["poc"]},
        "rule_pack": {"sha256": "a" * 64},
        "reproducibility_hash": "b" * 64,
    }
    base.update(extra)
    return base


def _issued(tmp_path: Path, **report: object):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    cred = attestation.issue(_report(**report), key, now=NOW, status=registry.credential_status(idx))
    registry.save(key, now=NOW)
    return key, cred, sd, registry


# --- validate_attestation / authorize_operation ---


def test_key_from_foreign_issuer_prefix_is_not_trusted(tmp_path: Path) -> None:
    key, cred, _, _ = _issued(tmp_path)
    fragment = cred["proof"]["verificationMethod"].split("#", 1)[1]
    cred["proof"]["verificationMethod"] = f"did:web:other.example#{fragment}"
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW, require_status=False)
    assert any(p.startswith("ключ подписи") for p in problems)
    assert not any(p.startswith("подпись не верна") for p in problems)


def test_validity_window_includes_start_and_excludes_end(tmp_path: Path) -> None:
    key, cred, _, _ = _issued(tmp_path)
    start = datetime.fromisoformat(cred["validFrom"])
    end = datetime.fromisoformat(cred["validUntil"])
    keys = [key.public_key()]
    assert attestation.validate_attestation(cred, keys, now=start, require_status=False) == []
    at_end = attestation.validate_attestation(cred, keys, now=end, require_status=False)
    assert any(p.startswith("срок действия") for p in at_end)


def test_validate_passes_require_seen_to_check_status(tmp_path: Path) -> None:
    key, cred, sd, _ = _issued(tmp_path)
    problems = attestation.validate_attestation(
        cred, [key.public_key()], now=NOW, status_dir=sd, require_status=True, require_seen=True
    )
    assert any(p.startswith("статус не проверен") and "seen" in p for p in problems)


def test_authorize_rejects_demo_profile_by_default(tmp_path: Path) -> None:
    key, cred, sd, _ = _issued(tmp_path, profile="demo")
    problems = attestation.authorize_operation(cred, [key.public_key()], now=NOW, status_dir=sd)
    assert any("demo" in p for p in problems)


def test_authorize_requires_status_even_on_stand(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    StatusRegistry.open(sd).save(key, now=NOW)
    cred = attestation.issue(_report(), key, now=NOW)
    problems = attestation.authorize_operation(cred, [key.public_key()], now=NOW, status_dir=sd)
    assert any("credentialStatus" in p for p in problems)


def test_authorize_missing_seen_in_prod_reported_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key, cred, sd, _ = _issued(tmp_path)
    monkeypatch.setenv("MANDAT_ENV", "prod")
    problems = attestation.authorize_operation(cred, [key.public_key()], now=NOW, status_dir=sd, operation_risk="low")
    assert [p for p in problems if "seen" in p] == [
        "нет high-watermark seen: в защищённом режиме обязателен для допуска операции"
    ]


def test_authorize_ok_on_stand(tmp_path: Path) -> None:
    key, cred, sd, _ = _issued(tmp_path)
    assert attestation.authorize_operation(cred, [key.public_key()], now=NOW, status_dir=sd) == []


# --- биты и реестр ---


def test_set_bit_changes_only_its_bit() -> None:
    bits = bytearray(b"\x00\xff")
    set_bit(bits, 3)
    set_bit(bits, 1)
    assert bits == bytearray(b"\x50\xff")
    set_bit(bits, 12, False)
    set_bit(bits, 3, False)
    assert bits == bytearray(b"\x40\xf7")


def test_decode_bits_size_boundary() -> None:
    assert len(decode_bits(encode_bits(bytearray(MAX_LIST_BYTES)))) == MAX_LIST_BYTES
    with pytest.raises(ValueError):
        decode_bits(encode_bits(bytearray(MAX_LIST_BYTES + 1)))


def test_mark_accepts_first_and_last_index_and_rejects_size(tmp_path: Path) -> None:
    registry = StatusRegistry.open(tmp_path / "status")
    last = registry.size - 1
    for index in (0, last, registry.size):
        registry.entries[str(index)] = {}
    assert registry.mark(0, "revocation", "r0") is True
    assert registry.mark(last, "suspension", "rl") is True
    assert registry.entries["0"]["history"] == [{"purpose": "revocation", "reason": "r0"}]
    assert registry.mark(0, "revocation", "again") is False
    with pytest.raises(ValueError):
        registry.mark(registry.size, "revocation", "out")


def test_credential_status_entry_shape(tmp_path: Path) -> None:
    registry = StatusRegistry.open(tmp_path / "status")
    entry = registry.credential_status(7)[0]
    assert entry == {
        "id": f"{list_url('revocation')}#7",
        "type": "BitstringStatusListEntry",
        "statusPurpose": "revocation",
        "statusListIndex": "7",
        "statusListCredential": list_url("revocation"),
        "epoch": registry.epoch,
    }


# --- check_status ---


def test_check_status_without_entries() -> None:
    assert check_status({}, Path("/nonexistent"), []) == {"state": "без статуса"}


def test_check_status_rejects_other_epoch(tmp_path: Path) -> None:
    key, cred, sd, _ = _issued(tmp_path)
    for entry in cred["credentialStatus"]:
        entry["epoch"] = "other"
    with pytest.raises(ValueError, match="эпохи"):
        check_status(cred, sd, key.public_key())


def test_check_status_rejects_list_older_than_issuer_state(tmp_path: Path) -> None:
    key, cred, sd, registry = _issued(tmp_path)
    registry.save(key, now=NOW)
    shutil.copy(sd / "snapshots" / "1" / "revocation.json", sd / "snapshots" / "2" / "revocation.json")
    with pytest.raises(ValueError, match="состояния издателя"):
        check_status(cred, sd, key.public_key())


def test_check_status_seen_watermark_keys_and_replay(tmp_path: Path) -> None:
    key, cred, sd, registry = _issued(tmp_path)
    registry.save(key, now=NOW)
    seen: dict[str, int] = {}
    check_status(cred, sd, key.public_key(), seen=seen)
    assert seen == {f"{list_url(p)}#{registry.epoch}": 2 for p in ("revocation", "suspension")}
    old = tmp_path / "old"
    shutil.copytree(sd / "snapshots" / "1", old)
    with pytest.raises(ValueError, match="уже проверенного"):
        check_status(cred, old, key.public_key(), seen=seen)


def test_check_status_index_bounds(tmp_path: Path) -> None:
    key, cred, sd, _ = _issued(tmp_path)
    for index, ok in ((0, True), (MIN_BITS - 1, True), (MIN_BITS, False)):
        for entry in cred["credentialStatus"]:
            entry["statusListIndex"] = str(index)
        if ok:
            assert check_status(cred, sd, key.public_key())["state"] == "действует"
        else:
            with pytest.raises(ValueError, match="RANGE_ERROR"):
                check_status(cred, sd, key.public_key())


def test_check_status_future_and_stale_boundaries(tmp_path: Path) -> None:
    key, cred, sd, _ = _issued(tmp_path)
    pub = key.public_key()
    assert check_status(cred, sd, pub, now=NOW - timedelta(minutes=5))["state"] == "действует"
    assert check_status(cred, sd, pub, now=NOW - timedelta(minutes=5, seconds=1))["state"] == "список из будущего"
    max_age = timedelta(days=status_max_age_for(None))
    assert check_status(cred, sd, pub, now=NOW + max_age)["state"] == "действует"
    assert check_status(cred, sd, pub, now=NOW + max_age + timedelta(seconds=1))["state"] == "список устарел"
