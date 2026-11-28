"""Допуск операции по OpenID AuthZEN Authorization API 1.0 (Final Specification, 12.01.2026).

AuthZEN — открытый стандарт обмена между точкой применения решения (PEP: ДБО, шлюз API, антифрод) и точкой
принятия решения (PDP). «Мандат» отвечает здесь как PDP на вопрос «может ли этот представитель провести эту
операцию за эту организацию по этому удостоверению». Решение считает та же функция, что стоит за
``POST /v1/operations/authorize`` (``services.admission.decide_detailed``): стандарт меняет только конверт.

Профиль «Мандата» поверх информационной модели AuthZEN (раздел 5):

* ``subject`` — ``{"type": "representative", "id": "<ИНН представителя>"}``;
* ``resource`` — ``{"type": "organization", "id": "<ИНН организации>"}``;
* ``action`` — ``{"name": "<код операции>", "properties": {"operation_date": "ГГГГ-ММ-ДД", "amount_rub": 480000}}``;
* ``context`` — ``{"credential": {…удостоверение…}, "nonce": "…", "audience": "…", "operation_risk": "payment"}``.

Отказ — ``200`` и ``{"decision": false}`` с причинами в ``context`` (раздел 5.5); ошибки запроса — ``400``, без токена —
``401`` с ``WWW-Authenticate``, чужая роль — ``403`` (раздел 10.1.2). Поля вне профиля игнорируются (10.1.1), JSON
читается как I-JSON (11.5). Пакетная оценка (раздел 7) поддерживает три семантики; ошибка одной оценки — её
``decision: false`` с ``context.error``, ошибка структуры пакета — ``400`` до первой оценки, поэтому ни один ``nonce``
не расходуется на запросе, который всё равно отвергнут. Поиск субъектов, ресурсов и действий (раздел 8) не
реализован: PDP без состояния не знает, кто ещё вправе действовать за организацию, и в метаданных этих адресов нет.
Ответ не подписывается (раздел 11.6 это допускает): подлинность решения держат TLS и журнал «Мандата».

Профиль читает обычный JSON и не зависит от FastAPI. Схема тела HTTP для документации API — в ``api/app.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from mandat.audit.status_list import known_risk

METADATA_PATH = "/.well-known/authzen-configuration"
EVALUATION_PATH = "/access/v1/evaluation"
EVALUATIONS_PATH = "/access/v1/evaluations"
PREFIX = "/access/v1/"
PDP_ENV = "MANDAT_AUTHZEN_PDP"

SUBJECT_TYPE = "representative"
RESOURCE_TYPE = "organization"
PARTS = ("subject", "action", "resource", "context")
SEMANTICS = ("execute_all", "deny_on_first_deny", "permit_on_first_permit")
# Раздел 11.7: пакет ограничен, как тело запроса и глубина JSON. Каждая оценка пишет запись в журнал.
MAX_EVALUATIONS = 64
USER_REASON = "Операция не допущена: обратитесь в банк"
BAD_REQUEST = "запрос не соответствует профилю AuthZEN «Мандата»"

_INN = re.compile(r"\d{10}(?:\d{2})?")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_SAFE_INTEGER = 2**53 - 1


class AuthzenError(ValueError):
    """Ошибка запроса AuthZEN. ``status`` — код HTTP или ``context.error.status`` одной оценки."""

    def __init__(self, status: int, message: str, cause: str) -> None:
        super().__init__(cause)
        self.status, self.message, self.cause = status, message, cause


@dataclass(frozen=True)
class Admission:
    """Запрос допуска «Мандата», собранный из одной оценки AuthZEN."""

    credential: dict[str, Any]
    organization_inn: str
    operation: str
    operation_date: str
    amount_rub: int | None
    representative_inn: str
    audience: str
    nonce: str
    operation_risk: str | None

    def fields(self) -> dict[str, Any]:
        """Запрос операции в полях ``POST /v1/operations/authorize`` (``admission.admit``)."""
        return {
            "organization_inn": self.organization_inn,
            "operation": self.operation,
            "operation_date": self.operation_date,
            "amount_rub": self.amount_rub,
            "representative_inn": self.representative_inn,
            "audience": self.audience,
            "nonce": self.nonce,
        }


def _bad(cause: str) -> AuthzenError:
    return AuthzenError(400, BAD_REQUEST, cause)


def _text(value: Any, name: str, *, low: int, high: int) -> str:
    if not isinstance(value, str) or not low <= len(value) <= high:
        raise _bad(f"{name}: строка от {low} до {high} символов")
    return value


def _object(value: Any, name: str, *, optional: bool = False) -> dict[str, Any]:
    if value is None and optional:
        return {}
    if not isinstance(value, dict):
        raise _bad(f"{name}: объект JSON")
    return value


def _inn(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _INN.fullmatch(value):
        raise _bad(f"{name}: ИНН из 10 или 12 цифр")
    return value


def admission_of(evaluation: Mapping[str, Any]) -> Admission:
    """Профиль «Мандата» для одной оценки — объекта JSON по разделу 6.1. Нарушение — ``AuthzenError(400)``, до
    расхода ``nonce``. Поля вне профиля не читаются (раздел 10.1.1)."""
    subject = _object(evaluation.get("subject"), "subject")
    resource = _object(evaluation.get("resource"), "resource")
    action = _object(evaluation.get("action"), "action")
    if subject.get("type") != SUBJECT_TYPE:
        raise _bad(f"subject.type: ожидается {SUBJECT_TYPE}")
    if resource.get("type") != RESOURCE_TYPE:
        raise _bad(f"resource.type: ожидается {RESOURCE_TYPE}")
    representative_inn = _inn(subject.get("id"), "subject.id")
    organization_inn = _inn(resource.get("id"), "resource.id")
    operation = _text(action.get("name"), "action.name", low=1, high=64)
    props = _object(action.get("properties"), "action.properties", optional=True)
    day = props.get("operation_date")
    if not isinstance(day, str) or not _DAY.fullmatch(day):
        raise _bad("action.properties.operation_date: ГГГГ-ММ-ДД")
    amount = props.get("amount_rub")
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int)):
        raise _bad("action.properties.amount_rub: целое число рублей")
    if amount is not None and not 0 <= amount <= MAX_SAFE_INTEGER:
        raise _bad("action.properties.amount_rub: от 0 до 2^53−1")
    context = _object(evaluation.get("context"), "context", optional=True)
    credential = context.get("credential")
    if not isinstance(credential, dict):
        raise _bad("context.credential: удостоверение «Мандата» — объект")
    risk = context.get("operation_risk")
    if risk is not None:
        risk = _text(risk, "context.operation_risk", low=1, high=32)
        if not known_risk(risk):
            raise _bad("context.operation_risk: уровня нет в карте окон свежести")
    return Admission(
        credential=credential,
        organization_inn=organization_inn,
        operation=operation,
        operation_date=day,
        amount_rub=amount,
        representative_inn=representative_inn,
        audience=_text(context.get("audience"), "context.audience", low=1, high=200),
        nonce=_text(context.get("nonce"), "context.nonce", low=16, high=200),
        operation_risk=risk,
    )


def decision(result: dict[str, Any]) -> dict[str, Any]:
    """Решение AuthZEN (раздел 5.5) по ответу допуска «Мандата». Пустые поля не пишутся (раздел 11.5)."""
    context: dict[str, Any] = {"checked_at": result["checked_at"]}
    if result.get("already_admitted"):
        context["already_admitted"] = True
        if result.get("admitted_at"):
            context["admitted_at"] = result["admitted_at"]
    if not result["allowed"]:
        problems = list(result["problems"])
        context["problems"] = problems
        # Ключи по образцу примера раздела 5.5.2.1: для администратора — причины, для клиента — общий текст.
        context["reason_admin"] = {"403": "; ".join(problems)}
        context["reason_user"] = {"403": USER_REASON}
    return {"decision": bool(result["allowed"]), "context": context}


def failed(error: AuthzenError) -> dict[str, Any]:
    """Ошибка одной оценки пакета: решение закрыто, причина — в ``context.error`` (раздел 7.2.1)."""
    return {"decision": False, "context": {"error": {"status": error.status, "message": error.message}}}


def expand(body: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Оценки пакета (раздел 7.1, тело уже прошло схему HTTP) с подставленными значениями по умолчанию и семантика.

    Ключ оценки заменяет верхний ключ целиком (раздел 7.1.1). Оценка без subject, action или resource и без
    верхнего значения — ошибка всего пакета: она проверяется до первой оценки.
    """
    options = body.get("options") or {}
    semantic = options.get("evaluations_semantic", "execute_all")
    if semantic not in SEMANTICS:
        raise _bad("options.evaluations_semantic: " + ", ".join(SEMANTICS))
    items = body.get("evaluations") or []
    if len(items) > MAX_EVALUATIONS:
        raise _bad(f"evaluations: не больше {MAX_EVALUATIONS} оценок в пакете")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items or [{}]):
        merged = {key: item[key] if item.get(key) is not None else body.get(key) for key in PARTS}
        if any(merged[key] is None for key in ("subject", "action", "resource")):
            raise _bad(f"evaluations[{index}]: нет subject, action или resource и нет значения по умолчанию")
        out.append(merged)
    return out, semantic


def run_batch(
    evaluations: list[dict[str, Any]],
    semantic: str,
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Оценки по порядку. ``deny_on_first_deny`` останавливается на первом отказе, ``permit_on_first_permit`` — на
    первом допуске; остальные оценки не выполняются и ``nonce`` не расходуют (раздел 7.1.2.1)."""
    results: list[dict[str, Any]] = []
    for evaluation in evaluations:
        try:
            result = evaluate(evaluation)
        except AuthzenError as exc:
            result = failed(exc)
        results.append(result)
        if semantic == "deny_on_first_deny" and not result["decision"]:
            break
        if semantic == "permit_on_first_permit" and result["decision"]:
            break
    return results


def check_pdp_identifier(value: str) -> str:
    """Идентификатор PDP (раздел 9.1.1): адрес https без запроса и фрагмента. Хвостовая косая черта снимается."""
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment or "#" in value or "?" in value:
        raise ValueError(f"{PDP_ENV}: нужен адрес https без запроса и фрагмента, например https://mandat.bank.local")
    return value.strip().rstrip("/")


def metadata(pdp: str) -> dict[str, Any]:
    """Метаданные PDP (раздел 9.2.2): только реализованные адреса. Поиска нет — его адресов в ответе тоже нет."""
    return {
        "policy_decision_point": pdp,
        "access_evaluation_endpoint": pdp + EVALUATION_PATH,
        "access_evaluations_endpoint": pdp + EVALUATIONS_PATH,
    }
