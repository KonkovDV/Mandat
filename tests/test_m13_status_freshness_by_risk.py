"""M-13: свежесть списка статусов зависит от риска операции; seen обязателен для authorize в prod."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.status_list import StatusRegistry, check_status, status_max_age_for

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _report(**extra):
    base = {
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


def _issued(tmp_path: Path, *, list_age_days: float = 0.0):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    cred = attestation.issue(_report(), key, now=NOW, status=registry.credential_status(idx))
    issued_at = NOW - timedelta(days=list_age_days)
    registry.save(key, now=issued_at)
    return key, cred, sd


def test_status_max_age_for_payment_vs_archive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_BY_RISK", raising=False)
    assert status_max_age_for("payment") < 1
    assert status_max_age_for("archive_receipt") == 7.0
    assert status_max_age_for(None) == 7.0


def test_global_env_overrides_risk_map(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "3")
    assert status_max_age_for("payment") == 3.0


def test_payment_rejects_two_day_old_list(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path, list_age_days=2)
    state = check_status(
        cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk="payment", require_seen=False
    )["state"]
    assert "устарел" in state


def test_archive_allows_two_day_old_list(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path, list_age_days=2)
    state = check_status(
        cred,
        sd,
        key.public_key(),
        now=NOW + timedelta(minutes=1),
        operation_risk="archive_receipt",
        require_seen=False,
    )["state"]
    assert state == "действует"


def test_check_status_require_seen_without_seen_raises(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path)
    with pytest.raises(ValueError, match="high-watermark seen"):
        check_status(cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), require_seen=True)


def test_authorize_prod_without_seen_rejects(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path)
    problems = attestation.authorize_operation(cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd)
    assert any("seen" in p for p in problems)


def test_authorize_prod_with_seen_and_payment_stale(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    key, cred, sd = _issued(tmp_path, list_age_days=2)
    problems = attestation.authorize_operation(
        cred,
        [key.public_key()],
        now=NOW + timedelta(minutes=1),
        status_dir=sd,
        seen={},
        operation_risk="payment",
    )
    assert any("устарел" in p for p in problems)
