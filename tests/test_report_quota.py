"""Квота отчётов — на владельца: поток кейсов одного пользователя не вытесняет чужие и не опустошает очередь разбора.

Раньше общий потолок ``MAX_REPORTS`` с вытеснением по порядку давал любому оператору удалить чужие кейсы, в том
числе ждущие разбора, а очередь молча прятала кейс без отчёта. Теперь сверх квоты уходят только самые старые кейсы
того же владельца с закрытым разбором, вытеснение пишется в журнал (``evict_case``), а когда вытеснить нечего, новый
кейс получает 429. Поведение одно в памяти, SQLite и Redis, в том числе под шифром хранилища.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as app_module
from mandat.api.review import OPEN_STATES
from mandat.api.sealing import SealedStore, Sealer
from mandat.api.storage import OPEN_REVIEW_STATES, MemoryStore, ReportQuotaExceeded, SQLiteStore
from mandat.audit import journal
from mandat.eval.synth import build

CLOSED = {"state": "closed"}
QUEUED = {"state": "queued"}


@pytest.fixture(params=["memory", "sqlite", "redis", "sealed-sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    if request.param == "redis":
        return request.getfixturevalue("redis_store")
    return SealedStore(SQLiteStore(tmp_path / "sealed.db"), Sealer("store-key-" + "q" * 32))


def _commit(store, case_id: str, owner: str, review: dict, limit: int = 2) -> list[str]:
    store.review_put(case_id, review, 0)
    store.stage(case_id, {"case": case_id}, owner, "a-" + case_id, None)
    evicted: list[str] = []
    assert store.commit(case_id, "a-" + case_id, limit, evicted=evicted)
    return evicted


def test_open_states_match_the_review_queue():
    assert {s.value for s in OPEN_STATES} == OPEN_REVIEW_STATES


def test_one_owner_cannot_evict_another_owners_reports(store):
    _commit(store, "victim", "anna", QUEUED)
    _commit(store, "settled", "anna", CLOSED)
    assert _commit(store, "m1", "mallory", CLOSED) == []
    assert _commit(store, "m2", "mallory", CLOSED) == []
    assert _commit(store, "m3", "mallory", CLOSED) == ["m1"]  # своё, самое старое, с закрытым разбором
    assert store.get("victim") == ({"case": "victim"}, "anna")
    assert store.get("settled") == ({"case": "settled"}, "anna")
    assert store.get("m1") is None and store.get("m3") is not None


def test_report_in_review_is_never_evicted_and_a_full_quota_refuses(store):
    _commit(store, "q1", "mallory", QUEUED)
    _commit(store, "q2", "mallory", QUEUED)
    assert not store.room("mallory", "q3", 2)
    assert store.room("anna", "a1", 2)  # квота чужого владельца не тронута
    store.review_put("q3", QUEUED, 0)
    store.stage("q3", {"case": "q3"}, "mallory", "a-q3", None)
    with pytest.raises(ReportQuotaExceeded):
        store.commit("q3", "a-q3", 2)
    assert store.pending("q3") is None, "отказ снимает ожидающую запись: сверка не опубликует её позже"
    assert store.get("q1") is not None and store.get("q2") is not None and store.get("q3") is None
    assert store.room("mallory", "q1", 2), "перезапись своего кейса места не занимает"
    with pytest.raises(ReportQuotaExceeded):
        store.put("q4", {"case": "q4"}, "mallory", 2)


def test_report_without_a_review_record_is_kept(store):
    store.put("bare", {"case": "bare"}, "mallory", 1)
    with pytest.raises(ReportQuotaExceeded):
        store.put("next", {"case": "next"}, "mallory", 1)
    assert store.get("bare") is not None


def test_closing_the_review_makes_the_report_evictable(store):
    _commit(store, "q1", "mallory", QUEUED, limit=1)
    assert not store.room("mallory", "q2", 1)
    store.review_put("q1", CLOSED, 1)
    assert store.room("mallory", "q2", 1)
    store.review_put("q1", {"state": "queued"}, 2)  # жалоба вернула кейс в очередь: снова держится
    assert not store.room("mallory", "q2", 1)


def test_old_sqlite_file_gets_the_review_flag(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE reviews (case_id TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL);
        INSERT INTO reviews VALUES ('open', 1, '{"state": "in_review"}'), ('done', 1, '{"state": "accepted"}');
        INSERT INTO reviews VALUES ('sealed', 1, '{"sealed": "mandat.sealed.v2", "data": "x"}');
        """
    )
    db.commit()
    db.close()
    SQLiteStore(path)
    rows = dict(sqlite3.connect(path).execute("SELECT case_id, held FROM reviews").fetchall())
    assert rows == {"open": 1, "done": 0, "sealed": 1}  # признак не прочитать — отчёт держится


# --- HTTP API ----------------------------------------------------------------------------------------------------

USERS = {"op1": "operator", "op2": "operator", "rev": "reviewer"}


def _token(name: str) -> str:
    return f"token-{name}-".ljust(40, "x")


def _h(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(name)}"}


def _client(store, tmp_path) -> TestClient:
    users = [
        {"name": n, "role": r, "token_sha256": hashlib.sha256(_token(n).encode()).hexdigest()} for n, r in USERS.items()
    ]
    app = app_module.create_app(users=json.dumps(users), store=store, journal_path=tmp_path / "j.jsonl", rate_per_min=0)
    return TestClient(app)


def _create(client: TestClient, who: str, seed: int):
    return client.post("/v1/cases", json=build("ok", seed).inline_manifest(), headers=_h(who))


def test_flood_by_one_operator_keeps_the_victim_and_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_REPORTS", 3)
    store = MemoryStore()
    client = _client(store, tmp_path)
    victim = _create(client, "op1", 1).json()["case_id"]
    codes = [_create(client, "op2", seed).status_code for seed in range(2, 8)]
    assert codes == [200, 200, 200, 429, 429, 429]
    assert client.get(f"/v1/cases/{victim}", headers=_h("op1")).status_code == 200
    queue = {i["case_id"] for i in client.get("/v1/review/queue", headers=_h("rev")).json()["items"]}
    assert victim in queue and len(queue) == 4
    verified = [r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "verify"]
    assert len(verified) == 4, "отказ по квоте приходит до проверки кейса и записи в журнал"


def test_eviction_of_a_settled_case_is_journaled(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_REPORTS", 1)
    store = MemoryStore()
    client = _client(store, tmp_path)
    old = _create(client, "op2", 1).json()["case_id"]
    body, version = store.review_get(old)
    store.review_put(old, {**body, "state": "closed"}, version)
    new = _create(client, "op2", 2)
    assert new.status_code == 200
    assert client.get(f"/v1/cases/{old}", headers=_h("op2")).status_code == 404
    evicted = [r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "evict_case"]
    assert [(r["case_id"], r["actor"], r["details"]["for_case"]) for r in evicted] == [
        (old, "op2", new.json()["case_id"])
    ]
    assert journal.verify_journal(tmp_path / "j.jsonl").ok


def test_queue_does_not_hide_a_case_whose_report_is_missing(tmp_path):
    store = MemoryStore()
    client = _client(store, tmp_path)
    case_id = _create(client, "op1", 1).json()["case_id"]
    store._reports.pop(case_id)  # отчёт пропал мимо квоты: копия, сбой
    items = client.get("/v1/review/queue", headers=_h("rev")).json()["items"]
    assert [(i["case_id"], i.get("report_missing")) for i in items] == [(case_id, True)]
