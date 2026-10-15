"""G-B: в protected режиме «risk не указан» = строжайшее окно; demo + status-dir + revoked не печатает успех."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.status_list import StatusRegistry, _risk_age_mapping, check_status, status_max_age_for
from mandat.cli import build_parser

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_BY_RISK", raising=False)


def _report():
    return {
        "verdict": "confirmed",
        "profile": "strict",
        "organization": {"inn": "7707083893"},
        "requester": {"inn": "123456789012"},
        "operation": {"code": "payment_sign", "date": NOW.date().isoformat()},
        "chain": {"poa_numbers": ["poc"]},
        "rule_pack": {"sha256": "a" * 64},
        "reproducibility_hash": "b" * 64,
    }


def _issued(tmp_path: Path, *, list_age: timedelta = timedelta(0), revoke: bool = False, save_now=NOW):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    cred = attestation.issue(_report(), key, now=NOW, status=registry.credential_status(idx))
    if revoke:
        registry.mark(idx, "revocation", "test")
    registry.save(key, now=save_now - list_age)
    return key, cred, sd


@pytest.mark.parametrize("blank", [None, "", "   ", "\t"])
@pytest.mark.parametrize("env", ["pilot", "prod", "staging"])
def test_protected_blank_risk_is_strictest(monkeypatch, env, blank):
    monkeypatch.setenv("MANDAT_ENV", env)
    strictest = min(_risk_age_mapping().values())
    assert status_max_age_for(blank) == strictest
    assert strictest < status_max_age_for("default")


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_demo_blank_risk_keeps_default(monkeypatch, blank):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert status_max_age_for(blank) == 7.0


def test_protected_explicit_default_is_still_default(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    assert status_max_age_for("default") == 7.0
    assert status_max_age_for("DEFAULT ") == 7.0


def test_protected_blank_risk_cannot_be_weakened_by_json(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_BY_RISK", '{"default": 1}')
    # JSON может только ужесточать: strictest не больше прежнего payment
    assert status_max_age_for(None) <= 1.0
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_BY_RISK", '{"default": 30}')
    with pytest.raises(ValueError):
        status_max_age_for(None)


def test_protected_global_cap_still_min(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "0.001")
    assert status_max_age_for(None) == 0.001


def test_check_status_prod_none_risk_rejects_6_day_list_but_default_accepts(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(days=6))
    now = NOW + timedelta(minutes=1)
    stale = check_status(cred, sd, key.public_key(), now=now, operation_risk=None, seen={}, require_seen=True)
    assert "устарел" in stale["state"]
    fresh = check_status(cred, sd, key.public_key(), now=now, operation_risk="default", seen={}, require_seen=True)
    assert fresh["state"] == "действует"


def test_demo_none_risk_accepts_6_day_list(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path, list_age=timedelta(days=6))
    state = check_status(cred, sd, key.public_key(), now=NOW + timedelta(minutes=1), operation_risk=None)["state"]
    assert state == "действует"


def _cli(tmp_path, key, cred, *extra):
    path = tmp_path / "cred.json"
    path.write_text(attestation.dump(cred), encoding="utf-8")
    pub = attestation.public_key_multibase(key.public_key())
    args = build_parser().parse_args(["attest-verify", str(path), "--public-key", pub, *extra])
    return args.func(args)


def test_demo_status_dir_revoked_prints_no_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path, revoke=True)
    code = _cli(tmp_path, key, cred, "--status-dir", str(sd))
    out = capsys.readouterr()
    assert code == 3
    assert "подпись верна" not in out.out + out.err
    assert "вердикт в удостоверении" not in out.out
    assert "не принимается" in out.err and "отозван" in out.err


def test_demo_status_dir_ok_prints_success_after_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path)
    code = _cli(tmp_path, key, cred, "--status-dir", str(sd))
    out = capsys.readouterr().out
    assert code == 0
    assert "подпись верна" in out and "статус удостоверения: действует" in out
    assert out.index("подпись верна") < out.index("статус удостоверения")


def test_demo_status_dir_broken_list_prints_no_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd = _issued(tmp_path)
    for f in sd.iterdir():
        if f.is_file() and f.name != ".registry.lock":
            f.write_text("not json", encoding="utf-8")
    code = _cli(tmp_path, key, cred, "--status-dir", str(sd))
    out = capsys.readouterr()
    assert code != 0
    assert "подпись верна" not in out.out + out.err


def test_prod_status_dir_revoked_prints_no_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd = _issued(tmp_path, revoke=True, save_now=datetime.now(UTC))
    code = _cli(tmp_path, key, cred, "--status-dir", str(sd))
    out = capsys.readouterr()
    assert code == 3
    assert "подпись верна" not in out.out + out.err
