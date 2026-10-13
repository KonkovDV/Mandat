"""Хранилища API: одинаковое поведение в памяти, в SQLite и в Redis, общее состояние между воркерами."""

from __future__ import annotations

import multiprocessing as mp

import pytest

from mandat.api.storage import MemoryStore, RedisStore, SQLiteStore, open_store


@pytest.fixture(params=["memory", "sqlite", "redis"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("lupa")
    return RedisStore(fakeredis.FakeRedis())


def test_sliding_window(store):
    assert [store.allow("a", 2, now=100.0) for _ in range(3)] == [True, True, False]
    assert store.allow("b", 2, now=100.0)
    assert not store.allow("a", 2, now=159.0)
    assert store.allow("a", 2, now=160.5)


def test_window_boundary_is_the_same_everywhere(store):
    assert store.allow("edge", 1, now=1000.0)
    assert not store.allow("edge", 1, now=1060.0)
    assert store.allow("edge", 1, now=1060.001)


def test_reports_owner_replace_and_eviction(store):
    store.put("c1", {"v": 1}, "anna", 2)
    store.put("c2", {"v": 2}, "boris", 2)
    assert store.get("c1") == ({"v": 1}, "anna")
    store.put("c1", {"v": 3}, "anna", 2)  # перезапись делает кейс самым свежим
    store.put("c3", {"v": 4}, "anna", 2)
    assert store.get("c2") is None
    assert store.get("c1") == ({"v": 3}, "anna")
    assert store.get("c3") == ({"v": 4}, "anna")
    assert store.get("missing") is None


def test_first_seen_once_per_ttl(store):
    assert store.first_seen("k", 10, now=1000.0)
    assert not store.first_seen("k", 10, now=1005.0)
    if not isinstance(store, RedisStore):  # у Redis срок считает сам сервер
        assert store.first_seen("k", 10, now=1011.0)


def test_sqlite_is_shared_between_instances(tmp_path):
    a, b = SQLiteStore(tmp_path / "s.db"), SQLiteStore(tmp_path / "s.db")
    a.put("x", {"ok": True}, "anna", 10)
    assert b.get("x") == ({"ok": True}, "anna")
    assert a.allow("ip", 1, now=1.0)
    assert not b.allow("ip", 1, now=2.0)


def _hammer(path, results):
    s = SQLiteStore(path)
    results.put(sum(s.allow("same", 25, now=500.0) for _ in range(20)))


def test_sqlite_limit_holds_across_processes(tmp_path):
    path = str(tmp_path / "p.db")
    SQLiteStore(path)
    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    workers = [ctx.Process(target=_hammer, args=(path, results)) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
    assert sum(results.get(timeout=5) for _ in workers) == 25


def test_open_store_specs(tmp_path):
    assert open_store("").name == "memory"
    assert open_store(f"sqlite:///{tmp_path / 'o.db'}").name == "sqlite"
    with pytest.raises(ValueError):
        open_store("postgres://nope")


def test_api_reports_survive_between_app_instances(tmp_path):
    pytest.importorskip("fastapi")
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    manifest = json.loads(Path("examples/cases/01_ok/case.json").read_text(encoding="utf-8"))
    manifest["documents"] = []
    db = tmp_path / "shared.db"
    one = TestClient(create_app("t", rate_per_min=0, store=SQLiteStore(db)))
    two = TestClient(create_app("t", rate_per_min=0, store=SQLiteStore(db)))
    h = {"Authorization": "Bearer t"}
    assert one.post("/v1/cases", json=manifest, headers=h).status_code == 200
    assert two.get(f"/v1/cases/{manifest['case_id']}", headers=h).status_code == 200
    assert two.post("/v1/cases", json=manifest, headers=h).status_code == 409
    assert two.get("/health").json()["store"] == "sqlite"


def test_api_rate_limit_is_shared(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    db = tmp_path / "rl.db"
    one = TestClient(create_app("t", rate_per_min=2, store=SQLiteStore(db)))
    two = TestClient(create_app("t", rate_per_min=2, store=SQLiteStore(db)))
    h = {"Authorization": "Bearer t"}
    codes = [one.get("/v1/cases/a", headers=h).status_code, two.get("/v1/cases/a", headers=h).status_code]
    assert codes == [404, 404]
    assert one.get("/v1/cases/a", headers=h).status_code == 429


def test_serve_refuses_workers_without_shared_store(monkeypatch, capsys):
    from mandat import cli

    monkeypatch.setenv("MANDAT_API_TOKEN", "t")
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.delenv("MANDAT_API_STORE", raising=False)
    pytest.importorskip("uvicorn")
    assert cli.main(["serve", "--workers", "2"]) == 2
    assert "MANDAT_API_STORE" in capsys.readouterr().err
