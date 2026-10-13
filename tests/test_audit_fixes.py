"""Закрытие находок аудита 2026-10: ключ журнала, якорь Меркла, API и ЕФРСБ."""

from __future__ import annotations

import json

import pytest

from mandat import cli
from mandat.audit import journal, merkle
from mandat.ports import BankruptcyStatus, FixtureRegistry, NotConnectedSignature, Ports


def _append(path, **kw):
    return journal.append(path, action="verify", actor="t", role="operator", case_id="c1", **kw)


def test_journal_needs_key_outside_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    with pytest.raises(journal.JournalKeyMissing):
        _append(tmp_path / "j.jsonl")
    assert not (tmp_path / "j.jsonl").exists()
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "k")
    assert "sig" in _append(tmp_path / "j.jsonl")
    assert journal.verify_journal(tmp_path / "j.jsonl").ok


def test_journal_works_without_key_in_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    assert "sig" not in _append(tmp_path / "j.jsonl")


def test_cli_reports_missing_key_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "prod")
    assert cli.main(["journal-verify", str(tmp_path / "none.jsonl")]) == 2
    assert "MANDAT_JOURNAL_KEY" in capsys.readouterr().err


def test_merkle_anchor_catches_rewritten_journal(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    path = tmp_path / "j.jsonl"
    _append(path)
    root = merkle.root(merkle.journal_leaves(journal.read_journal(path))).hex()
    assert cli.main(["journal-verify", str(path), "--merkle-anchor", root]) == 0
    capsys.readouterr()
    assert cli.main(["journal-verify", str(path), "--merkle-anchor", "0" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_bankruptcy_port_is_honestly_not_connected():
    ports = Ports(registry=FixtureRegistry({}), signature=NotConnectedSignature())
    assert ports.bankruptcy.status("7700000000") is BankruptcyStatus.NOT_CHECKED


@pytest.fixture
def api(monkeypatch):
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости

    import mandat.api.app as module

    class Stub:
        def __init__(self, case_id):
            self.case_id = case_id

        def as_dict(self):
            return {"case_id": self.case_id}

    monkeypatch.setattr(module, "load_case_inline", lambda manifest: manifest["case_id"])
    monkeypatch.setattr(module, "verify_case", lambda case, profile: Stub(case))
    return module


AUTH = {"Authorization": "Bearer secret"}


def test_api_refuses_to_overwrite_a_case(api):
    from fastapi.testclient import TestClient

    client = TestClient(api.create_app(token="secret", rate_per_min=0))
    assert client.post("/v1/cases", json={"case_id": "a"}, headers=AUTH).status_code == 200
    assert client.post("/v1/cases", json={"case_id": "a"}, headers=AUTH).status_code == 409
    assert client.post("/v1/cases?replace=true", json={"case_id": "a"}, headers=AUTH).status_code == 200


def test_api_docs_are_off_unless_asked(api, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("MANDAT_API_DOCS", raising=False)
    assert TestClient(api.create_app(token="secret")).get("/docs", headers=AUTH).status_code == 404
    assert TestClient(api.create_app(token="secret")).get("/openapi.json", headers=AUTH).status_code == 404
    # С 0.3.0 строже: описание API и при включённом флаге отдаётся только с токеном.
    with_docs = TestClient(api.create_app(token="secret", docs=True))
    assert with_docs.get("/docs").status_code == 401
    assert with_docs.get("/docs", headers=AUTH).status_code == 200


def test_api_rate_limit(api):
    from fastapi.testclient import TestClient

    client = TestClient(api.create_app(token="secret", rate_per_min=3))
    codes = [client.get("/v1/cases/x", headers=AUTH).status_code for _ in range(4)]
    assert codes == [404, 404, 404, 429]
    assert client.get("/health").status_code == 200
