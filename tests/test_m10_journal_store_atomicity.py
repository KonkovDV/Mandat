"""M-10: stage → журнал → commit, сверка после сбоя, Idempotency-Key, fencing.

Это не распределённая транзакция и не exactly-once: тесты проверяют границы (сбой между шагами,
повтор, вытеснение), а не доказывают отсутствие дублей в любом сценарии.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as app_module
from mandat.api.app import create_app
from mandat.api.storage import Idem, MemoryStore, RedisStore, SQLiteStore
from mandat.audit import journal
from mandat.eval import synth

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.delenv("MANDAT_API_JOURNAL", raising=False)


class Crash(BaseException):
    """Смерть процесса: не ловится ни одним ``except Exception``."""


def _manifest(seed: int = 1) -> dict:
    return synth.build("ok", seed).inline_manifest()


def _app(store, path: Path):
    return create_app("t", rate_per_min=0, store=store, journal_path=path)


def _client(store, path: Path, *, crash: bool = False) -> TestClient:
    # crash=True: «смерть процесса» (BaseException) должна дойти до теста, а не стать 500
    return TestClient(_app(store, path), raise_server_exceptions=crash)


def _verify_records(path: Path, case_id: str) -> list[dict]:
    if not path.exists():
        return []
    return [r for r in journal.read_journal(path) if r["action"] == "verify" and r["case_id"] == case_id]


@pytest.fixture(params=["memory", "sqlite", "redis"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    import fakeredis
    import lupa  # noqa: F401

    return RedisStore(fakeredis.FakeRedis())


# ---------- слой хранилища (три реализации) ----------


def test_staged_report_is_invisible_until_commit(store):
    store.stage("c1", {"v": 1}, "alice", "A", None)
    assert store.get("c1") is None
    assert store.pending("c1").attempt == "A"
    assert store.commit("c1", "A", 100) is True
    assert store.get("c1") == ({"v": 1}, "alice")
    assert store.pending("c1") is None
    assert store.commit("c1", "A", 100) is False  # повторный commit той же попытки ничего не делает


def test_commit_with_wrong_attempt_is_refused(store):
    store.stage("c1", {"v": 1}, "alice", "A", None)
    assert store.commit("c1", "WRONG", 100) is False
    assert store.get("c1") is None and store.pending("c1") is not None
    assert store.commit("nothing", "A", 100) is False


def test_newer_attempt_fences_older_one(store):
    store.stage("c1", {"v": "old"}, "alice", "A", None)
    store.stage("c1", {"v": "new"}, "alice", "B", None)
    assert store.commit("c1", "A", 100) is False
    assert store.get("c1") is None
    assert store.commit("c1", "B", 100) is True
    assert store.get("c1") == ({"v": "new"}, "alice")


def test_discard_only_matching_attempt(store):
    store.stage("c1", {"v": 1}, "alice", "A", None)
    assert store.discard("c1", "X") is False
    assert store.pending("c1") is not None
    assert store.discard("c1", "A") is True
    assert store.pending("c1") is None and store.get("c1") is None


def test_commit_replaces_existing_report_and_evicts_oldest(store):
    for i, cid in enumerate(["a", "b", "c"]):
        store.stage(cid, {"n": i}, "o", "x", None)
        assert store.commit(cid, "x", 2)
    assert store.get("a") is None and store.get("b") is not None and store.get("c") is not None
    store.stage("b", {"n": 99}, "o", "y", None)
    assert store.get("b") == ({"n": 0 + 1}, "o")  # до commit видна старая версия
    assert store.commit("b", "y", 2)
    assert store.get("b") == ({"n": 99}, "o")


def test_idempotency_record_written_atomically_with_commit(store):
    idem = Idem("alice", "key-1", "fp1")
    store.stage("c1", {"v": 1}, "alice", "A", idem)
    assert store.idem_get("alice", "key-1") is None
    assert store.pending_for_idem("alice", "key-1").case_id == "c1"
    assert store.commit("c1", "A", 100)
    assert store.idem_get("alice", "key-1") == ("c1", "fp1")
    assert store.pending_for_idem("alice", "key-1") is None
    assert store.idem_get("bob", "key-1") is None  # ключ принадлежит владельцу


def test_fenced_attempt_does_not_write_idempotency_record(store):
    store.stage("c1", {"v": "old"}, "alice", "A", Idem("alice", "k", "fp"))
    store.stage("c1", {"v": "new"}, "alice", "B", None)
    assert store.commit("c1", "A", 100) is False
    assert store.idem_get("alice", "k") is None


def test_pending_all_lists_everything_in_flight(store):
    store.stage("c1", {"v": 1}, "a", "A", None, now=1.0)
    store.stage("c2", {"v": 2}, "a", "B", None, now=2.0)
    assert [p.case_id for p in store.pending_all()] == ["c1", "c2"]
    store.commit("c1", "A", 10)
    assert [p.case_id for p in store.pending_all()] == ["c2"]


def test_sqlite_pending_survives_process_restart(tmp_path):
    path = tmp_path / "api.db"
    first = SQLiteStore(path)
    first.stage("c1", {"v": 1}, "alice", "A", Idem("alice", "k", "fp"), now=5.0)
    second = SQLiteStore(path)  # «новый процесс»
    pend = second.pending("c1")
    assert pend and pend.attempt == "A" and pend.idem == Idem("alice", "k", "fp") and pend.staged_at == 5.0
    assert second.commit("c1", "A", 10)
    assert SQLiteStore(path).get("c1") == ({"v": 1}, "alice")


# ---------- API: сбои между шагами ----------


def test_happy_path_one_journal_record_and_visible_report(tmp_path, store):
    path = tmp_path / "j.jsonl"
    client = _client(store, path)
    m = _manifest()
    r = client.post("/v1/cases", json=m, headers=AUTH)
    assert r.status_code == 200
    assert client.get(f"/v1/cases/{m['case_id']}", headers=AUTH).status_code == 200
    (rec,) = _verify_records(path, m["case_id"])
    assert rec["details"]["attempt"]
    assert store.pending(m["case_id"]) is None


def test_crash_after_journal_before_commit_then_restart_commits_without_duplicate(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    store = SQLiteStore(tmp_path / "api.db")

    class CrashOnCommit(SQLiteStore):
        def commit(self, *a, **k):
            raise Crash

    crashing = CrashOnCommit(tmp_path / "api.db")
    m = _manifest()
    with pytest.raises(Crash):
        _client(crashing, path, crash=True).post("/v1/cases", json=m, headers=AUTH)
    assert len(_verify_records(path, m["case_id"])) == 1
    assert store.get(m["case_id"]) is None and store.pending(m["case_id"]) is not None
    # новый процесс: свежие записи не трогаем (их мог писать живой воркер) …
    client = _client(SQLiteStore(tmp_path / "api.db"), path)
    assert client.get(f"/v1/cases/{m['case_id']}", headers=AUTH).status_code == 404
    assert store.pending(m["case_id"]) is not None
    # … а когда заявка устарела, сверка подтверждает отчёт по журналу
    monkeypatch.setattr(app_module, "CREATE_CLAIM_SECONDS", 0.0)
    client = _client(SQLiteStore(tmp_path / "api.db"), path)
    assert client.get(f"/v1/cases/{m['case_id']}", headers=AUTH).status_code == 200
    assert len(_verify_records(path, m["case_id"])) == 1
    assert store.pending(m["case_id"]) is None


def test_crash_after_stage_before_journal_restart_discards(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    store = SQLiteStore(tmp_path / "api.db")
    m = _manifest()

    def boom(*a, **k):
        raise Crash

    monkeypatch.setattr(journal, "append", boom)
    with pytest.raises(Crash):
        _client(store, path, crash=True).post("/v1/cases", json=m, headers=AUTH)
    monkeypatch.undo()
    assert store.pending(m["case_id"]) is not None and _verify_records(path, m["case_id"]) == []
    monkeypatch.setattr(app_module, "CREATE_CLAIM_SECONDS", 0.0)
    client = _client(SQLiteStore(tmp_path / "api.db"), path)
    assert store.pending(m["case_id"]) is None  # отброшена: в журнале попытки нет, клиент 200 не получал
    assert client.get(f"/v1/cases/{m['case_id']}", headers=AUTH).status_code == 404
    assert client.post("/v1/cases", json=m, headers=AUTH).status_code == 200
    assert len(_verify_records(path, m["case_id"])) == 1


def test_retry_without_key_after_crash_between_journal_and_commit(tmp_path):
    path = tmp_path / "j.jsonl"
    m = _manifest()

    class CrashOnCommit(SQLiteStore):
        def commit(self, *a, **k):
            raise Crash

    with pytest.raises(Crash):
        _client(CrashOnCommit(tmp_path / "api.db"), path, crash=True).post("/v1/cases", json=m, headers=AUTH)
    client = _client(SQLiteStore(tmp_path / "api.db"), path)
    # без Idempotency-Key: кейс уже зафиксирован журналом, честный ответ — 409, а не второй verify
    again = client.post("/v1/cases", json=m, headers=AUTH)
    assert again.status_code == 409
    assert len(_verify_records(path, m["case_id"])) == 1
    assert client.get(f"/v1/cases/{m['case_id']}", headers=AUTH).status_code == 200
    # осознанная перезапись даёт вторую запись — и это видно в журнале
    assert client.post("/v1/cases?replace=true", json=m, headers=AUTH).status_code == 200
    assert len(_verify_records(path, m["case_id"])) == 2


def test_idempotent_retry_after_crash_returns_same_report_without_second_journal_record(tmp_path):
    path = tmp_path / "j.jsonl"
    m = _manifest()
    headers = {**AUTH, "Idempotency-Key": "retry-1"}

    class CrashOnCommit(SQLiteStore):
        def commit(self, *a, **k):
            raise Crash

    with pytest.raises(Crash):
        _client(CrashOnCommit(tmp_path / "api.db"), path, crash=True).post("/v1/cases", json=m, headers=headers)
    client = _client(SQLiteStore(tmp_path / "api.db"), path)
    r = client.post("/v1/cases", json=m, headers=headers)
    assert r.status_code == 200 and r.json()["case_id"] == m["case_id"]
    assert len(_verify_records(path, m["case_id"])) == 1
    again = client.post("/v1/cases", json=m, headers=headers)
    assert again.status_code == 200 and again.json() == r.json()
    assert len(_verify_records(path, m["case_id"])) == 1


def test_store_commit_error_is_503_never_200_and_retry_with_key_completes(tmp_path):
    path = tmp_path / "j.jsonl"
    m = _manifest()
    headers = {**AUTH, "Idempotency-Key": "k-503"}

    class FlakyCommit(SQLiteStore):
        fail = True

        def commit(self, *a, **k):
            if self.fail:
                raise OSError("диск заполнен")
            return super().commit(*a, **k)

    store = FlakyCommit(tmp_path / "api.db")
    client = _client(store, path)
    r = client.post("/v1/cases", json=m, headers=headers)
    assert r.status_code == 503 and "журнал" in r.json()["detail"]
    assert store.get(m["case_id"]) is None
    store.fail = False
    ok = client.post("/v1/cases", json=m, headers=headers)
    assert ok.status_code == 200
    assert len(_verify_records(path, m["case_id"])) == 1


def test_stage_failure_is_503_and_writes_no_journal(tmp_path):
    path = tmp_path / "j.jsonl"

    class BrokenStage(MemoryStore):
        def stage(self, *a, **k):
            raise OSError("нет места")

    m = _manifest()
    r = _client(BrokenStage(), path).post("/v1/cases", json=m, headers=AUTH)
    assert r.status_code == 503
    assert _verify_records(path, m["case_id"]) == []


def test_journal_failure_discards_pending_and_is_503(tmp_path):
    path = tmp_path / "j.jsonl"
    path.write_text("испорчено\n", encoding="utf-8")
    store = MemoryStore()
    m = _manifest()
    r = _client(store, path).post("/v1/cases", json=m, headers=AUTH)
    assert r.status_code == 503
    assert store.pending(m["case_id"]) is None and store.get(m["case_id"]) is None


def test_commit_refused_means_409_not_200(tmp_path):
    class Superseded(MemoryStore):
        def commit(self, *a, **k):
            return False

    m = _manifest()
    r = _client(Superseded(), tmp_path / "j.jsonl").post("/v1/cases", json=m, headers=AUTH)
    assert r.status_code == 409


def test_unreadable_journal_does_not_decide_pending(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    store = SQLiteStore(tmp_path / "api.db")
    store.stage("c1", {"reproducibility_hash": "h"}, "alice", "A", None, now=time.time() - 1000)
    path.write_text("испорчено\n", encoding="utf-8")
    _app(store, path)
    assert store.pending("c1") is not None  # ни подтверждена, ни отброшена: журнал не прочитан


def test_fresh_pending_is_left_alone(tmp_path):
    path = tmp_path / "j.jsonl"
    store = SQLiteStore(tmp_path / "api.db")
    store.stage("c1", {"reproducibility_hash": "h"}, "alice", "A", None)  # только что
    app = _app(store, path)
    assert app.state.reconcile() == {"skipped": 1}
    assert store.pending("c1") is not None


def test_pending_with_foreign_journal_record_is_not_committed(tmp_path):
    """Запись журнала с тем же case_id, но другой попыткой, подтверждением не считается."""
    path = tmp_path / "j.jsonl"
    journal.append(
        path,
        action="verify",
        actor="x",
        role="operator",
        case_id="c1",
        payload_hash="h",
        details={"attempt": "OTHER"},
    )
    store = SQLiteStore(tmp_path / "api.db")
    store.stage("c1", {"reproducibility_hash": "h"}, "alice", "A", None, now=time.time() - 1000)
    _app(store, path)
    assert store.pending("c1") is None and store.get("c1") is None


# ---------- Idempotency-Key ----------


def test_same_key_same_body_replays_one_journal_record(tmp_path):
    path = tmp_path / "j.jsonl"
    client = _client(MemoryStore(), path)
    m = _manifest()
    h = {**AUTH, "Idempotency-Key": "abc-123"}
    first = client.post("/v1/cases", json=m, headers=h)
    second = client.post("/v1/cases", json=m, headers=h)
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert len(_verify_records(path, m["case_id"])) == 1


def test_same_key_different_body_is_422(tmp_path):
    client = _client(MemoryStore(), tmp_path / "j.jsonl")
    h = {**AUTH, "Idempotency-Key": "abc-123"}
    assert client.post("/v1/cases", json=_manifest(1), headers=h).status_code == 200
    other = client.post("/v1/cases", json=_manifest(2), headers=h)
    assert other.status_code == 422 and "Idempotency-Key" in other.json()["detail"]


def test_same_key_different_replace_flag_is_422(tmp_path):
    client = _client(MemoryStore(), tmp_path / "j.jsonl")
    h = {**AUTH, "Idempotency-Key": "abc-123"}
    m = _manifest()
    assert client.post("/v1/cases", json=m, headers=h).status_code == 200
    assert client.post("/v1/cases?replace=true", json=m, headers=h).status_code == 422


@pytest.mark.parametrize("bad", ["", " ", "a b", "x" * 129, "a/b", "a;b"])
def test_invalid_idempotency_key_is_422(tmp_path, bad):
    client = _client(MemoryStore(), tmp_path / "j.jsonl")
    r = client.post("/v1/cases", json=_manifest(), headers={**AUTH, "Idempotency-Key": bad})
    assert r.status_code in (422, 400)


def test_key_is_scoped_per_user(tmp_path):
    import json

    from mandat.api.principals import hash_token

    users = json.dumps(
        [
            {"name": "a", "role": "operator", "token_sha256": hash_token("a" * 32)},
            {"name": "b", "role": "operator", "token_sha256": hash_token("b" * 32)},
        ]
    )
    path = tmp_path / "j.jsonl"
    client = TestClient(create_app(users=users, rate_per_min=0, store=MemoryStore(), journal_path=path))
    m = _manifest()
    ha = {"Authorization": "Bearer " + "a" * 32, "Idempotency-Key": "same"}
    hb = {"Authorization": "Bearer " + "b" * 32, "Idempotency-Key": "same"}
    ra = client.post("/v1/cases", json=m, headers=ha)
    rb = client.post("/v1/cases", json=m, headers=hb)
    assert ra.status_code == rb.status_code == 200
    assert ra.json()["case_id"] != rb.json()["case_id"]  # у b не подставился результат a
    assert client.post("/v1/cases", json=m, headers=ha).json() == ra.json()


def test_key_replay_after_report_eviction_is_409(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_REPORTS", 1)
    client = _client(MemoryStore(), tmp_path / "j.jsonl")
    h = {**AUTH, "Idempotency-Key": "k1"}
    assert client.post("/v1/cases", json=_manifest(1), headers=h).status_code == 200
    assert client.post("/v1/cases", json=_manifest(2), headers=AUTH).status_code == 200
    assert client.post("/v1/cases", json=_manifest(1), headers=h).status_code == 409


def test_concurrent_same_key_requests_write_one_journal_record(tmp_path):
    path = tmp_path / "j.jsonl"
    client = _client(SQLiteStore(tmp_path / "api.db"), path)
    m = _manifest()
    h = {**AUTH, "Idempotency-Key": "race"}
    codes: list[int] = []

    def go() -> None:
        codes.append(client.post("/v1/cases", json=m, headers=h).status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(codes) <= {200, 409} and 200 in codes
    assert len(_verify_records(path, m["case_id"])) == 1
    assert client.post("/v1/cases", json=m, headers=h).status_code == 200


def test_without_journal_in_demo_still_two_phase(tmp_path):
    store = MemoryStore()
    client = TestClient(create_app("t", rate_per_min=0, store=store))
    m = _manifest()
    assert client.post("/v1/cases", json=m, headers=AUTH).status_code == 200
    assert store.pending(m["case_id"]) is None and store.get(m["case_id"]) is not None


def test_openapi_documents_idempotency_header():
    spec = create_app(token="t", docs=True).openapi()
    params = spec["paths"]["/v1/cases"]["post"]["parameters"]
    assert any(p["name"] == "Idempotency-Key" and p["in"] == "header" for p in params)
