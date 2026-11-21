"""Кейс с ПДн удаляется по основанию из 152-ФЗ, а удаление записано в журнал без самих данных.

Отчёты с ФИО, ИНН и СНИЛС хранились бессрочно, удалить их было нельзя: хранилище ограничивал только счётчик
в 1000 отчётов. 152-ФЗ требует уничтожить данные, если обработка неправомерна (ст. 21, ч. 3: не позднее 10 рабочих
дней), цель достигнута или согласие отозвано (ч. 4 и 5: не позднее 30 дней). При автоматизированной обработке
уничтожение подтверждают актом и выгрузкой из журнала регистрации событий (приказ Роскомнадзора № 179, п. 2 и 5).
"""

from __future__ import annotations

import json
import typing

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore, ReviewVersionConflict, SQLiteStore
from mandat.audit import journal
from mandat.eval.synth import build

TOKENS = {"admin": "a" * 40, "operator": "o" * 40, "auditor": "u" * 40}
USERS = json.dumps([{"name": role, "role": role, "token_sha256": hash_token(t)} for role, t in TOKENS.items()])
ERASE = {"reason": "unlawful_processing"}


def _as(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(params=["memory", "sqlite", "redis"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    return request.getfixturevalue("redis_store")


def test_store_erase_removes_the_report_and_the_staged_copy(store):
    store.put("c1", {"inn": "7707083893"}, "anna", 10)
    store.stage("c2", {"inn": "500100732259"}, "anna", "attempt-1", None)
    assert store.erase("c1") is True
    assert store.get("c1") is None
    assert store.erase("c1") is False
    assert store.erase("c2") is False  # отчёта ещё нет, но ожидающая запись удаляется
    assert store.pending("c2") is None
    assert all(p.case_id != "c2" for p in store.pending_all())
    assert store.commit("c2", "attempt-1", 10) is False


def test_sqlite_erase_leaves_no_copy_on_disk(tmp_path):
    store = SQLiteStore(tmp_path / "api.db")
    marker = "Иванова-Петрова 112-233-445 95"
    store.put("c1", {"person": marker}, "anna", 10)

    def on_disk() -> bytes:
        return b"".join(f.read_bytes() for f in tmp_path.iterdir() if f.is_file())

    assert marker.encode() in on_disk()
    assert store.erase("c1")
    assert marker.encode() not in on_disk()


@pytest.fixture
def api_case(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    path = tmp_path / "journal.jsonl"
    shared = MemoryStore()
    client = TestClient(api.create_app(token="", users=USERS, rate_per_min=0, store=shared, journal_path=path))
    manifest = build("ok", seed=21).inline_manifest()
    created = client.post("/v1/cases", json=manifest, headers=_as("operator"))
    assert created.status_code == 200, created.text
    return client, created.json(), path, shared, manifest


def test_admin_erases_a_case_and_the_journal_keeps_the_proof_without_the_data(api_case):
    client, report, path, _, _ = api_case
    case_id = report["case_id"]
    answer = client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=_as("admin"))
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "case_id": case_id,
        "erased": True,
        "reason": "unlawful_processing",
        "basis": api.ERASE_REASONS["unlawful_processing"],
    }
    for url in (f"/v1/cases/{case_id}", f"/v1/cases/{case_id}/report.html"):
        assert client.get(url, headers=_as("admin")).status_code == 404, url
    record = next(r for r in _records(path) if r["action"] == "erase_case")
    # Выгрузка из журнала по приказу № 179, п. 5: кто, основание, категории ПДн, система и дата.
    assert record["case_id"] == case_id and record["actor"] == "admin" and record["at"]
    assert record["payload_hash"] == report["reproducibility_hash"]
    assert "ст. 21, ч. 3" in record["reason"]
    assert record["details"]["categories"] == list(api.ERASED_CATEGORIES)
    assert record["details"]["system"] == "Мандат"
    text = json.dumps(record, ensure_ascii=False)
    assert report["requester"]["inn"] not in text and report["organization"]["inn"] not in text
    assert journal.verify_journal(path).ok


@pytest.mark.parametrize(("role", "status"), [("operator", 403), ("auditor", 403)])
def test_only_the_admin_erases(api_case, role, status):
    client, report, _, _, _ = api_case
    case_id = report["case_id"]
    assert client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=_as(role)).status_code == status
    assert client.get(f"/v1/cases/{case_id}", headers=_as("operator")).status_code == 200


@pytest.mark.parametrize("body", [{"reason": "просто так"}, {"reason": "unlawful_processing", "note": "x"}, {}])
def test_erase_reason_comes_from_the_closed_list(api_case, body):
    client, report, _, _, _ = api_case
    answer = client.post(f"/v1/cases/{report['case_id']}/erase", json=body, headers=_as("admin"))
    assert answer.status_code == 422


def test_erase_reasons_match_the_request_schema():
    annotation = api.EraseBody.model_fields["reason"].annotation
    assert set(typing.get_args(annotation)) == set(api.ERASE_REASONS)


def test_unknown_case_is_404(api_case):
    client, _, _, _, _ = api_case
    assert client.post("/v1/cases/nope/erase", json=ERASE, headers=_as("admin")).status_code == 404


def test_nothing_is_erased_when_the_journal_fails(api_case, monkeypatch):
    client, report, _, shared, _ = api_case
    real = journal.append

    def failing(path, **kwargs):
        if kwargs.get("action") == "erase_case":
            raise OSError("диск журнала недоступен")
        return real(path, **kwargs)

    monkeypatch.setattr(journal, "append", failing)
    answer = client.post(f"/v1/cases/{report['case_id']}/erase", json=ERASE, headers=_as("admin"))
    assert answer.status_code == 503
    assert "кейс не удалён" in answer.json()["detail"]
    assert shared.get(report["case_id"]) is not None


def test_protected_mode_without_a_journal_erases_nothing(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    shared = MemoryStore()
    client = TestClient(api.create_app(token="", users=USERS, rate_per_min=0, store=shared))
    created = client.post("/v1/cases", json=build("ok", seed=22).inline_manifest(), headers=_as("operator"))
    case_id = created.json()["case_id"]
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    answer = client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=_as("admin"))
    assert answer.status_code == 503
    assert "журнал аудита не настроен" in answer.json()["detail"]
    assert shared.get(case_id) is not None


def test_erased_case_leaves_the_queue_and_its_review_history(api_case):
    client, report, _, shared, manifest = api_case
    case_id = report["case_id"]
    queue = client.get("/v1/review/queue", headers=_as("admin")).json()["items"]
    assert case_id in [item["case_id"] for item in queue]
    assert client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=_as("admin")).status_code == 200
    queue = client.get("/v1/review/queue", headers=_as("admin")).json()["items"]
    assert case_id not in [item["case_id"] for item in queue]
    body, _ = shared.review_get(case_id)
    assert body["state"] == "closed" and body["history"] == [] and body["erased"] == {"reason": "unlawful_processing"}
    # С несколькими пользователями сервер даёт загрузке новый case_id: прежний остаётся закрытым.
    again = client.post("/v1/cases", json=manifest, headers=_as("operator"))
    assert again.status_code == 200, again.text
    queue = [item["case_id"] for item in client.get("/v1/review/queue", headers=_as("admin")).json()["items"]]
    assert again.json()["case_id"] in queue and case_id not in queue


def test_same_case_id_after_erasure_starts_a_new_review(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    shared = MemoryStore()
    token = {"Authorization": "Bearer " + "s" * 40}
    client = TestClient(
        api.create_app(token="s" * 40, rate_per_min=0, store=shared, journal_path=tmp_path / "journal.jsonl")
    )
    manifest = build("ok", seed=23).inline_manifest()
    case_id = client.post("/v1/cases", json=manifest, headers=token).json()["case_id"]
    assert client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=token).status_code == 200
    _, closed_version = shared.review_get(case_id)
    # Тот же case_id загрузили снова: разбор начинается заново, версия продолжает расти.
    assert client.post("/v1/cases", json=manifest, headers=token).status_code == 200
    fresh, version = shared.review_get(case_id)
    assert fresh["state"] == "queued" and version > closed_version
    assert fresh["superseded"][-1]["history"] == []


def test_store_failure_keeps_the_case_and_a_retry_erases_it(api_case, monkeypatch):
    client, report, path, shared, _ = api_case
    case_id = report["case_id"]
    real = shared.erase

    def failing(case_id):
        raise RuntimeError("хранилище недоступно")

    monkeypatch.setattr(shared, "erase", failing)
    answer = client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=_as("admin"))
    assert answer.status_code == 503
    assert "хранилище недоступно, кейс не удалён" in answer.json()["detail"]
    assert shared.get(case_id) is not None
    monkeypatch.setattr(shared, "erase", real)
    assert client.post(f"/v1/cases/{case_id}/erase", json=ERASE, headers=_as("admin")).status_code == 200
    assert shared.get(case_id) is None
    assert [r["action"] for r in _records(path)].count("erase_case") == 2  # попытка и удаление — обе в журнале


def test_review_that_keeps_changing_blocks_the_erase(api_case, monkeypatch):
    client, report, _, shared, _ = api_case

    def conflict(case_id, state, expected_version):
        raise ReviewVersionConflict(case_id, expected_version, expected_version + 1)

    monkeypatch.setattr(shared, "review_put", conflict)
    answer = client.post(f"/v1/cases/{report['case_id']}/erase", json=ERASE, headers=_as("admin"))
    assert answer.status_code == 503
    assert "разбор кейса меняется прямо сейчас" in answer.json()["detail"]
    assert shared.get(report["case_id"]) is not None
