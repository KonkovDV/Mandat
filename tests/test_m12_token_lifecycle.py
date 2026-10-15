"""M-12: срок жизни и отзыв токенов API (expires_at / revoked), отзыв без рестарта, fail closed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mandat import cli
from mandat.api.app import create_app
from mandat.api.principals import Directory, access_problem, hash_token, load_users, parse_users

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
TOKEN = "t" * 32


def _entry(name="a", token=TOKEN, role="operator", **extra):
    return {"name": name, "role": role, "token_sha256": hash_token(token), **extra}


def _dir(entries, now=T0, **kw):
    return Directory(parse_users(entries, require_expiry=False), clock=lambda: now, **kw)


def test_not_expired_before_boundary_expired_at_and_after():
    exp = T0 + timedelta(hours=1)
    entries = [_entry(expires_at=exp.isoformat())]
    assert _dir(entries, exp - timedelta(seconds=1)).find(TOKEN) is not None
    assert _dir(entries, exp).find(TOKEN) is None  # граница: уже не действует
    assert _dir(entries, exp + timedelta(days=1)).find(TOKEN) is None


def test_revoked_rejected_even_with_future_expiry():
    entries = [_entry(revoked=True, expires_at=(T0 + timedelta(days=30)).isoformat())]
    assert _dir(entries).find(TOKEN) is None


def test_revoked_false_and_no_expiry_ok_in_demo():
    assert _dir([_entry(revoked=False)]).find(TOKEN) is not None
    assert _dir([_entry()]).find(TOKEN) is not None


def test_revoking_one_user_keeps_the_other():
    other = "o" * 32
    d = _dir([_entry("a", revoked=True), _entry("b", other)])
    assert d.find(TOKEN) is None
    assert d.find(other).name == "b"


def test_expiry_timezone_is_normalised():
    # 15:00+03:00 == 12:00Z
    d = _dir([_entry(expires_at="2026-10-15T15:00:00+03:00")], now=T0)
    assert d.find(TOKEN) is None
    d = _dir([_entry(expires_at="2026-10-15T15:00:00+03:00")], now=T0 - timedelta(seconds=1))
    assert d.find(TOKEN) is not None
    assert _dir([_entry(expires_at="2026-10-15T12:00:01Z")]).find(TOKEN) is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"expires_at": "2026-10-15T12:00:00"},  # без пояса
        {"expires_at": "завтра"},
        {"expires_at": 12345},
        {"expires_at": ""},
        {"revoked": "false"},  # строка "false" не должна читаться как «не отозван»
        {"revoked": 0},
        {"revoked": None},
    ],
)
def test_malformed_lifecycle_fields_refuse_to_load(bad):
    with pytest.raises(ValueError):
        parse_users([_entry(**bad)], require_expiry=False)


def test_protected_requires_expires_at(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    with pytest.raises(ValueError, match="expires_at"):
        parse_users([_entry()])
    assert parse_users([_entry(expires_at="2030-01-01T00:00:00Z")])


def test_demo_does_not_require_expires_at(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert parse_users([_entry()])


def test_serve_refuses_pilot_users_without_expiry(monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.delenv("MANDAT_API_TOKEN", raising=False)
    monkeypatch.setenv("MANDAT_API_USERS", json.dumps([_entry()]))
    assert "expires_at" in (access_problem() or "")
    assert cli.main(["serve"]) == 2
    assert "expires_at" in capsys.readouterr().err


def test_http_expired_and_revoked_tokens_get_401():
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    users = json.dumps(
        [
            _entry("old", "e" * 32, expires_at=past),
            _entry("gone", "r" * 32, revoked=True, expires_at=future),
            _entry("live", "l" * 32, expires_at=future),
        ]
    )
    client = TestClient(create_app(users=users, store=None))
    for tok in ("e" * 32, "r" * 32):
        assert client.get("/v1/cases/x", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        assert client.post("/v1/cases", json={}, headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert client.get("/v1/cases/x", headers={"Authorization": f"Bearer {'l' * 32}"}).status_code == 404


def test_revocation_in_file_takes_effect_without_restart(tmp_path):
    path = tmp_path / "users.json"
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    path.write_text(json.dumps([_entry(expires_at=future)]), encoding="utf-8")
    client = TestClient(create_app(users=str(path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/v1/cases/x", headers=headers).status_code == 404
    path.write_text(json.dumps([_entry(expires_at=future, revoked=True)]) + " ", encoding="utf-8")
    assert client.get("/v1/cases/x", headers=headers).status_code == 401


def test_unreadable_users_file_fails_closed_then_recovers(tmp_path):
    path = tmp_path / "users.json"
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    good = json.dumps([_entry(expires_at=future)])
    path.write_text(good, encoding="utf-8")
    client = TestClient(create_app(users=str(path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/v1/cases/x", headers=headers).status_code == 404
    path.write_text("{ not json ", encoding="utf-8")
    assert client.get("/v1/cases/x", headers=headers).status_code in (401, 503)
    path.write_text(good + "\n", encoding="utf-8")
    assert client.get("/v1/cases/x", headers=headers).status_code == 404


def test_deleted_users_file_fails_closed(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps([_entry()]), encoding="utf-8")
    client = TestClient(create_app(users=str(path)))
    path.unlink()
    assert client.get("/v1/cases/x", headers={"Authorization": f"Bearer {TOKEN}"}).status_code in (401, 503)


def test_users_file_in_protected_mode_without_expiry_fails_closed_on_reload(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    path.write_text(json.dumps([_entry(expires_at=future)]), encoding="utf-8")
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    client = TestClient(create_app(users=str(path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/v1/cases/x", headers=headers).status_code == 404
    path.write_text(json.dumps([_entry()]) + "\n", encoding="utf-8")  # забыли expires_at
    assert client.get("/v1/cases/x", headers=headers).status_code in (401, 503)


def test_legacy_token_unaffected_and_ignored_with_users_file(tmp_path):
    legacy = TestClient(create_app(token=TOKEN))
    assert legacy.get("/v1/cases/x", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    path = tmp_path / "users.json"
    path.write_text(json.dumps([_entry("u", "u" * 32)]), encoding="utf-8")
    path.write_text("[]", encoding="utf-8")
    # пустой файл со списком: общий токен не оживает
    client = TestClient(create_app(token=TOKEN, users=str(path)))
    assert client.get("/v1/cases/x", headers={"Authorization": f"Bearer {TOKEN}"}).status_code in (401, 503)


def test_load_users_keeps_lifecycle_fields(tmp_path):
    path = tmp_path / "u.json"
    path.write_text(json.dumps([_entry(expires_at="2030-01-01T00:00:00Z", revoked=True)]), encoding="utf-8")
    (entry,) = load_users(str(path))
    assert entry.revoked and entry.expires_at == datetime(2030, 1, 1, tzinfo=UTC)


def test_api_user_cli_emits_expiry(capsys, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert cli.main(["api-user", "--name", "x", "--role", "operator", "--expires-in-days", "30"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    entry = json.loads(line)
    exp = datetime.fromisoformat(entry["expires_at"])
    assert timedelta(days=29) < exp - datetime.now(UTC) <= timedelta(days=30, seconds=5)
    assert parse_users([entry], require_expiry=True)


def test_api_user_cli_pilot_requires_expiry_and_rejects_nonpositive(capsys, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    assert cli.main(["api-user", "--name", "x", "--role", "operator"]) == 2
    assert cli.main(["api-user", "--name", "x", "--role", "operator", "--expires-in-days", "0"]) == 2
    assert cli.main(["api-user", "--name", "x", "--role", "operator", "--expires-in-days", "-1"]) == 2
