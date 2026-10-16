"""M-09c: маршруты разбора, очередь, настраиваемый SLA, конфликт версии через API."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as app_module
from mandat.api.app import create_app
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore, RedisStore, SQLiteStore, Store
from mandat.audit import journal
from mandat.eval import synth
from mandat.services.next_steps import SLA_ENV, sla_hours

USERS = {
    "op": ("operator", None),
    "op2": ("operator", None),
    "rv": ("reviewer", "lawyer"),
    "rv2": ("reviewer", "compliance"),
    "aud": ("auditor", None),
    "adm": ("admin", None),
}


def _token(name: str) -> str:
    return (name * 32)[:32]


def _h(name: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + _token(name)}


def _users() -> str:
    out = []
    for name, (role, spec) in USERS.items():
        entry = {"name": name, "role": role, "token_sha256": hash_token(_token(name))}
        if spec:
            entry["specialization"] = spec
        out.append(entry)
    return json.dumps(out)


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    for var in ("MANDAT_ENV", "MANDAT_JOURNAL_KEY", "MANDAT_API_JOURNAL", SLA_ENV):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(params=["memory", "sqlite", "redis"])
def store(request, tmp_path) -> Store:
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    import fakeredis
    import lupa  # noqa: F401  # без зависимости тест падает, а не пропускается

    return RedisStore(fakeredis.FakeRedis())


def _client(store: Store, tmp_path: Path) -> TestClient:
    app = create_app(users=_users(), rate_per_min=0, store=store, journal_path=tmp_path / "j.jsonl")
    return TestClient(app)


def _case(client: TestClient, defect: str = "ok", seed: int = 1, who: str = "op") -> str:
    response = client.post("/v1/cases", json=synth.build(defect, seed).inline_manifest(), headers=_h(who))
    assert response.status_code == 200, response.text
    return response.json()["case_id"]


def _act(client: TestClient, case_id: str, action: str, who: str, version: int, reason: str = "основание"):
    return client.post(
        f"/v1/cases/{case_id}/review/{action}",
        json={"expected_version": version, "reason": reason},
        headers=_h(who),
    )


def test_full_cycle_and_journal(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _case(client)
    review = client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).json()
    assert review["state"] == "queued" and review["version"] == 1
    assert review["priority"] and review["sla_hours"] == sla_hours(review["priority"]) and review["due_at"]
    version = review["version"]
    for action, state in [("open", "in_review"), ("request_docs", "docs_requested"), ("open", "in_review")]:
        response = _act(client, case_id, action, "rv", version)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == state
        version = response.json()["version"]
    accepted = _act(client, case_id, "accept", "rv", version)
    assert accepted.json()["state"] == "accepted" and accepted.json()["due_at"] is None
    closed = _act(client, case_id, "close", "op", accepted.json()["version"], reason="")
    assert closed.status_code == 200 and closed.json()["state"] == "closed"
    actions = [r["action"] for r in journal.read_journal(tmp_path / "j.jsonl") if r["case_id"] == case_id]
    assert actions == [
        "verify",
        "review_open",
        "review_request_docs",
        "review_open",
        "review_accept",
        "review_close",
    ]


def test_stale_version_is_409_and_state_kept(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _case(client)
    assert _act(client, case_id, "open", "rv", 1).status_code == 200
    stale = _act(client, case_id, "open", "rv2", 1)
    assert stale.status_code == 409 and "устарела" in stale.json()["detail"]
    assert client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).json()["version"] == 2


def test_store_conflict_is_409(store, tmp_path, monkeypatch):
    # Другой воркер успел записать ту же версию между чтением и записью: ReviewVersionConflict → 409.
    client = _client(store, tmp_path)
    case_id = _case(client)
    real_get = store.review_get

    def racing_get(cid):
        found = real_get(cid)
        monkeypatch.setattr(store, "review_get", real_get)
        assert found is not None
        store.review_put(cid, found[0], found[1])
        return found

    monkeypatch.setattr(store, "review_get", racing_get)
    response = _act(client, case_id, "open", "rv", 1)
    assert response.status_code == 409


def test_parallel_same_version_exactly_one_wins(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _case(client)
    codes: list[int] = []
    barrier = threading.Barrier(4)

    def go(who: str) -> None:
        barrier.wait()
        codes.append(_act(client, case_id, "open", who, 1).status_code)

    threads = [threading.Thread(target=go, args=(w,)) for w in ("rv", "rv2", "op", "rv")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes).count(200) == 1 and set(codes) <= {200, 409}


def test_roles_and_independence(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _case(client)
    assert _act(client, case_id, "open", "aud", 1).status_code == 403  # только чтение
    assert _act(client, case_id, "open", "adm", 1).status_code == 403  # админ кейсы не решает
    assert _act(client, case_id, "open", "op2", 1).status_code == 404  # чужой кейс оператору не виден
    assert _act(client, case_id, "open", "op", 1).status_code == 200
    assert _act(client, case_id, "accept", "op", 2).status_code == 403  # решает только reviewer
    assert _act(client, case_id, "accept", "rv", 2, reason="  ").status_code == 422
    assert _act(client, case_id, "close", "rv", 2).status_code == 409  # из in_review не закрыть
    assert _act(client, case_id, "nonsense", "rv", 2).status_code == 404
    assert client.post(f"/v1/cases/{case_id}/review/open", json={}, headers=_h("rv")).status_code == 422


def test_reviewer_cannot_decide_own_case(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _case(client, who="rv")
    assert _act(client, case_id, "open", "rv", 1).status_code == 200
    assert _act(client, case_id, "accept", "rv", 2).status_code == 403
    assert _act(client, case_id, "accept", "rv2", 2).status_code == 200


def test_appeal_of_auto_decision_on_behalf_of_client(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _case(client, "ok_charter_cleared")
    review = client.get(f"/v1/cases/{case_id}/review", headers=_h("op")).json()
    assert review["state"] == "auto_decided" and review["version"] == 1 and review["due_at"] is None
    assert client.get("/v1/review/queue", headers=_h("rv")).json()["items"] == []
    assert _act(client, case_id, "appeal", "op", 1, reason="").status_code == 422
    appealed = _act(client, case_id, "appeal", "op", 1)
    assert appealed.status_code == 200, appealed.text
    body = appealed.json()
    assert body["state"] == "queued" and body["priority"] == "P2" and body["version"] == 2 and body["due_at"]
    record = [r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "review_appeal"][0]
    assert record["details"]["on_behalf_of"] == "client"
    assert [i["case_id"] for i in client.get("/v1/review/queue", headers=_h("rv")).json()["items"]] == [case_id]


def test_queue_visibility_and_order(store, tmp_path, monkeypatch):
    client = _client(store, tmp_path)
    mine = _case(client, "ok", 1, "op")
    other = _case(client, "expired", 2, "op2")
    for who, expected in [("rv", {mine, other}), ("aud", {mine, other}), ("adm", {mine, other}), ("op", {mine})]:
        response = client.get("/v1/review/queue", headers=_h(who))
        assert response.status_code == 200
        assert {i["case_id"] for i in response.json()["items"]} == expected, who
    items = client.get("/v1/review/queue", headers=_h("rv")).json()["items"]
    assert [i["due_at"] for i in items] == sorted(i["due_at"] for i in items)
    assert all("history" not in i for i in items)
    monkeypatch.setenv(SLA_ENV, json.dumps({"P1": 1, "P2": 2, "P3": 3}))
    hours = {i["case_id"]: i["sla_hours"] for i in client.get("/v1/review/queue", headers=_h("rv")).json()["items"]}
    assert set(hours.values()) <= {1, 2, 3}
    assert client.get("/v1/review/queue").status_code == 401


def test_protected_mode_without_journal_is_503(tmp_path, monkeypatch):
    store = MemoryStore()
    client = TestClient(create_app(users=_users(), rate_per_min=0, store=store, journal_path=tmp_path / "j.jsonl"))
    case_id = _case(client)
    bare = TestClient(create_app(users=_users(), rate_per_min=0, store=store))
    monkeypatch.setattr(app_module, "protected", lambda: True)
    response = _act(bare, case_id, "open", "rv", 1)
    assert response.status_code == 503 and "журнал" in response.json()["detail"]
    assert store.review_get(case_id) is not None and store.review_get(case_id)[1] == 1


def test_broken_journal_is_503_and_state_kept(tmp_path):
    store = MemoryStore()
    path = tmp_path / "j.jsonl"
    client = _client(store, tmp_path)
    case_id = _case(client)
    path.write_text(path.read_text(encoding="utf-8") + "{не json\n", encoding="utf-8")
    response = _act(client, case_id, "open", "rv", 1)
    assert response.status_code == 503
    assert store.review_get(case_id)[0]["state"] == "queued"


def test_sla_defaults_and_config(monkeypatch):
    assert [sla_hours(p) for p in ("P1", "P2", "P3")] == [4, 24, 72]
    monkeypatch.setenv(SLA_ENV, '{"P1": 2}')
    assert [sla_hours(p) for p in ("P1", "P2", "P3")] == [2, 24, 72]
    for bad in ("[", "[]", '{"P9": 1}', '{"P1": 0}', '{"P1": true}', '{"P1": "4"}', '{"P1": 100000}'):
        monkeypatch.setenv(SLA_ENV, bad)
        with pytest.raises(ValueError, match=SLA_ENV):
            sla_hours("P1")


def test_review_list_all_backends(store):
    assert store.review_list() == []
    store.review_put("b", {"state": "queued"}, 0)
    store.review_put("a", {"state": "queued"}, 0)
    store.review_put("a", {"state": "in_review"}, 1)
    assert store.review_list() == [("a", {"state": "in_review"}, 2), ("b", {"state": "queued"}, 1)]


def test_missing_review_record_is_created_on_first_action(tmp_path, monkeypatch):
    # Сбой записи разбора при создании кейса не отменяет кейс: разбор создаётся первым действием с версии 0.
    store = MemoryStore()
    client = _client(store, tmp_path)
    real_put = store.review_put

    def failing_put(*args, **kwargs):
        raise OSError("store down")

    monkeypatch.setattr(store, "review_put", failing_put)
    case_id = _case(client)
    monkeypatch.setattr(store, "review_put", real_put)
    review = client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).json()
    assert review["state"] == "queued" and review["version"] == 0
    assert client.get("/v1/review/queue", headers=_h("rv")).json()["items"] == []
    assert _act(client, case_id, "open", "rv", 0).json()["version"] == 1
