"""Заключения юриста в хранилищах: запись по ожидаемой версии, указатель по ОГРН, шифр и счётчики применений.

Заключения переживают рестарт так же, как разборы: SQLite и Redis держат их вне процесса, а новый экземпляр
хранилища видит те же записи и версии.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mandat.api.sealing import SealedStore, Sealer, SealError
from mandat.api.storage import ConclusionVersionConflict, MemoryStore, RedisStore, SQLiteStore, Store

OGRN, OTHER = "1257700846061", "1027700132195"
BASIS = "Иванова-Петрова 112-233-445 95: ограничений на платежи нет"
KEY = "store-key-" + "s" * 32


def _body(cid: str, ogrn: str = OGRN, **over) -> dict:
    return {"id": cid, "ogrn": ogrn, "status": "draft", "basis": BASIS, **over}


@pytest.fixture(params=["memory", "sqlite", "redis", "sealed-sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Store:
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    if request.param == "sealed-sqlite":
        return SealedStore(SQLiteStore(tmp_path / "api.db"), Sealer(KEY))
    redis: RedisStore = request.getfixturevalue("redis_store")
    return redis


def test_put_get_versions_and_conflicts(store: Store) -> None:
    assert store.conclusion_get("cnc-a") is None
    assert store.conclusion_put("cnc-a", _body("cnc-a"), 0) == 1
    assert store.conclusion_get("cnc-a") == (_body("cnc-a"), 1)
    assert store.conclusion_put("cnc-a", _body("cnc-a", status="approved"), 1) == 2
    for stale in (0, 1, 5):
        with pytest.raises(ConclusionVersionConflict) as info:
            store.conclusion_put("cnc-a", _body("cnc-a", status="revoked"), stale)
        assert (info.value.expected, info.value.current) == (stale, 2)
    assert store.conclusion_get("cnc-a") == (_body("cnc-a", status="approved"), 2)
    with pytest.raises(ConclusionVersionConflict):
        store.conclusion_put("cnc-new", _body("cnc-new"), 1)
    assert store.conclusion_get("cnc-new") is None


def test_list_by_ogrn_and_the_ogrn_never_changes(store: Store) -> None:
    store.conclusion_put("cnc-b", _body("cnc-b"), 0)
    store.conclusion_put("cnc-a", _body("cnc-a"), 0)
    store.conclusion_put("cnc-c", _body("cnc-c", OTHER), 0)
    assert [(cid, version) for cid, _, version in store.conclusion_list(OGRN)] == [("cnc-a", 1), ("cnc-b", 1)]
    assert [cid for cid, _, _ in store.conclusion_list(OTHER)] == ["cnc-c"]
    assert store.conclusion_list("1234567890123") == []
    with pytest.raises(ValueError, match="ОГРН"):
        store.conclusion_put("cnc-a", _body("cnc-a", OTHER), 1)
    with pytest.raises(ValueError, match="ОГРН"):
        store.conclusion_put("cnc-d", {"id": "cnc-d"}, 0)
    assert [cid for cid, _, _ in store.conclusion_list(OGRN)] == ["cnc-a", "cnc-b"]


def test_returned_record_is_a_copy(store: Store) -> None:
    body = _body("cnc-a", history=[{"action": "create"}])
    store.conclusion_put("cnc-a", body, 0)
    body["history"].append({"action": "approve"})
    got = store.conclusion_get("cnc-a")
    assert got is not None and got[0]["history"] == [{"action": "create"}]
    got[0]["history"].clear()
    assert store.conclusion_get("cnc-a")[0]["history"] == [{"action": "create"}]  # type: ignore[index]


def test_parallel_writes_of_one_version_exactly_one_wins(store: Store) -> None:
    store.conclusion_put("cnc-a", _body("cnc-a"), 0)
    barrier, wins, lock = threading.Barrier(6), [], threading.Lock()

    def write(i: int) -> None:
        barrier.wait()
        try:
            store.conclusion_put("cnc-a", _body("cnc-a", writer=i), 1)
        except ConclusionVersionConflict:
            return
        with lock:
            wins.append(i)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    assert store.conclusion_get("cnc-a") == (_body("cnc-a", writer=wins[0]), 2)


def test_application_tallies(store: Store) -> None:
    assert store.conclusion_tallies(["cnc-a", "cnc-b"]) == {"cnc-a": (0, 0), "cnc-b": (0, 0)}
    store.conclusion_tally("cnc-a", True)
    store.conclusion_tally("cnc-a", False)
    store.conclusion_tally("cnc-b", True)
    assert store.conclusion_tallies(["cnc-a", "cnc-b"]) == {"cnc-a": (2, 1), "cnc-b": (1, 1)}
    assert store.conclusion_tallies([]) == {}


def test_sqlite_conclusions_survive_a_restart(tmp_path: Path) -> None:
    first = SQLiteStore(tmp_path / "api.db")
    first.conclusion_put("cnc-a", _body("cnc-a"), 0)
    first.conclusion_tally("cnc-a", True)
    again = SQLiteStore(tmp_path / "api.db")
    assert again.conclusion_get("cnc-a") == (_body("cnc-a"), 1)
    assert [cid for cid, _, _ in again.conclusion_list(OGRN)] == ["cnc-a"]
    assert again.conclusion_tallies(["cnc-a"]) == {"cnc-a": (1, 1)}


def test_redis_conclusions_survive_a_new_store_instance(redis_store: RedisStore) -> None:
    redis_store.conclusion_put("cnc-a", _body("cnc-a"), 0)
    redis_store.conclusion_tally("cnc-a", False)
    again = RedisStore(redis_store.r, prefix=redis_store.prefix)
    assert again.conclusion_get("cnc-a") == (_body("cnc-a"), 1)
    assert [cid for cid, _, _ in again.conclusion_list(OGRN)] == ["cnc-a"]
    assert again.conclusion_tallies(["cnc-a"]) == {"cnc-a": (1, 0)}


def test_redis_index_of_another_organization_does_not_leak_a_record(redis_store: RedisStore) -> None:
    redis_store.conclusion_put("cnc-a", _body("cnc-a"), 0)
    redis_store.r.sadd(f"{redis_store.prefix}conclusions:{OTHER}", "cnc-a")
    assert redis_store.conclusion_list(OTHER) == []


def test_sealed_conclusion_is_unreadable_in_the_inner_store_and_bound_to_its_place() -> None:
    inner = MemoryStore()
    store = SealedStore(inner, Sealer(KEY))
    store.conclusion_put("cnc-a", _body("cnc-a"), 0)
    store.conclusion_put("cnc-b", _body("cnc-b", status="approved"), 0)
    assert store.conclusion_get("cnc-a") == (_body("cnc-a"), 1)
    raw = json.dumps(inner.conclusion_get("cnc-a"), ensure_ascii=False)
    assert "Иванова" not in raw and "112-233-445" not in raw and inner.conclusion_get("cnc-a")[0]["ogrn"] == OGRN
    # Заключение cnc-b под номером cnc-a: шифр цел, но привязан к другому номеру.
    inner._conclusions["cnc-a"] = (inner.conclusion_get("cnc-b")[0], 1)
    with pytest.raises(SealError):
        store.conclusion_get("cnc-a")
    # ОГРН рядом с шифром переписан: указатель другой организации запись не открывает.
    envelope = inner.conclusion_get("cnc-b")[0]
    inner._conclusions["cnc-b"] = ({**envelope, "ogrn": OTHER}, 1)
    with pytest.raises(SealError):
        store.conclusion_list(OTHER)
    with pytest.raises(SealError):
        store.conclusion_get("cnc-b")
    inner._conclusions["cnc-b"] = (_body("cnc-b", status="approved"), 1)
    with pytest.raises(SealError, match="не зашифрована"):
        store.conclusion_get("cnc-b")
    store.conclusion_tally("cnc-b", True)
    assert store.conclusion_tallies(["cnc-b"]) == inner.conclusion_tallies(["cnc-b"]) == {"cnc-b": (1, 1)}


def test_sealed_version_is_part_of_the_binding() -> None:
    inner = MemoryStore()
    store = SealedStore(inner, Sealer(KEY))
    store.conclusion_put("cnc-a", _body("cnc-a"), 0)
    first = inner.conclusion_get("cnc-a")[0]
    store.conclusion_put("cnc-a", _body("cnc-a", status="revoked"), 1)
    # Откат к прежней записи под новым номером версии: отзыв нельзя тихо отменить подменой записи.
    inner._conclusions["cnc-a"] = (first, 2)
    with pytest.raises(SealError):
        store.conclusion_get("cnc-a")
