"""M-12: срок жизни и отзыв токенов API (expires_at / revoked), отзыв без рестарта, fail closed."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mandat import cli
from mandat.api import principals
from mandat.api.app import create_app
from mandat.api.principals import (
    MAX_TOKEN_TTL_DAYS,
    Directory,
    access_problem,
    hash_token,
    load_users,
    parse_users,
)

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
TOKEN = "t" * 32


def _in_days(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


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
    assert parse_users([_entry(expires_at=_in_days(30))])


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
    assert client.get("/v1/cases/x", headers=headers).status_code == 401
    path.write_text(good + "\n", encoding="utf-8")
    assert client.get("/v1/cases/x", headers=headers).status_code == 404


def test_deleted_users_file_fails_closed(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps([_entry()]), encoding="utf-8")
    client = TestClient(create_app(users=str(path)))
    path.unlink()
    assert client.get("/v1/cases/x", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401


def test_users_file_in_protected_mode_without_expiry_fails_closed_on_reload(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    path.write_text(json.dumps([_entry(expires_at=future)]), encoding="utf-8")
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    client = TestClient(create_app(users=str(path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/v1/cases/x", headers=headers).status_code == 404
    path.write_text(json.dumps([_entry()]) + "\n", encoding="utf-8")  # забыли expires_at
    assert client.get("/v1/cases/x", headers=headers).status_code == 401


def test_legacy_token_unaffected_and_ignored_with_users_file(tmp_path):
    legacy = TestClient(create_app(token=TOKEN))
    assert legacy.get("/v1/cases/x", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    path = tmp_path / "users.json"
    path.write_text("[]", encoding="utf-8")
    # пустой файл со списком: общий токен не оживает
    client = TestClient(create_app(token=TOKEN, users=str(path)))
    assert client.get("/v1/cases/x", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401


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


# ---------- добавлено по ревью ----------


def test_protected_expires_at_null_counts_as_missing(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    with pytest.raises(ValueError, match="expires_at"):
        parse_users([_entry(expires_at=None)])
    monkeypatch.setenv("MANDAT_ENV", "demo")
    (entry,) = parse_users([_entry(expires_at=None)])
    assert entry.expires_at is None  # в демо null = без срока


def test_protected_far_future_expiry_is_refused(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    with pytest.raises(ValueError, match="потолок"):
        parse_users([_entry(expires_at="9999-12-31T23:59:59Z")])
    with pytest.raises(ValueError, match="потолок"):
        parse_users([_entry(expires_at=_in_days(MAX_TOKEN_TTL_DAYS + 1))])
    assert parse_users([_entry(expires_at=_in_days(MAX_TOKEN_TTL_DAYS - 1))])


def test_protected_ceiling_boundary_with_fixed_now(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    edge = T0 + timedelta(days=MAX_TOKEN_TTL_DAYS)
    assert parse_users([_entry(expires_at=edge.isoformat())], now=T0)  # ровно потолок — можно
    with pytest.raises(ValueError):
        parse_users([_entry(expires_at=(edge + timedelta(microseconds=1)).isoformat())], now=T0)


def test_revoked_entry_far_future_does_not_block_the_list(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    users = parse_users(
        [_entry("a", revoked=True, expires_at="9999-12-31T23:59:59Z"), _entry("b", "b" * 32, expires_at=_in_days(5))]
    )
    assert len(users) == 2


def test_demo_far_future_only_warns(monkeypatch, caplog):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    with caplog.at_level("WARNING"):
        assert parse_users([_entry(expires_at="9999-12-31T23:59:59Z")])
    assert any("позже" in r.message for r in caplog.records)


def test_boundary_microsecond_and_no_grace_for_clock_skew():
    exp = T0 + timedelta(hours=1)
    entries = [_entry(expires_at=exp.isoformat())]
    assert _dir(entries, exp - timedelta(microseconds=1)).find(TOKEN) is not None
    assert _dir(entries, exp).find(TOKEN) is None
    # часы сервера на минуту позже срока: допуска нет, токен просрочен
    assert _dir(entries, exp + timedelta(minutes=1)).find(TOKEN) is None
    # часы отстали: токен ещё действует (часы сервера считаются точными, это зафиксировано в docs/KEYS.md)
    assert _dir(entries, exp - timedelta(hours=2)).find(TOKEN) is not None


def test_clock_is_read_on_every_request():
    now = [T0]
    d = Directory(
        parse_users([_entry(expires_at=(T0 + timedelta(minutes=1)).isoformat())], require_expiry=False),
        clock=lambda: now[0],
    )
    assert d.find(TOKEN) is not None
    now[0] = T0 + timedelta(minutes=1)
    assert d.find(TOKEN) is None


def _same_size_pair(tmp_path):
    future = _in_days(10)
    live = json.dumps([_entry(expires_at=future, revoked=False)])
    dead = json.dumps([_entry(expires_at=future, revoked=True)])
    # подгоняем длину пробелами в конце: размер файла совпадает
    width = max(len(live), len(dead))
    return live.ljust(width), dead.ljust(width)


def test_same_size_in_place_edit_with_preserved_mtime_is_noticed(tmp_path):
    live, dead = _same_size_pair(tmp_path)
    path = tmp_path / "users.json"
    path.write_text(live, encoding="utf-8")
    d = Directory(load_users(str(path)), reload_path=path)
    assert d.find(TOKEN) is not None
    before = path.stat()
    path.write_text(dead, encoding="utf-8")  # тот же inode, тот же размер
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # mtime «не менялся»
    assert path.stat().st_size == before.st_size and path.stat().st_ino == before.st_ino
    assert d.find(TOKEN) is None


def test_content_hash_catches_edit_even_if_every_stat_field_is_equal(tmp_path, monkeypatch):
    live, dead = _same_size_pair(tmp_path)
    path = tmp_path / "users.json"
    path.write_text(live, encoding="utf-8")
    d = Directory(load_users(str(path)), reload_path=path)
    assert d.find(TOKEN) is not None
    frozen = d._file_stamp()
    monkeypatch.setattr(d, "_file_stamp", lambda: frozen)  # метка не меняется вовсе
    path.write_text(dead, encoding="utf-8")
    assert d.find(TOKEN) is None


def test_atomic_rename_replacement_is_noticed(tmp_path):
    live, dead = _same_size_pair(tmp_path)
    path = tmp_path / "users.json"
    path.write_text(live, encoding="utf-8")
    d = Directory(load_users(str(path)), reload_path=path)
    assert d.find(TOKEN) is not None
    tmp = tmp_path / "users.json.tmp"
    tmp.write_text(dead, encoding="utf-8")
    os.replace(tmp, path)
    assert d.find(TOKEN) is None


def test_unchanged_old_file_is_not_reparsed(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    path.write_text(json.dumps([_entry(expires_at=_in_days(10))]), encoding="utf-8")
    old = path.stat().st_mtime_ns - 10 * 10**9
    os.utime(path, ns=(old, old))
    d = Directory(load_users(str(path)), reload_path=path)
    assert d.find(TOKEN) is not None  # первый запрос перечитывает файл
    calls = []
    real = principals.parse_users
    monkeypatch.setattr(principals, "parse_users", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(5):
        assert d.find(TOKEN) is not None
    assert calls == []


def test_concurrent_requests_after_file_change_parse_once_and_never_see_revoked(tmp_path, monkeypatch):
    live, dead = _same_size_pair(tmp_path)
    path = tmp_path / "users.json"
    path.write_text(live, encoding="utf-8")
    d = Directory(load_users(str(path)), reload_path=path)
    assert d.find(TOKEN) is not None
    # дать файлу «состариться», чтобы окно RECENT не вызывало повторного разбора
    old = path.stat().st_mtime_ns - 10 * 10**9
    os.utime(path, ns=(old, old))
    assert d.find(TOKEN) is not None
    tmp = tmp_path / "users.json.tmp"
    tmp.write_text(dead, encoding="utf-8")
    os.utime(tmp, ns=(old + 1, old + 1))
    os.replace(tmp, path)
    old2 = path.stat().st_mtime_ns - 10 * 10**9
    os.utime(path, ns=(old2, old2))
    calls = []
    real = principals.parse_users

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(principals, "parse_users", counting)
    barrier = threading.Barrier(16)
    results: list[object] = []
    errors: list[BaseException] = []

    def go() -> None:
        try:
            barrier.wait()
            results.append(d.find(TOKEN))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert results == [None] * 16
    assert len(calls) == 1


def test_concurrent_toggle_never_serves_revoked_token_after_revocation(tmp_path):
    live, dead = _same_size_pair(tmp_path)
    path = tmp_path / "users.json"
    path.write_text(live, encoding="utf-8")
    d = Directory(load_users(str(path)), reload_path=path)
    stop = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                d.find(TOKEN)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for _ in range(20):
        tmp = tmp_path / "u.tmp"
        tmp.write_text(dead, encoding="utf-8")
        os.replace(tmp, path)
        assert d.find(TOKEN) is None  # сразу после отзыва — ни одного допуска
        tmp.write_text(live, encoding="utf-8")
        os.replace(tmp, path)
        assert d.find(TOKEN) is not None
    stop.set()
    for t in threads:
        t.join()
    assert errors == []


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "0", "-5", "1e9", "367"])
def test_api_user_cli_rejects_nonfinite_and_out_of_range(bad, capsys, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert cli.main(["api-user", "--name", "x", "--role", "operator", f"--expires-in-days={bad}"]) == 2
    assert "--expires-in-days" in capsys.readouterr().err


def test_api_user_cli_accepts_exact_maximum(capsys, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    assert cli.main(["api-user", "--name", "x", "--role", "operator", f"--expires-in-days={MAX_TOKEN_TTL_DAYS}"]) == 0
    entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert parse_users([entry])  # выданная запись проходит потолок в pilot (с запасом в секунды округления)
