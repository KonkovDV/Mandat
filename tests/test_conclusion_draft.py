"""Проект заключения юриста от модели банка: только через ``advisory/llm.py`` и только факты с дословной цитатой.

Модель здесь подставная: запрос строит настоящий ``case_charter_messages`` (обезличенные строки устава), ответ
записан заранее. Без модели, при сбое и при занятой модели проект пуст, а порядок утверждения тот же.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from mandat.advisory import draft, llm
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline

SENTENCE = "Сделки на сумму свыше 300 000 рублей совершаются генеральным директором только после одобрения"


@pytest.fixture
def case():
    return load_case_inline(synth.build("charter_limit", 2).inline_manifest())


@pytest.fixture
def model(monkeypatch):
    sent: list[str] = []
    answer = {
        "facts": [
            {"kind": "approval_threshold", "value": 300000, "quote": SENTENCE, "confidence": 0.92},
            {"kind": "approval_threshold", "value": 300000, "quote": SENTENCE, "confidence": 0.2},
            {"kind": "grant_right", "value": None, "quote": SENTENCE, "confidence": 0.9},
            {"kind": "joint_signature", "value": None, "quote": "Подписи ставят трое", "confidence": 0.9},
        ]
    }

    def complete(self, messages):
        sent.append(messages.messages[-1]["content"])
        return "Ответ модели: " + json.dumps(answer, ensure_ascii=False)

    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setattr(llm.OpenAICompatClient, "complete", complete)
    return sent


def test_only_grounded_confident_facts_reach_the_draft(case, model):
    result = draft.propose(case)
    assert result.origin == "advisory"
    assert [(p["kind"], p["value_rub"], p["method"]) for p in result.proposals] == [
        ("approval_threshold", 300000, "llm_advisory")
    ]
    assert result.proposals[0]["quote"] == SENTENCE and result.proposals[0]["confidence"] == 0.92
    assert result.proposals[0]["locator"].startswith("text:")
    assert sorted(result.dropped) == [
        "неизвестный вид факта 'grant_right'",
        "низкая уверенность: кейс уходит человеку",
        "цитата не найдена в документе",
    ]
    assert result.note == "модель предложила фактов: 4, шлюз цитаты пропустил: 1"
    director = case.egrul.executives[0].person.full_name.split()[0]
    assert director not in model[-1] and llm.DATA_BEGIN in model[-1]


def test_without_a_model_the_draft_is_empty(case, monkeypatch):
    monkeypatch.delenv("MANDAT_LLM_BASE_URL", raising=False)
    result = draft.propose(case)
    assert (result.origin, result.proposals) == ("case", ()) and "модель не подключена" in result.note


def test_model_failure_busy_model_and_missing_charter(case, monkeypatch):
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")

    def fail(self, messages):
        raise TimeoutError("модель молчит")

    monkeypatch.setattr(llm.OpenAICompatClient, "complete", fail)
    assert "модель не ответила (TimeoutError)" in draft.propose(case).note
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://model.example/v1")
    assert "модель не ответила (ValueError)" in draft.propose(case).note
    taken = [draft._slots.acquire(blocking=False) for _ in range(draft.MODEL_SLOTS)]
    try:
        assert "модель занята" in draft.propose(case).note
    finally:
        for ok in taken:
            if ok:
                draft._slots.release()
    assert "нет устава" in draft.propose(dataclasses.replace(case, charter=None)).note


def test_the_model_path_is_the_one_in_llm(case, model):
    facts, seen = llm.charter_candidates(case)
    assert len(facts) == 4 and seen == model[-1]
    assert seen.startswith(llm.DATA_BEGIN) and seen.rstrip().endswith(llm.DATA_END)
