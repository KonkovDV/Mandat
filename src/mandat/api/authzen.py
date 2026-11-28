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
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from mandat.audit.status_list import known_risk

METADATA_PATH = "/.well-known/authzen-configuration"
EVALUATION_PATH = "/access/v1/evaluation"
EVALUATIONS_PATH = "/access/v1/evaluations"
PREFIX = "/access/v1/"
PDP_ENV = "MANDAT_AUTHZEN_PDP"

SUBJECT_TYPE = "representative"
RESOURCE_TYPE = "organization"
SEMANTICS = ("execute_all", "deny_on_first_deny", "permit_on_first_permit")
# Раздел 11.7: пакет ограничен, как тело запроса и глубина JSON. Каждая оценка пишет запись в журнал.
MAX_EVALUATIONS = 64
USER_REASON = "Операция не допущена: обратитесь в банк"
BAD_REQUEST = "запрос не соответствует профилю AuthZEN «Мандата»"

_INN = re.compile(r"\d{10}(?:\d{2})?")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_SAFE_INTEGER = 2**53 - 1


class _Open(BaseModel):
    """Неизвестные поля игнорируются: так требует раздел 10.1.1 ради совместимости версий."""

    model_config = ConfigDict(extra="ignore")


class Subject(_Open):
    type: str = Field(description="representative")
    id: str = Field(description="ИНН представителя: 10 или 12 цифр")
    properties: dict[str, Any] | None = None


class Resource(_Open):
    type: str = Field(description="organization")
    id: str = Field(description="ИНН организации: 10 или 12 цифр")
    properties: dict[str, Any] | None = None


class Action(_Open):
    name: str = Field(description="код операции из каталога операций")
    properties: dict[str, Any] | None = Field(
        default=None, description="operation_date — день операции по Москве (ГГГГ-ММ-ДД), amount_rub — целые рубли"
    )


class EvaluationRequest(_Open):
    """Раздел 6.1. ``context``: credential, nonce, audience и необязательный operation_risk."""

    subject: Subject
    action: Action
    resource: Resource
    context: dict[str, Any] | None = None


class PartialEvaluation(_Open):
    subject: Subject | None = None
    action: Action | None = None
    resource: Resource | None = None
    context: dict[str, Any] | None = None


class EvaluationsRequest(_Open):
    """Раздел 7.1: верхние ``subject``, ``action``, ``resource`` и ``context`` — значения по умолчанию для оценок."""

    subject: Subject | None = None
    action: Action | None = None
    resource: Resource | None = None
    context: dict[str, Any] | None = None
    evaluations: list[PartialEvaluation] | None = None
    options: dict[str, Any] | None = None


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


def _bad(cause: str) -> AuthzenError:
    return AuthzenError(400, BAD_REQUEST, cause)


def _text(value: Any, name: str, *, low: int, high: int) -> str:
    if not isinstance(value, str) or not low <= len(value) <= high:
        raise _bad(f"{name}: строка от {low} до {high} символов")
    return value


def admission_of(evaluation: EvaluationRequest) -> Admission:
    """Профиль «Мандата» для одной оценки. Нарушение — ``AuthzenError(400)``, до расхода ``nonce``."""
    if evaluation.subject.type != SUBJECT_TYPE:
        raise _bad(f"subject.type: ожидается {SUBJECT_TYPE}")
    if evaluation.resource.type != RESOURCE_TYPE:
        raise _bad(f"resource.type: ожидается {RESOURCE_TYPE}")
    if not _INN.fullmatch(evaluation.subject.id):
        raise _bad("subject.id: ИНН из 10 или 12 цифр")
    if not _INN.fullmatch(evaluation.resource.id):
        raise _bad("resource.id: ИНН из 10 или 12 цифр")
    operation = _text(evaluation.action.name, "action.name", low=1, high=64)
    props = evaluation.action.properties or {}
    day = props.get("operation_date")
    if not isinstance(day, str) or not _DAY.fullmatch(day):
        raise _bad("action.properties.operation_date: ГГГГ-ММ-ДД")
    amount = props.get("amount_rub")
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int)):
        raise _bad("action.properties.amount_rub: целое число рублей")
    if amount is not None and not 0 <= amount <= MAX_SAFE_INTEGER:
        raise _bad("action.properties.amount_rub: от 0 до 2^53−1")
    context = evaluation.context or {}
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
        organization_inn=evaluation.resource.id,
        operation=operation,
        operation_date=day,
        amount_rub=amount,
        representative_inn=evaluation.subject.id,
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


def expand(body: EvaluationsRequest) -> tuple[list[EvaluationRequest], str]:
    """Оценки пакета с подставленными значениями по умолчанию и семантика.

    Ключ оценки заменяет верхний ключ целиком (раздел 7.1.1). Оценка без subject, action или resource и без
    верхнего значения — ошибка всего пакета: она проверяется до первой оценки.
    """
    options = body.options or {}
    semantic = options.get("evaluations_semantic", "execute_all")
    if semantic not in SEMANTICS:
        raise _bad("options.evaluations_semantic: " + ", ".join(SEMANTICS))
    items = body.evaluations or []
    if len(items) > MAX_EVALUATIONS:
        raise _bad(f"evaluations: не больше {MAX_EVALUATIONS} оценок в пакете")
    out: list[EvaluationRequest] = []
    for index, item in enumerate(items or [PartialEvaluation()]):
        subject = item.subject or body.subject
        action = item.action or body.action
        resource = item.resource or body.resource
        if subject is None or action is None or resource is None:
            raise _bad(f"evaluations[{index}]: нет subject, action или resource и нет значения по умолчанию")
        context = item.context if item.context is not None else body.context
        out.append(EvaluationRequest(subject=subject, action=action, resource=resource, context=context))
    return out, semantic


def run_batch(
    evaluations: list[EvaluationRequest],
    semantic: str,
    evaluate: Callable[[EvaluationRequest], dict[str, Any]],
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
