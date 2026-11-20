"""P6-05: допуск платежа выдаётся служебной учётной записи системы банка, а не сотруднику.

``POST /v1/operations/authorize`` принимал любую роль с правом записи. Оператор, разбирающий и администратор —
люди — могли получить допуск к платежу по чужому удостоверению и израсходовать его одноразовый ``nonce``, а
получатель удостоверения (``audience``) не был связан с тем, кто спрашивает: учётная запись одного канала
ДБО расходовала удостоверения другого. Это нарушение минимальных привилегий и разделения обязанностей
(приказ ФСТЭК № 21, меры УПД.4 и УПД.5; ГОСТ Р 57580.1). Теперь в ``pilot`` и ``prod`` допуск выдаётся только
роли ``dbo``, её учётная запись привязана к своим ``audiences``, человек получает 403, а ``nonce`` при отказе
не расходуется. Роль ``dbo`` не создаёт кейсы и не читает отчёты с ПДн. На стенде допуск по-прежнему доступен
ролям с правом записи.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_a5_operation_admission import AUDIENCE, Issuer, _body

from mandat.api.app import create_app
from mandat.api.principals import hash_token, parse_users
from mandat.api.storage import SQLiteStore

EXPIRES = (datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds")


def _user(name: str, role: str, **extra) -> dict:
    return {"name": name, "role": role, "token_sha256": hash_token(name * 8), "expires_at": EXPIRES, **extra}


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + name * 8}


@pytest.fixture
def issuer(tmp_path) -> Issuer:
    return Issuer(tmp_path)


@pytest.fixture
def pilot(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "j" * 40)
    monkeypatch.setenv("MANDAT_PII_KEY", "p" * 40)


def _client(issuer: Issuer, tmp_path, users: list[dict]) -> TestClient:
    app = create_app(
        users=json.dumps(users),
        store=SQLiteStore(tmp_path / "store.db"),
        journal_path=tmp_path / "journal.jsonl",
        admission_config=issuer.config(),
    )
    return TestClient(app)


def test_dbo_role_carries_audiences_and_nobody_else_does():
    users = parse_users([_user("dbomsb", "dbo", audiences=[AUDIENCE])], require_expiry=True)
    assert users[0].principal.role == "dbo" and users[0].principal.audiences == (AUDIENCE,)
    with pytest.raises(ValueError, match="audiences"):
        parse_users([_user("dbomsb", "dbo")], require_expiry=True)
    with pytest.raises(ValueError, match="audiences"):
        parse_users([_user("anna", "operator", audiences=[AUDIENCE])], require_expiry=True)
    for bad in ([], [""], ["x" * 201], "bank-dbo-msb", [7]):
        with pytest.raises(ValueError, match="audiences"):
            parse_users([_user("dbomsb", "dbo", audiences=bad)], require_expiry=True)
    assert parse_users([_user("dbomsb", "dbo")], require_expiry=False)[0].principal.audiences == ()


def test_protected_mode_admits_only_the_bank_system_for_its_audience(issuer, tmp_path, pilot):
    users = [
        _user("dbomsb", "dbo", audiences=[AUDIENCE]),
        _user("dbocorp", "dbo", audiences=["bank-dbo-corp"]),
        _user("anna", "operator"),
        _user("boris", "reviewer"),
        _user("root", "admin"),
    ]
    client = _client(issuer, tmp_path, users)
    nonce = "n-" + "5" * 20
    body = _body(issuer.issue(nonce), nonce)
    for person in ("anna", "boris", "root"):
        answer = client.post("/v1/operations/authorize", json=body, headers=_auth(person))
        assert answer.status_code == 403, person
        assert "роль dbo" in answer.json()["detail"]
    other_channel = client.post("/v1/operations/authorize", json=body, headers=_auth("dbocorp"))
    assert other_channel.status_code == 403
    assert "audience" in other_channel.json()["detail"]

    admitted = client.post("/v1/operations/authorize", json=body, headers=_auth("dbomsb"))
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["allowed"] is True, admitted.json()  # отказы выше nonce не израсходовали


def test_dbo_account_does_not_create_cases_or_read_reports(issuer, tmp_path, pilot):
    client = _client(issuer, tmp_path, [_user("dbomsb", "dbo", audiences=[AUDIENCE])])
    assert client.post("/v1/cases", json={"case_id": "x"}, headers=_auth("dbomsb")).status_code == 403
    assert client.post("/v1/events", json={"events": []}, headers=_auth("dbomsb")).status_code == 403
    assert client.get("/v1/cases/case-0", headers=_auth("dbomsb")).status_code == 404


def test_stand_keeps_admission_for_roles_that_write(issuer, tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    client = _client(issuer, tmp_path, [_user("anna", "operator"), _user("audit", "auditor")])
    nonce = "n-" + "6" * 20
    body = _body(issuer.issue(nonce), nonce)
    assert client.post("/v1/operations/authorize", json=body, headers=_auth("audit")).status_code == 403
    answer = client.post("/v1/operations/authorize", json=body, headers=_auth("anna"))
    assert answer.status_code == 200 and answer.json()["allowed"] is True, answer.text
