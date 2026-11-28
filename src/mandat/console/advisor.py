"""Консультант стенда: пояснение по фактам отчёта, факты устава и справка модели через шлюз.

Без модели консультант показывает детерминированное: факты отчёта, на которые обязано ссылаться любое пояснение
(``advisory.explainer``), пояснение из них через тот же шлюз и факты устава, найденные разбором текста
(``intake.charter``). С моделью (OpenAI-совместимый адрес из настроек ``advisory/llm.py``) добавляется справка:
модель получает только обезличенные строки устава (``llm.case_charter_messages``), каждый её факт проходит шлюз
цитаты и уверенности (``advisory.gate``), а отчёт с её находками сверяется с исходным — вердикт тот же.
Сетевой вызов модели остаётся в ``advisory/llm.py``; этот модуль только держит клиента и передаёт ему запрос.

Учёт модели (``advisory/economy.py``): принятый ответ на тот же обезличенный запрос берётся из кэша без вызова;
пока открыт предохранитель или исчерпан бюджет дня, справка сразу говорит, что ответ дают правила. Исход вызова
консультант записывает сам, потому что только он видит решение шлюза: ответ не JSON, ни одного принятого факта
или ответ позже срока консоли — неудача предохранителя, ответ, принятый шлюзом целиком, — в кэш.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from mandat.advisory import llm
from mandat.advisory.economy import Economy, ModelPaused
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
# Срок соединения с моделью. Консоль ждёт справку меньше (routes.MODEL_DEADLINE_SECONDS): ответ позже её срока
# всё равно попадает в расход и в кэш, а зависший провайдер держит слот не дольше этого.
CALL_TIMEOUT_SECONDS = 30.0
# Исходы ответа, которые предохранитель считает неудачей: не JSON и ни одного факта, прошедшего шлюз.
_FAILED = {"non_json", "ungrounded"}
# Эффекты находок, которые объясняют вердикт: возражения и непроверенное.
_EXPLAINING = {"refutes", "needs_review", "not_verified"}


@dataclass(frozen=True)
class ModelState:
    enabled: bool
    model: str | None
    host: str | None
    note: str
    # Модель подключена, но сейчас её не вызывают: ``budget`` — исчерпан бюджет дня, ``breaker`` — пауза после сбоев.
    paused: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "host": self.host,
            "note": self.note,
            "paused": self.paused,
        }


def _economy(client: Any) -> Economy | None:
    meter = getattr(client, "economy", None)
    return meter if isinstance(meter, Economy) else None


def model_client() -> tuple[ModelState, Any]:
    """Состояние модели и клиент из настроек ``advisory/llm.py``. Без адреса модель выключена."""
    try:
        client = llm.OpenAICompatClient.from_env()
    except llm.LlmDisabled as exc:
        return ModelState(False, None, None, str(exc)), None
    except ValueError as exc:
        return ModelState(False, None, None, f"адрес модели не принят: {exc}"), None
    host = (urlparse(client.base_url).hostname or "").lower() or None
    meter = _economy(client)
    pause = meter.paused() if meter is not None else None
    if pause is not None:
        return ModelState(True, str(client.model), host, pause.note, pause.reason), client
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


def _redacted(case: Case) -> tuple[Any, str] | dict[str, Any]:
    """Обезличенный запрос по уставу кейса и текст, который получит модель, — или справка, почему запроса нет."""
    if case.charter is None:
        return {"label": ADVICE_LABEL, "error": "в кейсе нет устава: модели нечего читать"}
    try:
        messages = llm.case_charter_messages(case)
    except llm.MixedAlphabet:
        error = "в уставе есть слова из кириллицы и латиницы вперемешку: такой текст в модель не уходит"
        return {"label": ADVICE_LABEL, "error": error}
    return messages, messages.messages[-1]["content"]


def _judge(case: Case, raw: str, sent: str) -> tuple[dict[str, Any], str]:
    """Ответ модели через шлюз и сверка вердикта. Второе значение — исход для учёта.

    ``accepted`` — шлюз принял ответ целиком (пустой список фактов тоже ответ: ограничений нет), ``partial`` — часть
    фактов отброшена, ``ungrounded`` — ни один предложенный факт не прошёл, ``non_json`` — ответ не по формату.
    Цитата сверяется и с уставом, и с обезличенным текстом, который видела модель: в нём ФИО заменены метками.
    """
    assert case.charter is not None
    candidates = llm.parse_facts(raw)
    seen = sent.replace(llm.DATA_BEGIN, "").replace(llm.DATA_END, "")
    gate = ground_candidates(candidates, case.charter, seen=seen)
    if llm.read_facts(raw) is None:
        outcome = "non_json"
    elif candidates and not gate.accepted:
        outcome = "ungrounded"
    else:
        outcome = "partial" if gate.dropped else "accepted"
    before = verify_case(case)
    after = verify_case(case, advisory=as_advisory_findings(gate.accepted))
    advice = {
        "label": ADVICE_LABEL,
        "sent": sent,
        "proposed": len(candidates),
        "accepted": [_charter_row(c) for c in gate.accepted],
        "dropped": list(gate.dropped),
        "verdict_before": VERDICT_LABEL_RU[before.verdict],
        "verdict_after": VERDICT_LABEL_RU[after.verdict],
        "verdict_unchanged": before.verdict == after.verdict,
    }
    return advice, outcome


def _from_cache(case: Case, meter: Economy, key: str, sent: str) -> dict[str, Any] | None:
    raw = meter.cached(key)
    if raw is None:
        return None
    advice, _ = _judge(case, raw, sent)
    return {**advice, "cached": True, "seconds": 0.0}


def cached_advice(case: Case, client: Any) -> dict[str, Any] | None:
    """Справка из кэша принятых ответов без вызова модели, слота и лимита — или ``None``, если запроса там нет."""
    meter = _economy(client)
    request = _redacted(case)
    if meter is None or isinstance(request, dict):
        return None
    messages, sent = request
    return _from_cache(case, meter, client.request_key(messages), sent)


def _settle(meter: Economy, key: str, raw: str, outcome: str, *, late: bool) -> None:
    """Исход вызова в учёт: неудача или удача предохранителя; ответ, принятый шлюзом целиком, — в кэш."""
    if outcome in _FAILED or late:
        meter.failed(outcome if outcome in _FAILED else "timeout")
    else:
        meter.succeeded()
    if outcome == "accepted":
        meter.remember(key, raw)


def model_advice(case: Case, client: Any, deadline: float | None = None) -> dict[str, Any]:
    """Справка модели по уставу кейса через шлюз; вердикт сверяется с отчётом без справки.

    Принятый ответ на тот же запрос берётся из кэша. Пока открыт предохранитель или исчерпан бюджет дня, вызова нет
    и справка говорит, что ответ дают правила. Ответ позже ``deadline`` секунд — неудача предохранителя, но
    принятый шлюзом попадает в кэш: за него уже заплачено.
    """
    request = _redacted(case)
    if isinstance(request, dict):
        return request
    messages, sent = request
    meter = _economy(client)
    key = client.request_key(messages) if meter is not None else ""
    hit = _from_cache(case, meter, key, sent) if meter is not None else None
    if hit is not None:
        return hit
    if not _slots.acquire(blocking=False):
        return {
            "label": ADVICE_LABEL,
            "error": "модель отвечает на другой запрос: повторите через минуту",
            "busy": True,
        }
    started = time.perf_counter()
    try:
        raw = client.complete(messages, timeout=CALL_TIMEOUT_SECONDS)
    except ModelPaused as exc:
        return {"label": ADVICE_LABEL, "error": exc.note, "paused": exc.reason}
    except Exception as exc:  # noqa: BLE001 — сеть, тайм-аут, ответ не по формату: справки нет, вердикт тот же
        return {
            "label": ADVICE_LABEL,
            "sent": sent,
            "error": f"модель не ответила: {type(exc).__name__}",
            "seconds": round(time.perf_counter() - started, 2),
        }
    finally:
        _slots.release()
    seconds = time.perf_counter() - started
    advice, outcome = _judge(case, raw, sent)
    if meter is not None:
        _settle(meter, key, raw, outcome, late=deadline is not None and seconds > deadline)
    return {**advice, "seconds": round(seconds, 2)}


def _int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _usd(value: float) -> str:
    """Сумма в долларах по-русски: «0 $», «0,00017 $», «0,9996 $», «1,5 $». Меньше доллара — до двух значащих
    цифр после нулей, но не меньше четырёх знаков: остаток бюджета не округляется до целого доллара."""
    if value <= 0:
        return "0 $"
    places = 2 if value >= 1 else min(8, max(4, 2 - math.floor(math.log10(value))))
    return f"{value:.{places}f}".rstrip("0").rstrip(".").replace(".", ",") + " $"


def usage_rows(usage: dict[str, Any]) -> list[dict[str, str]]:
    """Строки блока «Расход модели»: вызовы, кэш, расход и остаток дня, токены, предохранитель."""
    today, breaker = usage["today"], usage["breaker"]
    calls = _int(usage["calls"])
    if usage["failures"]:
        calls += f", неудачных {_int(usage['failures'])}"
    left = f"{_usd(today['left_usd'])} из {_usd(today['cap_usd'])}"
    if today["exhausted"]:
        left += ": до 00:00 по Москве ответ дают только правила"
    state = breaker["state"]
    if state == "open":
        guard = f"пауза после сбоев подряд, следующая попытка через {breaker['retry_in_seconds']} с"
    elif state == "half_open":
        guard = "пробный вызов: модель проверяется после паузы"
    elif breaker["failures_in_row"]:
        guard = f"в норме, неудач подряд: {breaker['failures_in_row']} из {breaker['threshold']}"
    else:
        guard = "в норме"
    rows = [
        ("Вызовы модели", calls),
        ("Ответы из кэша", _int(usage["cache_hits"])),
        ("Расход сегодня", _usd(today["cost_usd"])),
        ("Осталось на день", left),
        ("Токены: запрос и ответ", f"{_int(usage['prompt_tokens'])} и {_int(usage['completion_tokens'])}"),
        ("Предохранитель", guard),
    ]
    return [{"label": label, "value": value} for label, value in rows]


def usage_view() -> dict[str, Any]:
    """Блок «Расход модели» консоли: строки «что — сколько», пояснение и сами счётчики. Без ключа, запросов и ПДн."""
    state, _ = model_client()
    usage = llm.shared_economy().snapshot()
    note = (
        "Счётчики — с запуска службы, общие для всех сессий стенда; стоимость — из ответа провайдера, день — по "
        "Москве. Ключ, запросы и ответы модели здесь не показываются."
    )
    if not state.enabled:
        note = "Модель не подключена: консультант отвечает по фактам отчёта и разбору устава, расхода нет."
    elif state.paused:
        note = f"{state.note[:1].upper()}{state.note[1:]}. {note}"
    return {"model": state.as_dict(), "rows": usage_rows(usage), "note": note, "usage": usage}


def usage_health() -> dict[str, Any]:
    """Расход модели для /health стенда. Сбой подсчёта не роняет проверку живости: вместо счётчиков — пометка."""
    try:
        state, _ = model_client()
        return {
            "enabled": state.enabled,
            "model": state.model,
            "paused": state.paused,
            **llm.shared_economy().snapshot(),
        }
    except Exception:  # noqa: BLE001 — /health отвечает сторожу службы; счётчики модели ему не обязательны
        return {"error": "счётчики модели недоступны"}


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
