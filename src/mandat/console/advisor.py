"""Консультант стенда: пояснение по фактам отчёта, факты устава и справка модели через шлюз.

Без модели консультант показывает детерминированное: факты отчёта, на которые обязано ссылаться любое пояснение
(``advisory.explainer``), пояснение из них через тот же шлюз и факты устава, найденные разбором текста
(``intake.charter``). С моделью (OpenAI-совместимый адрес из настроек ``advisory/llm.py``) добавляется справка:
модель получает только обезличенные строки устава (``llm.case_charter_messages``), каждый её факт проходит шлюз
цитаты и уверенности (``advisory.gate``), а отчёт с её находками сверяется с исходным — вердикт тот же.
Сетевой вызов модели остаётся в ``advisory/llm.py``; этот модуль только держит клиента и передаёт ему запрос.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from mandat.advisory import llm
from mandat.advisory.explainer import Fact, explain, facts_of, numbers_in
from mandat.advisory.gate import as_advisory_findings, ground_candidates
from mandat.domain.model import Case
from mandat.domain.verdict import VERDICT_LABEL_RU
from mandat.intake.charter import CandidateFact, extract_charter_facts
from mandat.services.verify import verify_case

ADVICE_LABEL = "справка, вердикт не меняет"
QUESTION = "Почему такой вердикт и что сделать дальше?"
# Одновременных запросов к модели со стенда: остальные получают «модель занята», а не очередь.
MODEL_SLOTS = 2
_slots = threading.BoundedSemaphore(MODEL_SLOTS)
# Эффекты находок, которые объясняют вердикт: возражения и непроверенное.
_EXPLAINING = {"refutes", "needs_review", "not_verified"}


@dataclass(frozen=True)
class ModelState:
    enabled: bool
    model: str | None
    host: str | None
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "model": self.model, "host": self.host, "note": self.note}


def model_client() -> tuple[ModelState, Any]:
    """Состояние модели и клиент из настроек ``advisory/llm.py``. Без адреса модель выключена."""
    try:
        client = llm.OpenAICompatClient.from_env()
    except llm.LlmDisabled as exc:
        return ModelState(False, None, None, str(exc)), None
    except ValueError as exc:
        return ModelState(False, None, None, f"адрес модели не принят: {exc}"), None
    host = (urlparse(client.base_url).hostname or "").lower() or None
    return ModelState(True, str(client.model), host, "модель подключена через OpenAI-совместимый API"), client


def _value(raw: int | Decimal | None) -> str | None:
    return None if raw is None else str(raw)


def _charter_row(fact: CandidateFact) -> dict[str, Any]:
    return {
        "kind": fact.kind,
        "value": _value(fact.value),
        "quote": fact.quote[:400],
        "method": fact.method.value,
        "confidence": round(float(fact.confidence), 2),
        "locator": fact.ref.locator,
    }


def _statements_from(facts: list[Fact], report: dict[str, Any]) -> str:
    """Черновик пояснения без модели: вердикт, возражения и шаги — каждая фраза дословно из своего факта.

    Проверки, которые профиль не выполняет (подпись и банкротство в demo), пояснение не повторяет: о них
    говорит раздел отчёта «Что подключает пилот».
    """
    waived = {name for name, row in (report.get("checks") or {}).items() if (row or {}).get("status") == "waived"}
    explaining = {
        f["finding_id"]
        for f in report.get("findings", [])
        if f.get("effect") in _EXPLAINING and f.get("check") not in waived
    }
    rows = [
        {"text": fact.text, "fact_ids": [fact.id]}
        for fact in facts
        if fact.id == "verdict" or fact.id in explaining or fact.id.startswith("step:")
    ]
    return json.dumps({"statements": rows}, ensure_ascii=False)


def deterministic(case: Case | None, report: dict[str, Any]) -> dict[str, Any]:
    """Всё, что консультант знает без модели."""
    facts = facts_of(report)
    draft = _statements_from(facts, report)
    kept, dropped = explain(QUESTION, facts, lambda _messages: draft)
    charter: dict[str, Any] = {"present": False, "facts": []}
    if case is not None and case.charter is not None:
        charter = {"present": True, "facts": [_charter_row(f) for f in extract_charter_facts(case.charter)]}
    return {
        "question": QUESTION,
        "facts": [{"id": f.id, "text": f.text} for f in facts],
        "explanation": [{"text": s.text, "fact_ids": list(s.fact_ids)} for s in kept],
        "explanation_dropped": dropped,
        "charter": charter,
    }


def model_advice(case: Case, client: Any) -> dict[str, Any]:
    """Справка модели по уставу кейса через шлюз; вердикт сверяется с отчётом без справки."""
    if case.charter is None:
        return {"label": ADVICE_LABEL, "error": "в кейсе нет устава: модели нечего читать"}
    if not _slots.acquire(blocking=False):
        return {
            "label": ADVICE_LABEL,
            "error": "модель отвечает на другой запрос: повторите через минуту",
            "busy": True,
        }
    started = time.perf_counter()
    try:
        messages = llm.case_charter_messages(case)
        sent = messages.messages[-1]["content"]
        try:
            raw = client.complete(messages)
        except Exception as exc:  # noqa: BLE001 — сеть, тайм-аут, ответ не по формату: справки нет, вердикт тот же
            return {
                "label": ADVICE_LABEL,
                "sent": sent,
                "error": f"модель не ответила: {type(exc).__name__}",
                "seconds": round(time.perf_counter() - started, 2),
            }
    finally:
        _slots.release()
    candidates = llm.parse_facts(raw)
    gate = ground_candidates(candidates, case.charter)
    before = verify_case(case)
    after = verify_case(case, advisory=as_advisory_findings(gate.accepted))
    return {
        "label": ADVICE_LABEL,
        "sent": sent,
        "proposed": len(candidates),
        "accepted": [_charter_row(c) for c in gate.accepted],
        "dropped": list(gate.dropped),
        "verdict_before": VERDICT_LABEL_RU[before.verdict],
        "verdict_after": VERDICT_LABEL_RU[after.verdict],
        "verdict_unchanged": before.verdict == after.verdict,
        "seconds": round(time.perf_counter() - started, 2),
    }


GATE_CASE = "09_charter_limit"


def gate_proof() -> dict[str, Any]:
    """Шлюзы на подставных ответах модели: ловушки должны отпасть, честные ответы — пройти.

    Ответы подставлены, решения считает настоящий код: ``gate.ground_candidates`` для фактов устава и
    ``explainer.explain`` для пояснения по фактам отчёта. Кейс — демо-кейс с порогом суммы в уставе (НА РАЗБОР).
    """
    from mandat.console.demo import demo_manifest
    from mandat.intake.case_loader import load_case_inline

    case = load_case_inline(demo_manifest(GATE_CASE))
    assert case.charter is not None
    report = verify_case(case).as_dict()
    facts = facts_of(report)
    quote = extract_charter_facts(case.charter)[0].quote
    limit = next(f for f in report["findings"] if f["rule_id"] == "R-CHR-001")
    fact = next(f for f in facts if f.id == limit["finding_id"])

    def charter(item: dict[str, Any]) -> tuple[bool, str]:
        result = ground_candidates([item], case.charter)  # type: ignore[arg-type]
        return bool(result.accepted), result.dropped[0] if result.dropped else "принят"

    known = {f.id: f.text for f in facts}

    def statement(text: str, ids: list[str]) -> tuple[bool, str]:
        raw = json.dumps({"statements": [{"text": text, "fact_ids": ids}]}, ensure_ascii=False)
        kept, _ = explain(QUESTION, facts, lambda _messages: raw)
        if kept:
            return True, "принята"
        if not ids:
            return False, "нет ссылки на факт"
        if any(i not in known for i in ids):
            return False, "ссылка на несуществующий факт"
        if not numbers_in(text) <= set().union(*(numbers_in(known[i]) for i in ids)):
            return False, "число или дата не из процитированных фактов"
        return False, "язык разрешения шире вердикта"

    fake_quote = "Сделки свыше 3 000 000 рублей одобряет совет директоров"
    traps: list[tuple[str, str, str, tuple[bool, str] | None, bool]] = [
        ("устав", "цитаты нет в уставе", fake_quote, charter(_cand(fake_quote, 0.9)), False),
        ("устав", "вид факта «выдать право»", quote, charter({**_cand(quote, 0.9), "kind": "grant_right"}), False),
        ("устав", "уверенность 1,7 вне шкалы", quote, charter(_cand(quote, 1.7)), False),
        ("устав", "уверенность строкой", quote, charter(_cand(quote, "0.9")), False),
        ("устав", "уверенность 0,2", quote, charter(_cand(quote, 0.2)), False),
        (
            "пояснение",
            "«вправе подписать» при вердикте НА РАЗБОР",
            "Представитель вправе подписать платёж",
            None,
            False,
        ),
        ("пояснение", "неверная сумма", "Сумма операции 4 800 000 ₽ выше порога устава 300 000 ₽", None, False),
        ("пояснение", "дата, которой нет в фактах", "Решение собрания нужно получить до 15.12.2026", None, False),
        ("пояснение", "фраза без ссылки на факт", "Операцию можно провести после звонка клиенту", None, False),
        ("пояснение", "ссылка на несуществующий факт", "Устав ограничений не содержит", None, False),
        ("пояснение", "«подтверждено» вопреки вердикту", "Полномочие подтверждено", None, False),
        ("устав", "порог с дословной цитатой", quote, charter(_cand(quote, 0.9)), True),
        ("пояснение", "возражение дословно из факта", fact.text, None, True),
    ]
    cites = {
        "«вправе подписать» при вердикте НА РАЗБОР": [fact.id],
        "неверная сумма": [fact.id],
        "дата, которой нет в фактах": ["step:1"],
        "фраза без ссылки на факт": [],
        "ссылка на несуществующий факт": ["F-404"],
        "«подтверждено» вопреки вердикту": ["verdict"],
        "возражение дословно из факта": [fact.id],
    }
    rows: list[dict[str, Any]] = []
    for gate_name, trap, output, result, honest in traps:
        passed, reason = result if result is not None else statement(output, cites[trap])
        rows.append(
            {
                "gate": gate_name,
                "trap": trap,
                "output": output[:200],
                "honest": honest,
                "passed": passed,
                "reason": reason,
            }
        )
    planted = [r for r in rows if not r["honest"]]
    honest_rows = [r for r in rows if r["honest"]]
    rejected = sum(not r["passed"] for r in planted)
    passed_honest = sum(r["passed"] for r in honest_rows)
    return {
        "case": GATE_CASE,
        "verdict_label": report["verdict_label"],
        "rows": rows,
        "planted": len(planted),
        "rejected": rejected,
        "honest": len(honest_rows),
        "honest_passed": passed_honest,
        "label": f"отклонено {rejected} из {len(planted)}, честные прошли {passed_honest} из {len(honest_rows)}",
    }


def _cand(quote: str, confidence: object) -> dict[str, Any]:
    return {"kind": "approval_threshold", "value": 300000, "quote": quote, "confidence": confidence}
