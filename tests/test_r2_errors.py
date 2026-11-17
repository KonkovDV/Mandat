"""R2-API-3: общие ответы 422/500/503 с request_id, подробности только в журнале сервера, без ПДн."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.storage import MemoryStore

AUTH = {"Authorization": "Bearer t"}


def _client(**kw):
    application = api.create_app(token="t", rate_per_min=0, store=MemoryStore(), **kw)
    return application, TestClient(application, raise_server_exceptions=False)


def test_schema_422_is_generic_and_logs_no_input(caplog):
    _, client = _client()
    with caplog.at_level(logging.WARNING, "mandat.api"):
        r = client.post("/v1/cases", content=b'[{"inn": "7701234567", "mail": "ivan@example.ru"}]', headers=AUTH)
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "запрос не соответствует схеме" and len(body["request_id"]) == 16
    assert r.headers["x-request-id"] == body["request_id"]
    assert "7701234567" not in r.text and "7701234567" not in caplog.text and "ivan@" not in caplog.text
    assert body["request_id"] in caplog.text


def test_case_error_422_hides_message(caplog):
    _, client = _client()
    with caplog.at_level(logging.WARNING, "mandat.api"):
        r = client.post("/v1/cases", json={"case_id": "bad", "requester_inn": "770123456789"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["detail"] == "кейс не прошёл проверку входных данных"
    assert "CaseError" in caplog.text and "770123456789" not in caplog.text


def test_unhandled_500_is_generic(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("секрет 4111111111111111 в сообщении")

    monkeypatch.setattr(api, "evaluate", boom)
    monkeypatch.setattr(api, "parse_events", lambda payload, warnings: ([], []))
    _, client = _client()
    with caplog.at_level(logging.WARNING, "mandat.api"):
        r = client.post("/v1/events", json={"events": []}, headers=AUTH)
    assert r.status_code == 500
    assert r.json()["detail"] == "внутренняя ошибка сервера"
    assert "4111" not in r.text and "4111111111111111" not in caplog.text
    assert "RuntimeError" in caplog.text and "app.py:" in caplog.text
    assert r.headers["x-content-type-options"] == "nosniff"


def test_journal_503_hides_cause(tmp_path, monkeypatch, caplog):
    from mandat.audit import journal

    def fail(*a, **k):
        raise OSError("/secret/path/journal.jsonl: нет места")

    monkeypatch.setattr(journal, "append", fail)
    import copy

    from mandat.eval import synth

    _, client = _client(journal_path=tmp_path / "j.jsonl")
    r = client.post("/v1/cases", json=copy.deepcopy(synth.build("ok", 1).inline_manifest()), headers=AUTH)
    assert r.status_code == 503
    assert "secret" not in r.text and "secret" in caplog.text


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("ИНН 7701234567", "ИНН <num>"),
        ("a@b.ru x", "<email> x"),
        ("год 2026", "год 2026"),
        ("ОГРН 1027700132195", "ОГРН <num>"),
    ],
)
def test_scrub(raw, clean):
    assert api.scrub(raw) == clean


def test_every_response_has_request_id():
    _, client = _client()
    assert len(client.get("/health").headers["x-request-id"]) == 16
    assert client.get("/v1/cases/x").status_code == 401
    ids = {client.get("/v1/cases/x", headers=AUTH).headers["x-request-id"] for _ in range(3)}
    assert len(ids) == 3


def test_lint_claims_rejects_red_evidence():
    """714 против 715: число тестов берётся только из зелёного прогона."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "lint_claims", Path(__file__).resolve().parents[1] / "scripts" / "lint_claims.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.red_run({"returncode": 0, "passed": 5}) is None
    assert "красном" in module.red_run({"returncode": 1, "passed": 714, "failed": 1, "summary": "1 failed"})
    assert module.red_run(None)
