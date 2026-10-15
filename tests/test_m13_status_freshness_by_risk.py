"""M-13 acceptance: freshness by risk; seen required for authorize in prod."""

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


def _issued(tmp_path: Path, *, list_age: timedelta = timedelta(0)):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    cred = attestation.issue(_report(), key, now=NOW, status=registry.credential_status(idx))
    registry.save(key, now=NOW - list_age)
    return key, cred, sd


@pytest.fixture(autouse=True)
def _clear_age_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_BY_RISK", raising=False)


def test_status_max_age_for_payment_vs_archive():
    assert status_max_age_for("payment") < 1
    assert status_max_age_for("archive_receipt") == 7.0
    assert status_max_age_for(None) == 7.0


def test_unknown_risk_uses_strictest_window():
    payment = status_max_age_for("payment")
    assert status_max_age_for("xyz") == payment
    assert status_max_age_for("xyz") < status_max_age_for("default")


def test_global_env_is_min_with_risk_map(monkeypatch: pytest.MonkeyPatch):
    """G1: global ceiling cannot loosen payment (min(global, risk))."""
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "7")
    payment = status_max_age_for("payment")
    assert payment < 1  # still ~1h, not 7d
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "0.01")
    assert status_max_age_for("archive_receipt") == 0.01  # global tighter than archive


def test_payment_fresh_30m(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(minutes=30))
    state = check_status(
        cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk="payment", seen={}, require_seen=True
    )["state"]
    assert state == "действует"


def test_payment_stale_2h(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(hours=2))
    state = check_status(
        cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk="payment", seen={}, require_seen=True
    )["state"]
    assert "устарел" in state


def test_default_6d_ok(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(days=6))
    state = check_status(
        cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk=None, seen={}, require_seen=True
    )["state"]
    assert state == "действует"


def test_default_8d_stale(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(days=8))
    state = check_status(
        cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk=None, seen={}, require_seen=True
    )["state"]
    assert "устарел" in state


def test_unknown_risk_2h_stale(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(hours=2))
    state = check_status(
        cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk="xyz", seen={}, require_seen=True
    )["state"]
    assert "устарел" in state


def test_prod_no_seen_authorize_fail_closed(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(minutes=1))
    problems = attestation.authorize_operation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd, operation_risk="payment"
    )
    assert any("seen" in p for p in problems)


def test_dev_no_seen_authorize_ok(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "dev")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(minutes=1))
    problems = attestation.authorize_operation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd, operation_risk="payment"
    )
    assert problems == []


def test_authorize_passes_risk_refuses_stale_payment(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(hours=2))
    problems = attestation.authorize_operation(
        cred,
        [key.public_key()],
        now=NOW + timedelta(minutes=1),
        status_dir=sd,
        seen={},
        operation_risk="payment",
    )
    assert any("устарел" in p for p in problems)


def test_env_override_bad(monkeypatch: pytest.MonkeyPatch):
    from mandat.audit.status_list import status_max_age_days

    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "nan")
    with pytest.raises(ValueError, match="MANDAT_STATUS_MAX_AGE_DAYS"):
        status_max_age_days()
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "-1")
    with pytest.raises(ValueError, match="MANDAT_STATUS_MAX_AGE_DAYS"):
        status_max_age_days()


def test_check_status_require_seen_without_seen_raises(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path)
    with pytest.raises(ValueError, match="high-watermark seen"):
        check_status(cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), require_seen=True)
