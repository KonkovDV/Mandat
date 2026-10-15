"""M-01: обязательный статус в pilot/prod; crypto ≠ допуск операции."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.status_list import StatusRegistry

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # в прошлом относительно wall-clock CLI


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


def _issued(tmp_path: Path, *, revoke: bool = False, suspend: bool = False, with_status: bool = True):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    status = registry.credential_status(idx) if with_status else None
    cred = attestation.issue(_report(), key, now=NOW, status=status)
    if with_status:
        if revoke:
            registry.mark(idx, "revocation", "test")
        if suspend:
            registry.mark(idx, "suspension", "test")
        registry.save(key, now=NOW)
    return key, cred, sd, idx


def test_validate_attestation_without_status_dir_fails_in_prod(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path, revoke=True)
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW + timedelta(minutes=1))
    assert problems, "prod без status_dir обязан отказать"
    assert any("статус" in p or "каталог" in p for p in problems)


def test_validate_attestation_revoked_with_status_dir_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path, revoke=True)
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd)
    assert any("отозвано" in p for p in problems)


def test_validate_attestation_suspended_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path, suspend=True)
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd)
    assert any("приостановлено" in p for p in problems)


def test_validate_attestation_missing_credential_status_fails_when_required(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key = Ed25519PrivateKey.generate()
    cred = attestation.issue(_report(), key, now=NOW)
    sd = tmp_path / "status"
    StatusRegistry.open(sd).save(key, now=NOW)
    problems = attestation.validate_attestation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd, require_status=True
    )
    assert any("credentialStatus" in p or "статус" in p for p in problems)


def test_validate_attestation_corrupt_status_list_fails(tmp_path, monkeypatch):
    from mandat.audit.status_list import published_root

    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path)
    root = published_root(sd)
    (root / "revocation.json").write_text("{not-json", encoding="utf-8")
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd)
    assert any("статус" in p for p in problems)


def test_validate_attestation_stale_list_fails_when_now_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, idx = _issued(tmp_path)
    registry = StatusRegistry.open(sd)
    registry.save(key, now=NOW - timedelta(days=8))
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd)
    assert any("устарел" in p for p in problems)


def test_cli_attest_verify_prod_requires_status_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path, revoke=True)
    path = tmp_path / "cred.json"
    path.write_text(attestation.dump(cred), encoding="utf-8")
    pub = attestation.public_key_multibase(key.public_key())
    from mandat.cli import build_parser

    args = build_parser().parse_args(["attest-verify", str(path), "--public-key", pub])
    code = args.func(args)
    err = capsys.readouterr().err
    assert code != 0
    assert "принимается" not in err and "принимается" not in capsys.readouterr().out



def test_cli_attest_verify_no_pass_print_before_exit_2(tmp_path, monkeypatch, capsys):
    """Stage nit: «подпись верна» must not appear before exit 2/4 failures."""
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path, revoke=True)
    path = tmp_path / "cred.json"
    path.write_text(attestation.dump(cred), encoding="utf-8")
    pub = attestation.public_key_multibase(key.public_key())
    from mandat.cli import build_parser

    args = build_parser().parse_args(["attest-verify", str(path), "--public-key", pub])
    code = args.func(args)
    captured = capsys.readouterr()
    assert code == 2
    combined = captured.out + captured.err
    assert "подпись верна" not in combined
    assert "нужен --status-dir" in captured.err



def test_cli_crypto_only_never_says_accepted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key, cred, sd, _ = _issued(tmp_path, revoke=True)
    path = tmp_path / "cred.json"
    path.write_text(attestation.dump(cred), encoding="utf-8")
    pub = attestation.public_key_multibase(key.public_key())
    from mandat.cli import build_parser

    args = build_parser().parse_args(["attest-verify", str(path), "--public-key", pub, "--crypto-only"])
    code = args.func(args)
    out = capsys.readouterr()
    combined = out.out + out.err
    assert code == 0
    assert "принимается" not in combined
    assert "действует" not in combined
    assert "криптограф" in combined.lower() or "криптография" in combined or "только криптография" in combined


def test_demo_allow_missing_status_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    key, cred, sd, _ = _issued(tmp_path)
    assert (
        attestation.validate_attestation(cred, [key.public_key()], now=NOW + timedelta(minutes=1), require_status=False)
        == []
    )
    path = tmp_path / "cred.json"
    path.write_text(attestation.dump(cred), encoding="utf-8")
    pub = attestation.public_key_multibase(key.public_key())
    from mandat.cli import build_parser

    args = build_parser().parse_args(["attest-verify", str(path), "--public-key", pub])
    code = args.func(args)
    captured = capsys.readouterr()
    assert code == 0, captured
    assert "принимается" not in captured.out
    assert "статус не проверялся" in captured.err


def test_authorize_operation_rejects_refuted_even_with_valid_status(tmp_path, monkeypatch):
    """Murder Board #8 / M-04: status+refuted ≠ allow."""
    monkeypatch.setenv("MANDAT_ENV", "prod")
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "refuted", "poa_numbers": ["poc"]})
    cred = attestation.issue(_report(verdict="refuted"), key, now=NOW, status=registry.credential_status(idx))
    registry.save(key, now=NOW)
    crypto = attestation.validate_attestation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd, require_status=True
    )
    assert crypto == [], crypto  # квитанция подлинна даже при refuted
    auth = attestation.authorize_operation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=sd, seen={}
    )
    assert any("confirmed" in p or "вердикт" in p for p in auth)
