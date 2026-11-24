"""Журнал аудита без лишних данных: основание решения разбора хранится отпечатком.

Воркшоп «Данные, безопасность и комплаенс» (Академия Финтеха, 20.11.2026): «журналы — оставить необходимое,
исключить секреты и лишние ПДн»; «удаление — поведение всей системы». Основание решения — свободный текст
сотрудника, в нём бывают фамилия клиента и номер паспорта. Журнал только дописывается и не стирается, поэтому
текст в него не пишется: в записи — HMAC на ключе журнала (без ключа — SHA-256). Текст остаётся в истории разбора
в хранилище, виден разбирающим и удаляется вместе с кейсом по основанию из 152-ФЗ.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from conftest import random_key
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore
from mandat.audit import journal
from mandat.eval import synth
from mandat.review.state_machine import ReviewCase, ReviewConflict

REASON = "Клиент Иванов Пётр Сергеевич, паспорт 4506 123456: нужен устав"
USERS = {"op": "operator", "rv": "reviewer", "root": "admin"}


def _token(name: str) -> str:
    return (name * 40)[:40]


def _h(name: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + _token(name)}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    users = json.dumps([{"name": n, "role": r, "token_sha256": hash_token(_token(n))} for n, r in USERS.items()])
    return TestClient(create_app(users=users, rate_per_min=0, store=MemoryStore(), journal_path=tmp_path / "j.jsonl"))


def _decide(client) -> str:
    case_id = client.post("/v1/cases", json=synth.build("ok", 1).inline_manifest(), headers=_h("op")).json()["case_id"]
    version = client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).json()["version"]
    opened = client.post(f"/v1/cases/{case_id}/review/open", json={"expected_version": version}, headers=_h("rv"))
    assert opened.status_code == 200, opened.text
    body = {"expected_version": opened.json()["version"], "reason": REASON}
    asked = client.post(f"/v1/cases/{case_id}/review/request_docs", json=body, headers=_h("rv"))
    assert asked.status_code == 200, asked.text
    return case_id


def test_reason_text_stays_in_the_review_and_only_its_digest_goes_to_the_journal(client, tmp_path):
    case_id = _decide(client)
    text = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    assert "Иванов" not in text and "123456" not in text
    record = next(r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "review_request_docs")
    assert record["reason"] == "sha256:" + hashlib.sha256(REASON.encode()).hexdigest()
    history = client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).json()["history"]
    assert history[-1]["reason"] == REASON, "разбирающий видит текст в истории разбора"


def test_erasing_the_case_leaves_no_reason_text_anywhere(client, tmp_path):
    case_id = _decide(client)
    assert (
        client.post(f"/v1/cases/{case_id}/erase", json={"reason": "purpose_achieved"}, headers=_h("root")).status_code
        == 200
    )
    assert "Иванов" not in (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    assert client.get(f"/v1/cases/{case_id}/review", headers=_h("rv")).status_code == 404


def test_with_a_journal_key_the_digest_is_keyed(tmp_path, monkeypatch):
    key = random_key("journal-minimisation")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", key)
    digest = journal.text_digest(REASON)
    assert digest.startswith("hmac-sha256:") and digest != journal.text_digest(REASON, key=random_key("other"))
    assert journal.text_digest("") == ""


def test_same_decision_repeated_is_not_a_conflict_and_another_reason_is(tmp_path):
    path = tmp_path / "j.jsonl"
    first = ReviewCase("c-1", "h" * 64)
    first.apply("open", actor="rv", role="reviewer", journal_path=path)
    twin = ReviewCase.from_dict(first.to_dict())
    first.apply("request_docs", actor="rv", role="reviewer", reason=REASON, journal_path=path)
    twin.apply("request_docs", actor="rv", role="reviewer", reason=REASON, journal_path=path)
    assert sum(r["action"] == "review_request_docs" for r in journal.read_journal(path)) == 1
    other = ReviewCase("c-1", "h" * 64, version=1)
    with pytest.raises(ReviewConflict):
        other.apply("request_docs", actor="rv", role="reviewer", reason="другое основание", journal_path=path)


def test_record_written_before_digests_still_matches_the_same_text(tmp_path):
    """Журнал до перехода на отпечатки хранил текст: повтор того же решения по нему не конфликт."""
    path = tmp_path / "j.jsonl"
    case = ReviewCase("c-2", "h" * 64)
    case.apply("open", actor="rv", role="reviewer", journal_path=path)
    journal.append(
        path,
        action="review_accept",
        actor="rv",
        role="reviewer",
        case_id="c-2",
        payload_hash="h" * 64,
        reason="ок",
        details={"state": "accepted", "version": 1, "actor": "rv"},
    )
    case.apply("accept", actor="rv", role="reviewer", reason="ок", journal_path=path)
    assert sum(r["action"] == "review_accept" for r in journal.read_journal(path)) == 1
