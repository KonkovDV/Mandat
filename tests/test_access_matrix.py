"""Матрица доступа и изоляция кейсов: разрешение и запрет на каждом пути к отчёту.

Воркшоп «Данные, безопасность и комплаенс» (Академия Финтеха, 20.11.2026): «успешно войти — не значит получить
все права»; «изоляция клиентов: проверить свои и чужие документы на каждом значимом пути доступа»; «поддержке —
статус обращения, а не все документы клиента». OWASP API Security Top 10 2023, API1 — Broken Object Level
Authorization: проверка владельца объекта на каждом маршруте, а не только на главном.

Таблица ``EXPECTED`` — это и есть матрица доступа «роль × путь → ответ». Оператор A создал кейс; оператор B,
разбирающий сотрудник, аудитор, администратор и учётная запись ДБО идут к нему всеми путями. Чужой кейс отвечает
так же, как несуществующий: 404 с тем же телом, иначе по ответу можно перебирать номера кейсов.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.storage import MemoryStore
from mandat.eval.synth import build

ROLES = {
    "op_a": "operator",
    "op_b": "operator",
    "reviewer": "reviewer",
    "auditor": "auditor",
    "admin": "admin",
    "dbo": "dbo",
}


def _token(name: str) -> str:
    return f"token-{name}-".ljust(40, "x")


def _headers(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(name)}"}


@pytest.fixture
def world(tmp_path):
    users = [
        {"name": name, "role": role, "token_sha256": hashlib.sha256(_token(name).encode()).hexdigest()}
        | ({"audiences": ["bank-dbo-msb"]} if role == "dbo" else {})
        for name, role in ROLES.items()
    ]
    app = create_app(users=json.dumps(users), store=MemoryStore(), journal_path=tmp_path / "journal.jsonl")
    client = TestClient(app)
    created = client.post("/v1/cases", json=build("ok", 5).inline_manifest(), headers=_headers("op_a"))
    assert created.status_code == 200, created.text
    return client, created.json()["case_id"]


# Матрица: путь → ответ для каждой учётной записи. Пути без изменения состояния.
EXPECTED: dict[tuple[str, str], dict[str, int]] = {
    ("GET", "/v1/cases/{id}"): {"op_a": 200, "op_b": 404, "reviewer": 404, "auditor": 200, "admin": 200, "dbo": 404},
    ("GET", "/v1/cases/{id}/report.html"): {
        "op_a": 200,
        "op_b": 404,
        "reviewer": 404,
        "auditor": 200,
        "admin": 200,
        "dbo": 404,
    },
    ("GET", "/v1/cases/{id}/review"): {
        "op_a": 200,
        "op_b": 404,
        "reviewer": 200,
        "auditor": 200,
        "admin": 200,
        "dbo": 404,
    },
}
# Изменяющие пути: здесь проверяется только запрет, поэтому порядок запросов не влияет на результат.
DENIED_WRITES: dict[tuple[str, str], dict[str, int]] = {
    ("POST", "/v1/cases/{id}/review/open"): {"op_b": 404, "auditor": 403, "dbo": 403},
    ("POST", "/v1/cases/{id}/review/accept"): {"op_b": 404, "auditor": 403, "dbo": 403},
    ("POST", "/v1/cases/{id}/erase"): {"op_a": 403, "op_b": 403, "reviewer": 403, "auditor": 403, "dbo": 403},
}
BODIES = {"review": {"expected_version": 0, "reason": "проверка матрицы"}, "erase": {"reason": "purpose_achieved"}}


def test_read_paths_follow_the_access_matrix(world):
    client, case_id = world
    mismatches = []
    for (method, template), expected in EXPECTED.items():
        for user, status in expected.items():
            got = client.request(method, template.format(id=case_id), headers=_headers(user)).status_code
            if got != status:
                mismatches.append(f"{user} {method} {template}: {got}, ожидалось {status}")
    assert not mismatches, "\n".join(mismatches)


def test_write_paths_deny_foreign_and_read_only_accounts(world):
    client, case_id = world
    mismatches = []
    for (method, template), expected in DENIED_WRITES.items():
        body = BODIES["erase"] if template.endswith("erase") else BODIES["review"]
        for user, status in expected.items():
            got = client.request(method, template.format(id=case_id), json=body, headers=_headers(user)).status_code
            if got != status:
                mismatches.append(f"{user} {method} {template}: {got}, ожидалось {status}")
    assert not mismatches, "\n".join(mismatches)
    assert client.get(f"/v1/cases/{case_id}", headers=_headers("op_a")).status_code == 200, "запреты ничего не сломали"


def test_foreign_case_is_indistinguishable_from_a_missing_one(world):
    client, case_id = world
    for path in ("/v1/cases/{id}", "/v1/cases/{id}/report.html", "/v1/cases/{id}/review"):
        foreign = client.get(path.format(id=case_id), headers=_headers("op_b"))
        missing = client.get(path.format(id="case-0000000000000000"), headers=_headers("op_b"))
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["detail"] == missing.json()["detail"]


def test_review_queue_shows_a_case_only_to_those_who_may_handle_it(world):
    client, case_id = world
    seen = {
        user: case_id
        in {item["case_id"] for item in client.get("/v1/review/queue", headers=_headers(user)).json()["items"]}
        for user in ROLES
    }
    assert seen == {"op_a": True, "op_b": False, "reviewer": True, "auditor": True, "admin": True, "dbo": False}


def test_idempotency_key_of_another_user_does_not_return_their_report(world):
    client, case_id = world
    manifest = build("ok", 6).inline_manifest()
    key = {"Idempotency-Key": "same-key-for-both"}
    first = client.post("/v1/cases", json=manifest, headers={**_headers("op_a"), **key})
    second = client.post("/v1/cases", json=manifest, headers={**_headers("op_b"), **key})
    assert first.status_code == second.status_code == 200
    assert first.json()["case_id"] != second.json()["case_id"], "ключ повтора принадлежит пользователю, а не системе"
    assert client.get(f"/v1/cases/{first.json()['case_id']}", headers=_headers("op_b")).status_code == 404


def test_only_admin_erases_and_the_erased_case_is_gone_for_everyone(world):
    client, case_id = world
    assert client.post(f"/v1/cases/{case_id}/erase", json=BODIES["erase"], headers=_headers("admin")).status_code == 200
    for user in ROLES:
        assert client.get(f"/v1/cases/{case_id}", headers=_headers(user)).status_code == 404, user
