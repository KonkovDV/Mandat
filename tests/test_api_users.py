"""Пользователи и роли API: хеши токенов, владельцы кейсов, case_id от сервера."""

from __future__ import annotations

import json

import pytest

from mandat import cli
from mandat.api.principals import hash_token, load_users, parse_users

TOKENS = {"anna": "t-anna", "boris": "t-boris", "audit": "t-audit", "root": "t-root"}
ROLES = {"anna": "operator", "boris": "reviewer", "audit": "auditor", "root": "admin"}
USERS = json.dumps([{"name": n, "role": ROLES[n], "token_sha256": hash_token(t)} for n, t in TOKENS.items()])


def h(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


@pytest.fixture
def client(monkeypatch):
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости
    from fastapi.testclient import TestClient

    import mandat.api.app as module

    class Stub:
        def __init__(self, case_id):
            self.case_id = case_id

        def as_dict(self):
            return {"case_id": self.case_id, "verdict": "needs_review"}

    monkeypatch.setattr(module, "load_case_inline", lambda manifest: manifest["case_id"])
    monkeypatch.setattr(module, "verify_case", lambda case, profile: Stub(case))
    monkeypatch.setattr(module, "render", lambda report: "<p>ok</p>")
    return TestClient(module.create_app(token="", users=USERS, rate_per_min=0))


def test_server_issues_case_id_and_keeps_client_ref(client):
    report = client.post("/v1/cases", json={"case_id": "mine"}, headers=h("anna")).json()
    assert report["case_id"].startswith("case-") and report["case_id"] != "mine"
    assert report["client_case_id"] == "mine"
    second = client.post("/v1/cases", json={"case_id": "mine"}, headers=h("anna")).json()
    assert second["case_id"] != report["case_id"]


def test_owner_sees_own_case_others_do_not(client):
    case_id = client.post("/v1/cases", json={}, headers=h("anna")).json()["case_id"]
    assert client.get(f"/v1/cases/{case_id}", headers=h("anna")).status_code == 200
    assert client.get(f"/v1/cases/{case_id}/report.html", headers=h("anna")).status_code == 200
    assert client.get(f"/v1/cases/{case_id}", headers=h("boris")).status_code == 404
    assert client.get(f"/v1/cases/{case_id}/report.html", headers=h("boris")).status_code == 404
    assert client.get(f"/v1/cases/{case_id}", headers=h("audit")).status_code == 200
    assert client.get(f"/v1/cases/{case_id}", headers=h("root")).status_code == 200


def test_auditor_only_reads(client):
    assert client.post("/v1/cases", json={}, headers=h("audit")).status_code == 403
    assert client.post("/v1/events", json={"rights": [], "events": []}, headers=h("audit")).status_code == 403


def test_unknown_token_and_health(client):
    assert client.get("/v1/cases/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/health").json()["mode"] == "users"


def test_single_token_mode_still_works(monkeypatch):
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости
    from fastapi.testclient import TestClient

    import mandat.api.app as module

    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    app = TestClient(module.create_app(token="secret", rate_per_min=0))
    assert app.get("/health").json()["mode"] == "single-token"
    assert app.get("/v1/cases/x", headers={"Authorization": "Bearer secret"}).status_code == 404


@pytest.mark.parametrize(
    "users",
    [
        {"name": "a", "role": "god", "token_sha256": "0" * 64},
        {"name": "a", "role": "admin", "token_sha256": "short"},
        {"name": "", "role": "admin", "token_sha256": "0" * 64},
    ],
)
def test_bad_user_entries_are_rejected(users):
    with pytest.raises(ValueError):
        parse_users([users])


def test_duplicate_names_rejected():
    entry = {"name": "a", "role": "admin", "token_sha256": "0" * 64}
    with pytest.raises(ValueError):
        parse_users([entry, entry])


def test_users_from_file(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(USERS, encoding="utf-8")
    assert {u.principal.name for u in load_users(str(path))} == set(TOKENS)


def test_cli_issues_token_and_hash(capsys):
    assert cli.main(["api-user", "--name", "vera", "--role", "auditor"]) == 0
    lines = capsys.readouterr().out.splitlines()
    token = lines[0].rsplit(": ", 1)[1]
    entry = json.loads(lines[-1])
    assert entry == {"name": "vera", "role": "auditor", "token_sha256": hash_token(token)}
    assert cli.main(["api-user", "--name", "vera", "--role", "boss"]) == 2
