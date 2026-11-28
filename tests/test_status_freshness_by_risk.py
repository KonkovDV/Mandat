"""Свежесть списка статусов по риску операции; в prod допуск требует ``seen``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.status_list import (
    StatusRegistry,
    _risk_age_mapping,
    check_status,
    known_risk,
    operation_tier,
    status_max_age_for,
)

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


def _issued(tmp_path: Path, *, list_age: timedelta = timedelta(0), code: str = "payment_sign"):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    report = _report(operation={"code": code, "date": NOW.date().isoformat()})
    cred = attestation.issue(report, key, now=NOW, status=registry.credential_status(idx))
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
    """Global ceiling cannot loosen payment (min(global, risk))."""
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "7")
    payment = status_max_age_for("payment")
    assert payment < 1  # still ~1h, not 7d
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "0.01")
    assert status_max_age_for("archive_receipt") == 0.01  # global tighter than archive


def test_ga_protected_blocks_loosening_risk_json(monkeypatch: pytest.MonkeyPatch):
    """G-A: in pilot/prod JSON overrides may only tighten vs defaults."""
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_BY_RISK", '{"payment":3650}')
    with pytest.raises(ValueError, match="нельзя ослабить"):
        _risk_age_mapping()


def test_ga_protected_allows_tightening_risk_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_BY_RISK", '{"payment":0.01}')
    assert _risk_age_mapping()["payment"] == 0.01
    assert status_max_age_for("payment") == 0.01


def test_ga_demo_allows_loosening_risk_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_BY_RISK", '{"payment":3650}')
    assert _risk_age_mapping()["payment"] == 3650.0


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
    """Явный risk=default остаётся 7 дней; «не указан» в prod — строжайший (см. test_status_freshness_unknown_risk)."""
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(days=6))
    state = check_status(
        cred,
        sd,
        key.public_key(),
        now=NOW + timedelta(minutes=1),
        operation_risk="default",
        seen={},
        require_seen=True,
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


# --- допуск: окно задаёт подписанная операция, запрос его только сужает ---------------------------------------


def _authorize(key, cred, sd, risk):
    return attestation.authorize_operation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd, seen={}, operation_risk=risk
    )


def test_operation_tier_comes_from_the_operation_catalog():
    assert operation_tier("PAYMENT_SIGN") == "payment"
    assert operation_tier("REQUISITES_CHANGE") == operation_tier("USER_ADMIN") == "high"
    assert operation_tier("STATEMENT_VIEW") == "default"
    assert operation_tier("payment_sign") is None and operation_tier(None) is None


@pytest.mark.parametrize("env", ["demo", "pilot"])
@pytest.mark.parametrize("risk", ["low", "archive", "archive_receipt", "default", "LOW ", None])
def test_requested_risk_cannot_widen_the_window_of_a_signed_payment(tmp_path, monkeypatch, env, risk):
    """Платёж по списку трёхдневной давности: окно платежа около часа, слово вызывающей системы его не расширяет."""
    monkeypatch.setenv("MANDAT_ENV", env)
    key, cred, sd = _issued(tmp_path, list_age=timedelta(days=3), code="PAYMENT_SIGN")
    assert any("устарел" in p for p in _authorize(key, cred, sd, risk))


def test_requested_risk_only_tightens_the_window_of_a_low_risk_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(hours=2), code="STATEMENT_VIEW")
    assert not any("устарел" in p for p in _authorize(key, cred, sd, "default"))
    assert any("устарел" in p for p in _authorize(key, cred, sd, "payment"))


@pytest.mark.parametrize("risk", ["default", "archive", None])
def test_operation_outside_the_catalog_gets_the_strictest_window(tmp_path, monkeypatch, risk):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(hours=2))  # payment_sign: такого кода в каталоге нет
    assert any("устарел" in p for p in _authorize(key, cred, sd, risk))


def test_known_risk_accepts_map_levels_and_blank_only(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    assert all(known_risk(word) for word in ("payment", "LOW ", "default", "", None))
    assert not any(known_risk(word) for word in ("lowest", "архив", "pay ment"))
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_BY_RISK", '{"card": 0.01}')
    assert known_risk("card")
