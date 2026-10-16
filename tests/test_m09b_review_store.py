"""M-09b: состояние разбора в хранилищах с CAS по версии (Memory, SQLite, Redis)."""

from __future__ import annotations

import multiprocessing as mp
import threading
from pathlib import Path

import pytest

from mandat.api.storage import MemoryStore, RedisStore, ReviewVersionConflict, SQLiteStore, Store

N = 8


@pytest.fixture(params=["memory", "sqlite", "redis"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Store:
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    store: RedisStore = request.getfixturevalue("redis_store")
    return store


def test_review_absent(store: Store) -> None:
    assert store.review_get("c1") is None


def test_review_put_get_and_versions(store: Store) -> None:
    assert store.review_put("c1", {"state": "queued"}, 0) == 1
    assert store.review_get("c1") == ({"state": "queued"}, 1)
    assert store.review_put("c1", {"state": "in_review"}, 1) == 2
    assert store.review_get("c1") == ({"state": "in_review"}, 2)
    assert store.review_get("c2") is None


def test_stale_version_conflicts_and_keeps_state(store: Store) -> None:
    store.review_put("c1", {"state": "queued"}, 0)
    for stale in (0, 2, 5):
        with pytest.raises(ReviewVersionConflict) as info:
            store.review_put("c1", {"state": "accepted"}, stale)
        assert (info.value.expected, info.value.current) == (stale, 1)
    assert store.review_get("c1") == ({"state": "queued"}, 1)


def test_create_requires_version_zero(store: Store) -> None:
    with pytest.raises(ReviewVersionConflict):
        store.review_put("new", {"state": "queued"}, 1)
    assert store.review_get("new") is None


def test_returned_state_is_a_copy(store: Store) -> None:
    state = {"history": [{"action": "appeal"}]}
    store.review_put("c1", state, 0)
    state["history"].append({"action": "open"})
    got = store.review_get("c1")
    assert got is not None
    got[0]["history"].clear()
    assert store.review_get("c1") == ({"history": [{"action": "appeal"}]}, 1)


@pytest.mark.parametrize("expected", [0, 1])
def test_parallel_writes_same_version_exactly_one_wins(store: Store, expected: int) -> None:
    if expected:
        store.review_put("c1", {"writer": "init"}, 0)
    barrier = threading.Barrier(N)
    wins: list[int] = []
    conflicts: list[ReviewVersionConflict] = []
    lock = threading.Lock()

    def write(i: int) -> None:
        barrier.wait()
        try:
            store.review_put("c1", {"writer": i}, expected)
        except ReviewVersionConflict as exc:
            with lock:
                conflicts.append(exc)
        else:
            with lock:
                wins.append(i)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    assert len(conflicts) == N - 1
    assert all(c.current == expected + 1 for c in conflicts)
    assert store.review_get("c1") == ({"writer": wins[0]}, expected + 1)


def _proc_write(path: str, i: int, barrier: object, out: object) -> None:
    store = SQLiteStore(path)
    barrier.wait()  # type: ignore[attr-defined]
    try:
        store.review_put("c1", {"writer": i}, 0)
    except ReviewVersionConflict:
        out.put("conflict")  # type: ignore[attr-defined]
    else:
        out.put("win")  # type: ignore[attr-defined]


def test_sqlite_parallel_processes_exactly_one_wins(tmp_path: Path) -> None:
    path = str(tmp_path / "api.db")
    SQLiteStore(path)
    ctx = mp.get_context("spawn")
    barrier, out = ctx.Barrier(N), ctx.Queue()
    procs = [ctx.Process(target=_proc_write, args=(path, i, barrier, out)) for i in range(N)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    results = sorted(out.get(timeout=5) for _ in range(N))
    assert results.count("win") == 1
    assert results.count("conflict") == N - 1
    got = SQLiteStore(path).review_get("c1")
    assert got is not None and got[1] == 1
