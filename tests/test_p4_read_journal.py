"""P4-11: кто открыл отчёт кейса с ПДн, записано в журнале аудита; без журнала отчёт не выдаётся.

Раньше в журнал попадали создание кейса и допуск операции, а чтение отчёта с ФИО, ИНН и СНИЛС — нет. Сотрудник
с доступом к API мог выгрузить отчёты без следа, а это и есть внутренний нарушитель ст. 272.1 УК РФ (ч. 3, п. «г»:
с использованием служебного положения). Регистрация доступа к ПДн — мера группы РСБ приказа ФСТЭК № 21 и
требование ГОСТ Р 57580.1.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.storage import MemoryStore
from mandat.audit import journal
from mandat.eval.synth import build

TOKEN = "t" * 40
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def client_and_case(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    path = tmp_path / "journal.jsonl"
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore(), journal_path=path))
    manifest = build("ok", seed=11).inline_manifest()
    created = client.post("/v1/cases", json=manifest, headers=HEADERS)
    assert created.status_code == 200, created.text
    return client, created.json()["case_id"], path


def test_reading_a_case_and_its_report_is_journaled_without_the_data(client_and_case):
    client, case_id, path = client_and_case
    assert client.get(f"/v1/cases/{case_id}", headers=HEADERS).status_code == 200
    assert client.get(f"/v1/cases/{case_id}/report.html", headers=HEADERS).status_code == 200
    records = _records(path)
    reads = [r for r in records if r["action"] in {"read_case", "read_report"}]
    assert [r["action"] for r in reads] == ["read_case", "read_report"]
    report = client.get(f"/v1/cases/{case_id}", headers=HEADERS).json()
    for record in reads:
        assert record["case_id"] == case_id and record["actor"] and record["role"]
        assert record["payload_hash"] == report["reproducibility_hash"]
        text = json.dumps(record, ensure_ascii=False)
        assert report["requester"]["inn"] not in text and report["organization"]["inn"] not in text
    assert journal.verify_journal(path).ok


def test_report_is_not_given_out_when_the_journal_fails(client_and_case, monkeypatch):
    client, case_id, _ = client_and_case
    real = journal.append

    def failing(path, **kwargs):
        if kwargs.get("action") in {"read_case", "read_report"}:
            raise OSError("диск журнала недоступен")
        return real(path, **kwargs)

    monkeypatch.setattr(journal, "append", failing)
    for url in (f"/v1/cases/{case_id}", f"/v1/cases/{case_id}/report.html"):
        answer = client.get(url, headers=HEADERS)
        assert answer.status_code == 503, url
        assert "журнал аудита недоступен" in answer.json()["detail"]


def test_protected_mode_without_a_journal_gives_no_report(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore()))
    created = client.post("/v1/cases", json=build("ok", seed=12).inline_manifest(), headers=HEADERS)
    assert created.status_code == 200, created.text
    case_id = created.json()["case_id"]
    assert client.get(f"/v1/cases/{case_id}", headers=HEADERS).status_code == 200
    # Тот же кейс в защищённом режиме: без журнала чтение ПДн не регистрируется, поэтому отчёт не выдаётся.
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    for url in (f"/v1/cases/{case_id}", f"/v1/cases/{case_id}/report.html"):
        answer = client.get(url, headers=HEADERS)
        assert answer.status_code == 503, url
        assert "журнал аудита не настроен" in answer.json()["detail"]
