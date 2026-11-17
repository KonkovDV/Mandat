"""M-09 (#67): специализация через API, сброс разбора при replace, эскалация по сроку, жалоба на решение человека."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mandat.api import review as review_api
from mandat.api.app import create_app
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore, SQLiteStore, Store
from mandat.audit import journal
from mandat.eval import synth
from mandat.review.state_machine import ReviewCase, ReviewConflict, ReviewForbidden, ReviewInvalid, ReviewState
from mandat.services.next_steps import SLA_ENV, sla_hours

USERS = {
    "op": ("operator", None),
    "rv": ("reviewer", "lawyer"),
    "rv2": ("reviewer", "lawyer"),
    "rv3": ("reviewer", "compliance"),
}


def _token(name: str) -> str:
    return (name * 32)[:32]


def _h(name: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + _token(name)}


def _users() -> str:
    return json.dumps(
        [
            {"name": n, "role": r, "token_sha256": hash_token(_token(n)), **({"specialization": s} if s else {})}
            for n, (r, s) in USERS.items()
        ]
    )


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    for var in ("MANDAT_ENV", "MANDAT_JOURNAL_KEY", "MANDAT_API_JOURNAL", SLA_ENV):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path) -> Store:
    return MemoryStore() if request.param == "memory" else SQLiteStore(tmp_path / "api.db")


def _client(store: Store, tmp_path: Path) -> TestClient:
    return TestClient(create_app(users=_users(), rate_per_min=0, store=store, journal_path=tmp_path / "j.jsonl"))


def _post(client: TestClient, manifest: dict, replace: bool = False):
    return client.post("/v1/cases", params={"replace": replace} if replace else None, json=manifest, headers=_h("op"))


def _act(client, case_id, action, who, version, reason="основание", **extra):
    body = {"expected_version": version, "reason": reason, **extra}
    return client.post(f"/v1/cases/{case_id}/review/{action}", json=body, headers=_h(who))


def _review(client, case_id):
    return client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).json()


def _queued(client) -> str:
    response = _post(client, synth.build("ok", 1).inline_manifest())
    assert response.status_code == 200, response.text
    return response.json()["case_id"]


# --- required_specialization через API ---


def test_require_specialization_via_api(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _queued(client)
    response = _act(client, case_id, "require", "op", 1, specialization="compliance")
    assert response.status_code == 200, response.text
    assert response.json()["required_specialization"] == "compliance" and response.json()["state"] == "queued"
    v = response.json()["version"]
    v = _act(client, case_id, "open", "rv", v).json()["version"]
    assert _act(client, case_id, "accept", "rv", v).status_code == 403  # lawyer, а нужна compliance
    assert _act(client, case_id, "accept", "rv3", v).status_code == 200
    actions = [r["action"] for r in journal.read_journal(tmp_path / "j.jsonl") if r["case_id"] == case_id]
    assert "review_require" in actions


def test_require_cannot_be_replaced_or_invalid(store, tmp_path):
    client = _client(store, tmp_path)
    case_id = _queued(client)
    assert _act(client, case_id, "require", "op", 1, specialization="astrology").status_code == 422
    assert _act(client, case_id, "require", "op", 1, specialization="").status_code == 422
    v = _act(client, case_id, "require", "op", 1, specialization="lawyer").json()["version"]
    assert _act(client, case_id, "require", "rv3", v, specialization="compliance").status_code == 409
    assert _act(client, case_id, "require", "op", v, specialization="lawyer", reason="").status_code == 422
    assert _act(client, case_id, "open", "rv", v, specialization="lawyer").status_code == 422  # только в require


# --- replace=true сбрасывает разбор ---


def _single(store: Store, tmp_path: Path) -> TestClient:
    # replace=true перезаписывает кейс только в режиме одного токена: при списке пользователей номер кейса
    # выдаёт сервер, и повторная отправка создаёт новый кейс.
    return TestClient(create_app(token="t", rate_per_min=0, store=store, journal_path=tmp_path / "j.jsonl"))


T = {"Authorization": "Bearer t"}


def _rejected(store: Store, case_id: str) -> int:
    body, version = store.review_get(case_id)
    case = ReviewCase.from_dict(body)
    case.apply("require", actor="op", role="operator", reason="правовой вопрос", required_specialization="lawyer")
    case.apply("open", actor="rv", role="reviewer", specialization="lawyer")
    case.apply("reject", actor="rv", role="reviewer", specialization="lawyer", reason="нет полномочий")
    return store.review_put(case_id, review_api.dump(case, body), version)


def test_replace_resets_review(store, tmp_path):
    client = _single(store, tmp_path)
    manifest = synth.build("ok", 1).inline_manifest()
    case_id = manifest["case_id"]
    assert client.post("/v1/cases", json=manifest, headers=T).status_code == 200
    version = _rejected(store, case_id)
    other = {**synth.build("ok", 2).inline_manifest(), "case_id": case_id}
    assert client.post("/v1/cases?replace=true", json=other, headers=T).status_code == 200
    body, new_version = store.review_get(case_id)
    assert new_version == version + 1
    assert body["state"] == "queued" and body["history"] == []
    assert body["superseded"][0]["state"] == "rejected" and len(body["superseded"][0]["history"]) == 3
    assert body["required_specialization"] == "lawyer"  # перезаписью требование не снять
    reset = [r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "review_reset"]
    assert len(reset) == 1 and reset[0]["details"]["previous_state"] == "rejected"
    # Следующее действие идёт с новой версии; история прежнего разбора не теряется.
    case = ReviewCase.from_dict(body)
    case.apply("open", actor="rv2", role="reviewer", specialization="lawyer")
    store.review_put(case_id, review_api.dump(case, body), new_version)
    assert store.review_get(case_id)[0]["superseded"]


def test_replace_with_same_report_keeps_review(store, tmp_path):
    client = _single(store, tmp_path)
    manifest = synth.build("ok", 1).inline_manifest()
    assert client.post("/v1/cases", json=manifest, headers=T).status_code == 200
    version = _rejected(store, manifest["case_id"])
    assert client.post("/v1/cases?replace=true", json=manifest, headers=T).status_code == 200
    body, same = store.review_get(manifest["case_id"])
    assert body["state"] == "rejected" and same == version


# --- эскалация по сроку ---


def test_escalation_levels():
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    hours = sla_hours("P3")
    due = (now - timedelta(minutes=1)).isoformat()
    assert review_api.escalation("P3", (now + timedelta(hours=1)).isoformat(), now)["overdue"] is False
    first = review_api.escalation("P3", due, now)
    assert first == {"overdue": True, "escalation_level": 1, "effective_priority": "P2"}
    later = review_api.escalation("P3", (now - timedelta(hours=hours + 1)).isoformat(), now)
    assert later["escalation_level"] == 2 and later["effective_priority"] == "P1"
    assert review_api.escalation("P1", due, now)["effective_priority"] == "P1"
    assert review_api.escalation(None, None, now)["overdue"] is False


def test_queue_puts_overdue_first(store, tmp_path, monkeypatch):
    client = _client(store, tmp_path)
    late = _queued(client)
    fresh_manifest = synth.build("ok", 3).inline_manifest()
    fresh = _post(client, fresh_manifest).json()["case_id"]
    body, version = store.review_get(late)
    body["queued_at"] = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
    store.review_put(late, body, version)
    items = client.get("/v1/review/queue", headers=_h("rv")).json()["items"]
    assert [i["case_id"] for i in items][:2] == [late, fresh]
    assert items[0]["overdue"] and items[0]["effective_priority"] == "P1" and not items[1]["overdue"]


# --- жалоба на решение человека ---


def _decided(client) -> tuple[str, int]:
    case_id = _queued(client)
    v = _act(client, case_id, "open", "rv", 1).json()["version"]
    v = _act(client, case_id, "reject", "rv", v).json()["version"]
    return case_id, v


def test_appeal_on_manual_decision_goes_to_another_reviewer(store, tmp_path):
    client = _client(store, tmp_path)
    case_id, v = _decided(client)
    appealed = _act(client, case_id, "appeal", "op", v)
    assert appealed.status_code == 200 and appealed.json()["state"] == "queued"
    v = _act(client, case_id, "open", "rv", appealed.json()["version"]).json()["version"]  # open — не решение
    assert _act(client, case_id, "accept", "rv", v).status_code == 403  # решавший свою жалобу не решает
    assert _act(client, case_id, "accept", "rv2", v).status_code == 200


def test_appeal_after_close(store, tmp_path):
    client = _client(store, tmp_path)
    case_id, v = _decided(client)
    v = _act(client, case_id, "close", "op", v, reason="").json()["version"]
    assert _act(client, case_id, "appeal", "op", v).json()["state"] == "queued"


def test_appeal_needs_operator_and_reason(store, tmp_path):
    client = _client(store, tmp_path)
    case_id, v = _decided(client)
    assert _act(client, case_id, "appeal", "rv2", v).status_code == 403
    assert _act(client, case_id, "appeal", "op", v, reason=" ").status_code == 422


def test_state_machine_own_case_rules():
    case = ReviewCase(case_id="c", report_hash="h", created_by="op")
    case.apply("open", actor="rv", role="reviewer", specialization="lawyer")
    case.apply("request_docs", actor="rv", role="reviewer", specialization="lawyer", reason="нужен устав")
    case.apply("open", actor="rv2", role="reviewer", specialization="lawyer")
    case.apply("accept", actor="rv2", role="reviewer", specialization="lawyer", reason="ок")
    case.apply("appeal", actor="op", role="operator", reason="клиент не согласен")
    assert case.state is ReviewState.QUEUED and case.appealed_decision_by == "rv2"
    assert {"op", "rv", "rv2"} <= case.conflicted_actors()
    case.apply("open", actor="rv", role="reviewer", specialization="lawyer")
    with pytest.raises(ReviewForbidden):
        case.apply("reject", actor="rv", role="reviewer", specialization="lawyer", reason="нет")
    with pytest.raises(ReviewForbidden):
        case.apply("reject", actor="op", role="reviewer", specialization="lawyer", reason="нет")  # свой кейс
    case.apply("reject", actor="rv3", role="reviewer", specialization="lawyer", reason="нет")
    assert case.state is ReviewState.REJECTED


def test_state_machine_require_rules():
    case = ReviewCase(case_id="c", report_hash="h")
    with pytest.raises(ReviewInvalid):
        case.apply("require", actor="op", role="operator", reason="x", required_specialization=None)
    case.apply("require", actor="op", role="operator", reason="правовой вопрос", required_specialization="lawyer")
    case.apply("require", actor="op", role="operator", reason="повтор", required_specialization="lawyer")
    with pytest.raises(ReviewConflict):
        case.apply("require", actor="op", role="operator", reason="x", required_specialization="security")
    assert case.required_specialization == "lawyer"
