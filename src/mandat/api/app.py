"""HTTP API «Мандата».

Доступ по токену (заголовок ``Authorization: Bearer …``). Два режима:

* стенд: один ``MANDAT_API_TOKEN``, его владелец — администратор, ``case_id`` задаёт клиент
  (при заданном ``MANDAT_API_USERS`` этот токен не действует);
* пользователи: ``MANDAT_API_USERS`` (см. ``api/principals.py``), у каждого роль, ``case_id`` выдаёт сервер,
  а кейс видит только тот, кто его создал, плюс аудитор и администратор.

Без токенов API отвечает только на /health и /readyz: закрыто по умолчанию.
Swagger на /docs включается флагом ``MANDAT_API_DOCS=1``. Частота запросов ограничена
``MANDAT_API_RATE_PER_MIN`` (по умолчанию 120 в минуту на адрес клиента, 0 выключает). Адрес берётся
из соединения, ``X-Forwarded-For`` не читается: за прокси лимит надо ставить на самом прокси.
Окно лимита и отчёты лежат в хранилище ``MANDAT_API_STORE`` (``api/storage.py``): по умолчанию в памяти
процесса, для нескольких воркеров — SQLite или Redis.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import time
import traceback
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from mandat import ENGINE, __version__
from mandat.advisory import draft as advisory_draft
from mandat.api import authzen
from mandat.api import conclusions as conclusion_api
from mandat.api import review as review_api
from mandat.api.principals import Directory, Principal, load_users
from mandat.api.sealing import SealError, sealed
from mandat.api.storage import (
    ConclusionVersionConflict,
    Idem,
    Pending,
    ReportQuotaExceeded,
    ReviewVersionConflict,
    Store,
    open_store,
)
from mandat.audit import journal
from mandat.audit.attestation import NONCE_MIN_CHARS, OperationRequest
from mandat.audit.status_list import known_risk
from mandat.domain.conclusion import ID as CONCLUSION_ID
from mandat.domain.conclusion import Conclusion, ogrn_ok
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.report.html import render
from mandat.review import conclusions as lifecycle
from mandat.review.state_machine import (
    JOURNAL_ACTION,
    ReviewConflict,
    ReviewError,
    ReviewForbidden,
    ReviewInvalid,
    ReviewState,
)
from mandat.rules.pack import default_catalog
from mandat.services import admission
from mandat.services.env_policy import PROFILES, ProfileNotAllowed, protected, resolve_profile
from mandat.services.monitoring import MOSCOW, NOTICE_DAYS, evaluate, parse_as_of, parse_events
from mandat.services.next_steps import check_sla
from mandat.services.verify import verify_case
from mandat.strictjson import MAX_JSON_DEPTH, strict_loads
from mandat.strictjson import depth_exceeds as json_depth_exceeds

MAX_BODY = 8 * 1024 * 1024
OPEN_PATHS = {"/health", "/readyz"}
# Глубина вложенности JSON — MAX_JSON_DEPTH в strictjson. Глубокий массив в 2 КБ вызывал RecursionError (500).
# OWASP Secure Headers Project и ASVS 5.0 V3.4: ответы с ПДн не кэшируются, не встраиваются во фреймы,
# не уходят с Referer. CSP по умолчанию ставится, только если маршрут не задал свою (HTML-карточка).
SECURITY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]
CARD_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
DEFAULT_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")


# OWASP API8:2023, ASVS 5.0 V16: клиент получает общий текст и request_id, подробности — только
# в журнале сервера, и без ПДн: значения полей из ошибок схемы не пишутся, длинные числа (ИНН, ОГРН,
# СНИЛС, паспорт) и адреса почты маскируются.
log = logging.getLogger("mandat.api")
REQUEST_ID_HEADER = b"x-request-id"
# Номер от пяти цифр — и сплошной, и по группам через пробел, дефис, тире, точку, косую черту или скобки:
# карта «4111 1111 1111 1111», паспорт «45 06 123456», телефон «+7 (903) 123-45-67», СНИЛС «112-233-445 95».
# Раньше маска ловила только сплошные цифры, и номер по группам уходил в журнал сервера как есть.
# PCI DSS 4.0, требование 3.5.1: номер карты нечитаем везде, где хранится, в том числе в журналах. Дата и время
# тоже попадают под маску: для журнала сервера это допустимая цена, дата рождения — тоже персональные данные.
_DIGITS = re.compile(r"\+?\d(?:[ \t().\-\u2010-\u2015/]*\d){4,}")
# Адрес начинается с начала слова: без привязки маска пробовала каждую позицию строки без «@» и сканировала
# хвост заново — квадратичное время на тексте клиента в причине ошибки.
_EMAIL = re.compile(r"(?<![^\s@'\"])[^\s@'\"]+@[^\s@'\"]+")
# Запас за пределом журнала: номер или адрес на границе обрезки маскируется целиком, а не наполовину.
_SCRUB_MARGIN = 256


def scrub(text: str, limit: int = 300) -> str:
    """Текст для журнала сервера: без номеров от пяти цифр (в том числе по группам) и адресов почты, не длиннее
    ``limit``. Маска работает на обрезанном тексте: причина ошибки может нести мегабайты ввода клиента."""
    head = text[: limit + _SCRUB_MARGIN]
    return _EMAIL.sub("<email>", _DIGITS.sub("<num>", head))[:limit]


# Идентификатор запроса от вызывающей системы (AuthZEN 1.0, раздел 10.1.3: PDP возвращает тот же X-Request-ID).
# Принимается только из безопасного алфавита: UUID, ULID, base64url, W3C traceparent. Иначе — свой случайный.
CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._~:+/=-]{1,128}")


def request_id(scope: MutableMapping[str, Any]) -> str:
    state = scope.setdefault("state", {})
    if "request_id" not in state:
        offered = next(
            (v.decode("latin-1").strip() for k, v in scope.get("headers", []) if k.lower() == REQUEST_ID_HEADER),
            "",
        )
        if CLIENT_REQUEST_ID.fullmatch(offered):
            state["request_id"], state["request_id_from_client"] = offered, True
        else:
            state["request_id"] = secrets.token_hex(8)
    return str(state["request_id"])


def logged_request_id(scope: MutableMapping[str, Any]) -> str:
    """Для журнала сервера: присланный идентификатор проходит ту же маску номеров, что путь и причина."""
    rid = request_id(scope)
    return scrub(rid, 128) if (scope.get("state") or {}).get("request_id_from_client") else rid


def problem(request: Request, status: int, detail: str, cause: str) -> JSONResponse:
    rid = request_id(request.scope)
    # Путь задаёт клиент: кейс, названный по ИНН, иначе оставлял бы номер в журнале сервера.
    path = scrub(request.url.path)
    log.warning(
        "request_id=%s status=%s path=%s cause=%s", logged_request_id(request.scope), status, path, scrub(cause)
    )
    headers = {k.decode(): v.decode() for k, v in [*SECURITY_HEADERS, DEFAULT_CSP]}
    headers[REQUEST_ID_HEADER.decode()] = rid
    return JSONResponse({"detail": detail, "request_id": rid}, status_code=status, headers=headers)


class AdmissionOperation(BaseModel):
    """Запрос одной операции, к которому привязано удостоверение. ИНН представителя в журнал не пишется."""

    model_config = ConfigDict(extra="forbid")

    organization_inn: str = Field(pattern=r"^\d{10}(\d{2})?$")
    operation: str = Field(min_length=1, max_length=64)
    operation_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="день операции по Москве")
    amount_rub: StrictInt | None = Field(default=None, ge=0, le=2**53 - 1, description="целые рубли")
    representative_inn: str = Field(pattern=r"^\d{10}(\d{2})?$")
    audience: str = Field(min_length=1, max_length=200, description="система банка, для которой выпущено удостоверение")
    nonce: str = Field(min_length=NONCE_MIN_CHARS, max_length=200, description="одноразовый номер запроса")


class AdmissionBody(BaseModel):
    """Тело POST /v1/operations/authorize: удостоверение «Мандата» и запрос операции."""

    model_config = ConfigDict(extra="forbid")

    credential: dict[str, Any]
    request: AdmissionOperation
    operation_risk: str | None = Field(
        default=None,
        max_length=32,
        description="уровень риска из карты окон (payment, high, default…): окно свежести списка статусов задаёт "
        "подписанная операция, это поле может его только сузить; слово не из карты — 422",
    )


# Основания удаления кейса: закрытый список вместо свободного текста, чтобы в журнал не попали ПДн.
ERASE_REASONS = {
    "unlawful_processing": "152-ФЗ, ст. 21, ч. 3: обработка неправомерна; уничтожить не позднее 10 рабочих дней",
    "purpose_achieved": "152-ФЗ, ст. 21, ч. 4: цель обработки достигнута; уничтожить не позднее 30 дней",
    "consent_withdrawn": "152-ФЗ, ст. 21, ч. 5: согласие отозвано; уничтожить не позднее 30 дней",
    "subject_request": "152-ФЗ, ст. 14, ч. 1: данные незаконно получены или не нужны для цели обработки",
}
# Категории ПДн в отчёте кейса: их называет выгрузка из журнала (приказ Роскомнадзора № 179, п. 5).
ERASED_CATEGORIES = ("фамилия, имя, отчество", "ИНН", "СНИЛС", "должность")


class EraseBody(BaseModel):
    """Тело POST /v1/cases/{id}/erase: основание удаления из закрытого списка."""

    model_config = ConfigDict(extra="forbid")

    reason: Literal["unlawful_processing", "purpose_achieved", "consent_withdrawn", "subject_request"]


class ReviewAction(BaseModel):
    """Действие разбора. ``expected_version`` — поле ``version`` из GET /v1/cases/{id}/review."""

    expected_version: int = Field(ge=0)
    reason: str = Field(default="", max_length=4000)
    # Только для действия require: специализация, без которой кейс не решить.
    specialization: str | None = Field(default=None, max_length=64)


MAX_AMOUNT = 2**53 - 1


class RestrictionBody(BaseModel):
    """Ограничение устава в заключении юриста: вид, порог в рублях, «включительно» и цитата устава."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval_threshold", "joint_signature", "large_deal_approval"]
    value_rub: StrictInt | None = Field(default=None, ge=1, le=MAX_AMOUNT)
    inclusive: StrictBool = True
    quote: str | None = Field(default=None, max_length=400)


class ConclusionDraft(BaseModel):
    """Тело POST /v1/conclusions: черновик заключения юриста по одной редакции устава (docs/CONCLUSIONS.md)."""

    model_config = ConfigDict(extra="forbid")

    ogrn: str = Field(pattern=r"^\d{13}$", description="ОГРН юрлица — область заключения")
    charter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="sha256 документа устава — его редакция")
    outcome: Literal["no_restriction", "restriction"]
    restrictions: list[RestrictionBody] = Field(default_factory=list, max_length=5)
    basis: str = Field(min_length=1, max_length=4000, description="какие положения прочитаны и почему такой вывод")
    operations: list[str] = Field(default_factory=list, max_length=20, description="коды операций; пусто — все")
    max_amount_rub: StrictInt | None = Field(default=None, ge=1, le=MAX_AMOUNT, description="предел суммы")
    valid_until: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="последний день действия, не дальше года")


class ConclusionEdit(BaseModel):
    """Тело POST /v1/conclusions/{id}/confirm: автор подтверждает черновик, заданные поля заменяют проект."""

    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=0)
    outcome: Literal["no_restriction", "restriction"] | None = None
    restrictions: list[RestrictionBody] | None = Field(default=None, max_length=5)
    basis: str | None = Field(default=None, min_length=1, max_length=4000)
    operations: list[str] | None = Field(default=None, max_length=20)
    max_amount_rub: StrictInt | None = Field(default=None, ge=1, le=MAX_AMOUNT)
    valid_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ConclusionAction(BaseModel):
    """Тело POST /v1/conclusions/{id}/approve и /revoke. ``expected_version`` — поле ``version`` записи."""

    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=0)
    reason: str = Field(default="", max_length=4000)


class ConclusionFromCase(BaseModel):
    """Тело POST /v1/cases/{id}/conclusion-draft: документы того же кейса, чтобы модель банка прочла устав."""

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any] | None = Field(default=None, description="манифест кейса; без него проект пуст")


class ApiProblem(Exception):
    """Отказ с общим текстом для клиента; причина ``cause`` уходит только в журнал сервера."""

    def __init__(self, status: int, detail: str, cause: str) -> None:
        super().__init__(cause)
        self.status, self.detail, self.cause = status, detail, cause


async def strict_json_object(request: Request) -> None:
    """Тело POST — объект I-JSON. Повтор ключа и NaN, которые ``json.loads`` принимает, здесь отказ.

    Клиенту уходит тот же общий текст, что и при ошибке схемы: в ответе и в журнале нет тела запроса.
    """
    try:
        value = strict_loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiProblem(422, "запрос не соответствует схеме", "тело не является строгим JSON") from exc
    if not isinstance(value, dict):
        raise ApiProblem(422, "запрос не соответствует схеме", "тело должно быть объектом")


async def strict_json_authzen(request: Request) -> None:
    """Тело запроса AuthZEN — объект I-JSON (раздел 11.5). Нарушение — 400, а не 422 (раздел 10.1.2)."""
    try:
        await strict_json_object(request)
    except ApiProblem as exc:
        raise ApiProblem(400, authzen.BAD_REQUEST, exc.cause) from exc


def bearer_challenge(token_sent: bool) -> str:
    """``WWW-Authenticate`` для 401 по RFC 6750, разд. 3: без токена — только схема, с неверным — invalid_token."""
    return 'Bearer realm="mandat", error="invalid_token"' if token_sent else 'Bearer realm="mandat"'


class Guard:
    """Чистое ASGI-звено перед приложением: лимит частоты, токен, размер тела — именно в этом порядке.

    Токен проверяется по заголовкам до чтения тела: без токена сервер не читает ни байта
    запроса. Тело читается потоком со счётчиком и отвергается с 413, как только превысит
    ``MAX_BODY``, даже без ``Content-Length`` (chunked). ``BaseHTTPMiddleware`` с
    ``await request.body()`` здесь нельзя: он буферизует всё тело до проверок.

    В режиме стенда ``console`` (``mandat.console.routes.ConsoleGate``) забирает пути консоли: вместо токена — её
    проверки метода, типа тела и ``Origin``, свой потолок тела; лимит частоты тот же. Ко всем ответам стенда
    добавляются HSTS и ``X-Robots-Tag: noindex``.
    """

    def __init__(self, app: Any, *, directory: Directory, limit: int, store: Store, console: Any = None) -> None:
        self.app, self.directory, self.limit, self.store, self.console = app, directory, limit, store, console

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        console = self.console is not None and self.console.owns(path)
        stand_headers = list(self.console.extra_headers) if self.console is not None else []
        max_body = self.console.max_body if console else MAX_BODY

        rid = request_id(scope).encode("ascii")

        async def secured(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = [(k, v) for k, v in message.get("headers", []) if k.lower() != REQUEST_ID_HEADER]
                names = {k.lower() for k, _ in existing}
                # Заголовок, заданный маршрутом (CSP карточки, кэш файлов консоли), не дублируется общим.
                common = [(k, v) for k, v in [*SECURITY_HEADERS, *stand_headers, DEFAULT_CSP] if k not in names]
                message = {**message, "headers": [*existing, *common, (REQUEST_ID_HEADER, rid)]}
            await send(message)

        async def reply(status: int, detail: str, extra: dict[str, str] | None = None) -> None:
            # Отказы (401, 405, 413, 429) идут с теми же заголовками безопасности, что и обычные ответы.
            await JSONResponse({"detail": detail}, status_code=status, headers=extra)(scope, receive, secured)

        if path in OPEN_PATHS and method not in {"GET", "HEAD"}:
            # Открытые пути без токена и без лимита частоты отвечают только на GET: тело здесь не читается.
            await reply(405, "метод не поддерживается", {"Allow": "GET, HEAD"})
            return
        if console:
            refused = self.console.screen(str(method), path, headers)
            if refused is not None:
                await reply(*refused)
                return
        if path not in OPEN_PATHS:
            if self.limit > 0:
                client = (scope.get("client") or ("unknown",))[0]
                if not self.store.allow(str(client), self.limit):
                    await reply(429, "слишком много запросов", {"Retry-After": "60"})
                    return
            if not console and not self.directory.configured:
                await reply(503, "API закрыт: задайте MANDAT_API_TOKEN или MANDAT_API_USERS")
                return
            scheme, _, value = headers.get("authorization", "").partition(" ")
            if not console and (
                scheme.lower() != "bearer" or not value.strip() or self.directory.find(value.strip()) is None
            ):
                await reply(401, "нужен токен доступа", {"WWW-Authenticate": bearer_challenge(bool(value.strip()))})
                return
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit():
                await reply(400, "некорректный Content-Length")
                return
            if int(length) > max_body:
                await reply(413, "запрос больше допустимого")
                return
        messages: list[dict[str, Any]] = []
        total = 0
        if method not in {"GET", "HEAD", "OPTIONS"}:
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    messages.append(message)
                    break
                total += len(message.get("body", b""))
                if total > max_body:
                    await reply(413, "запрос больше допустимого")
                    return
                messages.append(message)
                if not message.get("more_body", False):
                    break

        if messages and json_depth_exceeds(b"".join(m.get("body", b"") for m in messages)):
            await reply(400, f"слишком глубокая вложенность JSON (более {MAX_JSON_DEPTH})")
            return

        async def replay() -> dict[str, Any]:
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, secured)


# Квота отчётов одного пользователя в хранилище. Без потолка поток POST /v1/cases съедает память (OWASP API4:2023);
# потолок на владельца, а не общий, иначе один пользователь вытеснял бы чужие кейсы и опустошал очередь разбора.
MAX_REPORTS = 1000
QUOTA_DETAIL = "квота кейсов пользователя исчерпана: все кейсы в ней ждут разбора, новый кейс не сохранён"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# Сколько держится заявка на создание кейса: дольше, чем занимает проверка и запись в журнал.
CREATE_CLAIM_SECONDS = 30.0
# Idempotency-Key: безопасный набор символов, чтобы ключ не попал в журнал/логи как произвольный текст.
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._~-]{1,128}")


COMMIT = re.compile(r"[0-9a-f]{7,40}")


def build_stamp() -> str | None:
    """Коммит, из которого собран сервер: ``MANDAT_COMMIT`` или файл ``BUILD`` в рабочем каталоге.

    ``BUILD`` пишет scripts/deploy_stand.sh: по ``/health`` видно, какой коммит репозитория работает на стенде.
    """
    raw = os.environ.get("MANDAT_COMMIT", "").strip()
    if not raw:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            raw = Path("BUILD").read_text(encoding="utf-8").strip()
    raw = raw[:40].lower()
    return raw if COMMIT.fullmatch(raw) else None


def readiness(store: Store, journal_path: Path | None) -> dict[str, str]:
    """Проверки для /readyz. Значения: ok | fail | not_configured (последнее только вне pilot/prod)."""
    from mandat.services.env_policy import key_problem

    checks: dict[str, str] = {}
    try:
        store.ping()
        checks["store"] = "ok"
    except Exception:
        checks["store"] = "fail"
    if protected() and store.name == "memory":
        checks["store"] = "fail"  # pilot/prod: память процесса не переживает рестарт
    if journal_path is None:
        checks["journal"] = "fail" if protected() else "not_configured"
    else:
        try:
            parent = journal_path.parent
            parent.mkdir(parents=True, exist_ok=True)
            ok = os.access(parent, os.W_OK) and (not journal_path.exists() or os.access(journal_path, os.W_OK))
            checks["journal"] = "ok" if ok else "fail"
        except OSError:
            checks["journal"] = "fail"
    try:
        checks["keys"] = "fail" if key_problem() else "ok"
    except Exception:
        checks["keys"] = "fail"
    return checks


def create_app(
    token: str | None = None,
    *,
    users: str | None = None,
    docs: bool | None = None,
    rate_per_min: int | None = None,
    store: Store | None = None,
    journal_path: Path | None = None,
    admission_config: admission.AdmissionConfig | None = None,
    authzen_pdp: str | None = None,
    console: bool | None = None,
) -> FastAPI:
    """Приложение API. ``console`` (по умолчанию флаг ``MANDAT_STAND``) добавляет консоль стенда; в pilot и prod —
    ``StandRefused``: стенд с демо-учётными записями в защищённом режиме не поднимается."""
    from mandat import console as stand

    with_console = stand.requested() if console is None else console
    if with_console and (reason := stand.refusal()):
        raise stand.StandRefused(reason)
    expected = token if token is not None else os.environ.get("MANDAT_API_TOKEN", "")
    raw_users = (users if users is not None else os.environ.get("MANDAT_API_USERS", "")).strip()
    users_file = Path(raw_users) if raw_users and not raw_users.startswith("[") else None
    sessions = None
    if with_console:
        from mandat.console.sessions import Sessions, StandDirectory

        sessions = Sessions()
        directory: Directory = StandDirectory(load_users(users), sessions, reload_path=users_file)
    else:
        directory = Directory(load_users(users), expected, reload_path=users_file)
    show_docs = docs if docs is not None else _env_flag("MANDAT_API_DOCS")
    limit = rate_per_min if rate_per_min is not None else int(os.environ.get("MANDAT_API_RATE_PER_MIN", "120"))
    app = FastAPI(
        title="Мандат API",
        version=__version__,
        docs_url="/docs" if show_docs else None,
        openapi_url="/openapi.json" if show_docs else None,
        redoc_url=None,
    )
    check_sla()  # неверный MANDAT_REVIEW_SLA_HOURS не даёт стартовать
    # Хранилище из окружения шифруется ключом MANDAT_STORE_KEY; переданное явно — как есть.
    shared = store if store is not None else sealed(open_store())
    env_journal = os.environ.get("MANDAT_API_JOURNAL", "").strip()
    audit_log = journal_path if journal_path is not None else (Path(env_journal) if env_journal else None)
    # Допуск операции включается связкой ключей издателя; настроенный не полностью — ошибка старта, а не 500.
    # На стенде удостоверения выпускает демо-издатель консоли, и допуск проверяет его ключ.
    issuer = None
    if with_console and admission_config is None:
        from mandat.console.issuer import DemoIssuer

        workdir = os.environ.get(stand.WORKDIR_ENV, "").strip()
        issuer = DemoIssuer(Path(workdir) if workdir else None)
        admission_config = issuer.config()
    admit = admission_config or admission.AdmissionConfig.from_env(protected=protected())
    raw_pdp = (authzen_pdp if authzen_pdp is not None else os.environ.get(authzen.PDP_ENV, "")).strip()
    pdp_identifier = authzen.check_pdp_identifier(raw_pdp) if raw_pdp else None

    gate = None
    if with_console:
        from mandat.console.routes import ConsoleGate

        gate = ConsoleGate()
    app.add_middleware(Guard, directory=directory, limit=limit, store=shared, console=gate)

    @app.exception_handler(RequestValidationError)
    async def schema_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Только место и тип ошибки: поле «input» может содержать ПДн.
        where = "; ".join(f"{'.'.join(map(str, e.get('loc', ())))}:{e.get('type', '')}" for e in exc.errors())
        if request.url.path.startswith(authzen.PREFIX):
            # AuthZEN 1.0, раздел 10.1.1: без обязательного атрибута — Bad Request.
            return problem(request, 400, authzen.BAD_REQUEST, where)
        return problem(request, 422, "запрос не соответствует схеме", where)

    @app.exception_handler(ApiProblem)
    async def api_problem(request: Request, exc: ApiProblem) -> JSONResponse:
        return problem(request, exc.status, exc.detail, exc.cause)

    @app.exception_handler(SealError)
    async def seal_error(request: Request, exc: SealError) -> JSONResponse:
        # Запись хранилища подменена, переставлена или не зашифрована: данных из неё клиент не получает.
        return problem(request, 500, "запись хранилища не прошла проверку целостности", f"SealError: {exc}")

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> JSONResponse:
        frames = " < ".join(
            f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
        )
        return problem(request, 500, "внутренняя ошибка сервера", f"{type(exc).__name__}: {exc} @ {frames}")

    def auth(authorization: str = Header(default="")) -> Principal:
        if not directory.configured:
            raise HTTPException(503, "API закрыт: задайте MANDAT_API_TOKEN или MANDAT_API_USERS")
        scheme, _, value = authorization.partition(" ")
        principal = directory.find(value.strip()) if scheme.lower() == "bearer" and value.strip() else None
        if principal is None:
            challenge = bearer_challenge(bool(value.strip()))
            raise HTTPException(401, "нужен токен доступа", headers={"WWW-Authenticate": challenge})
        return principal

    def writer(principal: Principal = Depends(auth)) -> Principal:
        if not principal.can_write:
            raise HTTPException(403, f"роль {principal.role} только читает")
        return principal

    def admitter(principal: Principal = Depends(auth)) -> Principal:
        """Кто получает допуск операции.

        В pilot и prod — только ``dbo``: служебная учётная запись системы банка, которая проводит операцию.
        Сотрудник допуск к платежу не получает: разделение обязанностей и минимальные привилегии (приказ
        ФСТЭК № 21, меры УПД.4 и УПД.5; ГОСТ Р 57580.1). На стенде допуск доступен и ролям с правом записи.
        """
        if principal.role == "dbo" or (principal.can_write and not protected()):
            return principal
        raise ApiProblem(
            403,
            "допуск операции выдаётся только учётной записи системы банка (роль dbo)",
            f"admission: role {principal.role}",
        )

    def visible(case_id: str, principal: Principal) -> dict[str, Any]:
        # Чужой кейс неотличим от несуществующего: 404, а не 403.
        found = shared.get(case_id)
        if found is None or not (principal.sees_all or found[1] == principal.name):
            raise HTTPException(404, "кейс не найден")
        return found[0]

    commit = build_stamp()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine": ENGINE,
            "auth_configured": directory.configured,
            "mode": "users" if directory.multi_user else "single-token",
            "store": shared.name,
            "commit": commit,
            "console": with_console,
        }

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Готовность (не живость): журнал, хранилище, ключи. 200 только если все проверки прошли.

        В теле только имена проверок и ok/fail, без путей и текстов исключений. Это проверка конфигурации и
        доступности на момент запроса, а не гарантия сохранности данных.
        """
        checks = readiness(shared, audit_log)
        ready = all(v in ("ok", "not_configured") for v in checks.values())
        body = {"status": "ready" if ready else "not_ready", "checks": checks}
        return JSONResponse(body, status_code=200 if ready else 503)

    def journal_has(pend: Pending) -> bool:
        """В журнале есть запись именно этой попытки. Ошибка чтения журнала — исключение, а не «нет»."""
        assert audit_log is not None
        for rec in journal.read_journal(audit_log):
            details = rec.get("details") or {}
            if (
                rec.get("action") == "verify"
                and rec.get("case_id") == pend.case_id
                and details.get("attempt") == pend.attempt
                and rec.get("payload_hash") == pend.report.get("reproducibility_hash")
            ):
                return True
        return False

    def reconcile_one(pend: Pending, *, force: bool = False) -> str:
        """Свести ожидающую запись с журналом после сбоя.

        Запись журнала этой попытки есть → отчёт подтверждается (журнал — источник истины, клиент мог
        не получить 200). Записи нет → попытка отбрасывается: клиенту 200 не уходил. Свежие записи
        (моложе заявки на создание) не трогаем без ``force``: их может писать живой запрос другого
        воркера. Если журнал не читается, ничего не решаем.
        """
        if not force and time.time() - pend.staged_at < CREATE_CLAIM_SECONDS:
            return "skipped"
        if audit_log is not None:
            try:
                recorded = journal_has(pend)
            except (ValueError, OSError, journal.JournalKeyMissing, journal.JournalError):
                return "skipped"
            if recorded:
                evicted: list[str] = []
                try:
                    done = shared.commit(pend.case_id, pend.attempt, MAX_REPORTS, evicted=evicted)
                except ReportQuotaExceeded:
                    return "over_quota"  # ожидающая запись снята: опубликовать её некуда
                journal_evictions(evicted, pend.owner, "reconcile", pend.case_id)
                return "committed" if done else "superseded"
        return "discarded" if shared.discard(pend.case_id, pend.attempt) else "superseded"

    def reconcile_all() -> dict[str, int]:
        counts: dict[str, int] = {}
        for pend in shared.pending_all():
            outcome = reconcile_one(pend)
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    app.state.reconcile = reconcile_all
    # После падения процесса: подтвердить или отбросить то, что осталось «в полёте». Хранилище может быть
    # недоступно при старте, тогда сверка повторится при следующем запросе по этому кейсу.
    with contextlib.suppress(Exception):
        reconcile_all()

    @app.post("/v1/cases", dependencies=[Depends(strict_json_object)])
    def create_case(
        manifest: dict[str, Any],
        profile: str | None = None,
        replace: bool = False,
        principal: Principal = Depends(writer),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if profile is not None and profile not in PROFILES:
            raise HTTPException(422, "profile: demo или strict")
        try:
            resolved = resolve_profile(profile)
        except ProfileNotAllowed as exc:
            raise HTTPException(403, str(exc)) from exc
        if idempotency_key is None:
            return _create(manifest, resolved, replace, principal, None)
        key = idempotency_key.strip()
        if not IDEMPOTENCY_KEY.fullmatch(key):
            raise HTTPException(422, "Idempotency-Key: 1–128 символов из A-Z a-z 0-9 . _ ~ -")
        canonical = json.dumps(
            {"manifest": manifest, "profile": profile, "replace": replace}, sort_keys=True, ensure_ascii=False
        )
        idem = Idem(principal.name, key, hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        lease = f"idem:{idem.owner}:{idem.key}"
        if not shared.first_seen(lease, CREATE_CLAIM_SECONDS):
            raise HTTPException(409, "запрос с этим Idempotency-Key уже выполняется: повторите позже")
        try:
            stale = shared.pending_for_idem(idem.owner, idem.key)
            if stale is not None:
                reconcile_one(stale, force=True)  # прошлая попытка с этим ключом оборвалась посреди записи
            done = shared.idem_get(idem.owner, idem.key)
            if done is not None:
                if done[1] != idem.fingerprint:
                    raise HTTPException(422, "Idempotency-Key уже использован с другим запросом")
                if shared.get(done[0]) is None:
                    raise HTTPException(409, "результат запроса с этим Idempotency-Key больше не хранится")
                # Повтор снова отдаёт отчёт с ПДн: это чтение, как GET, с проверкой видимости и записью.
                return read_logged(done[0], principal, "read_case", replay=True)
            return _create(manifest, resolved, replace, principal, idem)
        finally:
            shared.release(lease)

    def _create(
        manifest: dict[str, Any], profile: str, replace: bool, principal: Principal, idem: Idem | None
    ) -> dict[str, Any]:
        client_ref = manifest.get("case_id")
        if directory.multi_user:
            manifest = {**manifest, "case_id": f"case-{secrets.token_hex(8)}"}
        try:
            case = load_case_inline(manifest)
        except CaseError as exc:
            raise ApiProblem(422, "кейс не прошёл проверку входных данных", f"CaseError: {exc}") from exc
        # Строгий профиль читает реестр заключений юриста по уставам организации кейса (docs/CONCLUSIONS.md).
        conclusions = registry_for(case) if profile == "strict" else ()
        registry: dict[str, Any] = {"conclusions": conclusions} if conclusions else {}
        # В pilot и prod снимки источников сверяются с часами сервера: снимок «из будущего» не доказательство.
        if protected():
            report = verify_case(case, profile=profile, now=datetime.now(UTC), **registry).as_dict()
        else:
            report = verify_case(case, profile=profile, **registry).as_dict()
        case_id = report["case_id"]
        # Квота владельца проверяется до записи в журнал; окончательно её держит commit.
        try:
            has_room = shared.room(principal.name, case_id, MAX_REPORTS)
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно, кейс не сохранён", f"store: {type(exc).__name__}") from exc
        if not has_room:
            raise ApiProblem(429, QUOTA_DETAIL, f"quota: {MAX_REPORTS}")
        # Атомарная заявка на создание (в том числе при replace=true): из одновременных запросов с одним
        # case_id дальше проходит один, остальные получают 409, а не перезаписывают друг друга. Заявка
        # держится только на время записи: после успеха или любой ошибки она снимается, и честный повтор
        # проходит сразу. CREATE_CLAIM_SECONDS — страховка на случай, если процесс упал посреди записи.
        claim = f"create:{case_id}"
        if not shared.first_seen(claim, CREATE_CLAIM_SECONDS):
            raise HTTPException(409, "кейс с таким case_id сейчас записывается другим запросом: повторите позже")
        try:
            return _store(case_id, report, principal, client_ref, replace, idem)
        finally:
            shared.release(claim)

    def _store(
        case_id: str,
        report: dict[str, Any],
        principal: Principal,
        client_ref: Any,
        replace: bool,
        idem: Idem | None,
    ) -> dict[str, Any]:
        # Заявка у нас в руках, значит живой записи этого кейса нет: оставшееся «в полёте» — след сбоя.
        stale = shared.pending(case_id)
        if stale is not None:
            reconcile_one(stale, force=True)
        existing = shared.get(case_id)
        if existing is not None and not replace:
            raise HTTPException(409, "кейс с таким case_id уже есть: передайте replace=true, чтобы перезаписать")
        if existing is not None and not (principal.sees_all or existing[1] == principal.name):
            raise HTTPException(409, "кейс с таким case_id принадлежит другому пользователю")
        if directory.multi_user and client_ref is not None:
            report = {**report, "client_case_id": str(client_ref)}
        if audit_log is None and protected():
            raise HTTPException(503, "журнал аудита не настроен: задайте MANDAT_API_JOURNAL")
        # Порядок: stage в хранилище → запись журнала → commit. Отчёт виден (GET) и 200 уходит только после
        # commit. Сбой между шагами оставляет «ожидающую» запись, которую сверка (reconcile) подтверждает, если
        # запись журнала этой попытки есть, и отбрасывает, если её нет. Это не распределённая транзакция и не
        # exactly-once: журнал и хранилище остаются двумя системами, а повтор без Idempotency-Key после
        # сбоя может получить 409 вместо повторного ответа.
        attempt = secrets.token_hex(16)
        try:
            shared.stage(case_id, report, principal.name, attempt, idem)
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно, кейс не сохранён", f"store: {type(exc).__name__}") from exc
        applied = (report.get("charter_conclusions") or {}).get("applied") or []
        if audit_log is not None:
            try:
                # Применённое заключение юриста — своя запись журнала до записи проверки: опубликованный отчёт
                # всегда идёт после неё, а попытка, которую не опубликовали, оставляет только эти записи.
                for row in applied:
                    lifecycle.record_apply(
                        audit_log,
                        actor=actor_of(principal),
                        case_id=case_id,
                        report_hash=report["reproducibility_hash"],
                        applied=row,
                        attempt=attempt,
                    )
                details: dict[str, Any] = {
                    "verdict": report["verdict"],
                    "profile": report["profile"],
                    "via": "api",
                    "attempt": attempt,
                }
                if applied:
                    details["conclusions"] = [str(row.get("id")) for row in applied]
                journal.append(
                    audit_log,
                    action="verify",
                    actor=principal.name,
                    role=principal.role,
                    case_id=case_id,
                    payload_hash=report["reproducibility_hash"],
                    details=details,
                )
            except (ValueError, OSError, journal.JournalKeyMissing) as exc:
                with contextlib.suppress(Exception):
                    shared.discard(case_id, attempt)
                cause = f"journal: {type(exc).__name__}: {exc}"
                raise ApiProblem(503, "журнал аудита недоступен, кейс не сохранён", cause) from exc
        evicted: list[str] = []
        try:
            committed = shared.commit(case_id, attempt, MAX_REPORTS, evicted=evicted)
        except ReportQuotaExceeded as exc:
            # Место заняли между проверкой и записью: попытка снята, в журнале остаётся только запись verify.
            raise ApiProblem(429, QUOTA_DETAIL, f"quota: {MAX_REPORTS}, at commit") from exc
        except Exception as exc:
            # Запись в журнале уже есть, отчёт ждёт подтверждения: клиенту не 200, сверка завершит позже.
            raise ApiProblem(
                503, "хранилище недоступно: кейс записан в журнал, но не подтверждён", f"store: {type(exc).__name__}"
            ) from exc
        if not committed:
            raise HTTPException(409, "запись вытеснена другой попыткой: повторите запрос")
        journal_evictions(evicted, principal.name, principal.role, case_id)
        registry_after(case_id, report, principal)
        # Кейс без автоматического решения сразу встаёт в очередь разбора. При replace=true разбор начинается
        # заново по новому отчёту: прежний разбор уходит в «superseded» и в журнал (review_reset).
        # Сбой записи не отменяет кейс: разбор создастся при первом действии, но до того кейса нет в очереди,
        # поэтому сбой пишется в журнал сервера.
        try:
            seed_review(case_id, report, principal)
        except Exception as exc:
            log.warning("review_seed_failed case_id=%s error=%s", case_id, type(exc).__name__)
        return report

    operation_codes = frozenset(default_catalog().operations)

    def actor_of(principal: Principal) -> lifecycle.Actor:
        return lifecycle.Actor(principal.name, principal.role, principal.specialization)

    def registry_rows(ogrn: str) -> list[conclusion_api.Row]:
        """Заключения организации. Подменённая запись — ``SealError`` (500), недоступное хранилище — 503."""
        try:
            return shared.conclusion_list(ogrn)
        except SealError:
            raise
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно", f"store: {type(exc).__name__}") from exc

    def registry_for(case: Any) -> tuple[Conclusion, ...]:
        """Заключения юриста для проверки кейса. Нечитаемая запись не применяется и называется в журнале сервера."""
        ogrn = case.organization.ogrn
        if not ogrn_ok(ogrn):
            return ()
        parsed, broken = conclusion_api.for_rules(registry_rows(ogrn))
        for cid in broken:
            log.warning("conclusion_unreadable id=%s", scrub(cid))
        return parsed

    def registry_after(case_id: str, report: dict[str, Any], principal: Principal) -> None:
        """После публикации отчёта: счётчики применений и перепроверка заключений прежней редакции устава.

        Отчёт уже опубликован, поэтому сбой здесь ответ не меняет, а уходит в журнал сервера. Перепроверку повторит
        следующий кейс клиента с той же новой редакцией.
        """
        section = report.get("charter_conclusions")
        if not section:
            return
        status = ((report.get("checks") or {}).get("charter") or {}).get("status")
        cleared = status not in {"review", "not_verified", "refuted", "missing"}
        for row in section.get("applied") or []:
            try:
                shared.conclusion_tally(str(row.get("id")), cleared)
            except Exception as exc:  # noqa: BLE001 — счётчик для отчётности, не доказательство
                log.warning("conclusion_tally_failed id=%s error=%s", scrub(str(row.get("id"))), type(exc).__name__)
        charter = next((r.get("sha256") for r in report.get("inputs") or [] if r.get("kind") == "charter_text"), None)
        for row in section.get("previous") or []:
            if row.get("status") != "approved" or not charter:
                continue
            try:
                recheck_one(str(row.get("id")), case_id, str(charter), principal)
            except Exception as exc:  # noqa: BLE001 — перепроверку повторит следующий кейс с этой редакцией
                log.warning("conclusion_recheck_failed id=%s error=%s", scrub(str(row.get("id"))), type(exc).__name__)

    def recheck_one(conclusion_id: str, case_id: str, charter: str, principal: Principal) -> None:
        found = shared.conclusion_get(conclusion_id)
        if found is None or found[0].get("status") != "approved":
            return
        body, version = found
        new = lifecycle.recheck(
            body,
            version,
            actor=actor_of(principal),
            case_id=case_id,
            charter_sha256=charter,
            now=datetime.now(UTC),
            journal_path=audit_log,
        )
        with contextlib.suppress(ConclusionVersionConflict):
            shared.conclusion_put(conclusion_id, new, version)

    def journal_evictions(evicted: list[str], actor: str, role: str, for_case: str) -> None:
        """Отчёты, вытесненные квотой владельца ради ``for_case``, — в журнал: кейс уходит из хранилища не молча.

        Отчёт уже опубликован, поэтому сбой этой записи ответ не меняет, а уходит в журнал сервера.
        """
        for gone in evicted:
            if audit_log is None:
                log.warning("report_evicted case_id=%s", scrub(gone))
                continue
            try:
                journal.append(
                    audit_log,
                    action="evict_case",
                    actor=actor,
                    role=role,
                    case_id=gone,
                    reason="квота отчётов владельца: вытеснен самый старый кейс с закрытым разбором",
                    details={"via": "api", "quota": MAX_REPORTS, "for_case": for_case},
                )
            except (ValueError, OSError, journal.JournalKeyMissing) as exc:
                log.warning("evict_journal_failed case_id=%s error=%s", scrub(gone), type(exc).__name__)

    def seed_review(case_id: str, report: dict[str, Any], principal: Principal) -> None:
        fresh = review_api.initial(case_id, report, principal.name)
        stored = shared.review_get(case_id)
        if stored is None:
            with contextlib.suppress(ReviewVersionConflict):
                shared.review_put(case_id, fresh, 0)
            return
        previous, version = stored
        if previous.get("report_hash") == fresh["report_hash"] and not previous.get("erased"):
            return  # тот же отчёт: разбор продолжается; после удаления кейса разбор начинается заново
        fresh = review_api.reset(fresh, previous, version)
        if audit_log is not None:
            journal.append(
                audit_log,
                action="review_reset",
                actor=principal.name,
                role=principal.role,
                case_id=case_id,
                payload_hash=fresh["report_hash"],
                reason="replace=true: новый отчёт, разбор начат заново",
                details={
                    "version": version,
                    "previous_state": previous.get("state"),
                    "previous_report_hash": previous.get("report_hash"),
                },
            )
        shared.review_put(case_id, fresh, version)

    def close_review_for_erase(case_id: str, reason: str) -> None:
        """Разбор удалённого кейса закрывается без истории: в ней бывают слова сотрудников о человеке.

        Версия продолжает расти, поэтому решения по прежним версиям не спутать с новыми, если кейс с тем же
        ``case_id`` загрузят снова: ``seed_review`` тогда начнёт разбор заново.
        """
        for attempt in range(3):
            stored = shared.review_get(case_id)
            if stored is None:
                return
            previous, version = stored
            if previous.get("erased"):
                return
            keep = ("case_id", "report_hash", "created_by", "required_specialization")
            tombstone: dict[str, Any] = {key: previous.get(key) for key in keep}
            tombstone.update(state=ReviewState.CLOSED.value, history=[], superseded=[], erased={"reason": reason})
            try:
                shared.review_put(case_id, tombstone, version)
                return
            except ReviewVersionConflict:
                if attempt == 2:
                    raise

    def read_logged(case_id: str, principal: Principal, action: str, *, replay: bool = False) -> dict[str, Any]:
        """Отчёт кейса для чтения; чтение пишется в журнал аудита.

        В отчёте ФИО, ИНН и СНИЛС людей, поэтому кто, когда и какой кейс открыл, — событие безопасности, как
        создание кейса и допуск: 152-ФЗ, ст. 19, ч. 2; приказ ФСТЭК № 21, меры группы РСБ; ГОСТ Р 57580.1.
        Журнал недоступен — отчёт не выдаётся: чтение ПДн без записи о нём недоказуемо. Без журнала отчёт
        выдаётся только на стенде; в защищённом режиме — 503, как и при создании кейса. В записи — только
        идентификатор кейса, роль и хеш отчёта, без самих данных. ``replay`` — отчёт отдаёт повтор запроса
        по ``Idempotency-Key``: это тоже чтение, и в записи это видно.
        """
        report = visible(case_id, principal)
        if audit_log is None:
            if protected():
                raise HTTPException(503, "журнал аудита не настроен: задайте MANDAT_API_JOURNAL")
            return report
        try:
            journal.append(
                audit_log,
                action=action,
                actor=principal.name,
                role=principal.role,
                case_id=case_id,
                payload_hash=report.get("reproducibility_hash"),
                details={"via": "api", "replay": "idempotency_key"} if replay else {"via": "api"},
            )
        except (ValueError, OSError, journal.JournalKeyMissing) as exc:
            cause = f"journal: {type(exc).__name__}"
            raise ApiProblem(503, "журнал аудита недоступен, отчёт не выдан", cause) from exc
        return report

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str, principal: Principal = Depends(auth)) -> dict[str, Any]:
        return read_logged(case_id, principal, "read_case")

    @app.get("/v1/cases/{case_id}/report.html", response_class=HTMLResponse)
    def get_case_html(case_id: str, principal: Principal = Depends(auth)) -> HTMLResponse:
        return HTMLResponse(
            render(read_logged(case_id, principal, "read_report")),
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
            },
        )

    @app.post("/v1/cases/{case_id}/erase", dependencies=[Depends(strict_json_object)])
    def erase_case(case_id: str, body: EraseBody, principal: Principal = Depends(writer)) -> dict[str, Any]:
        """Удалить отчёт кейса с ПДн по основанию из 152-ФЗ. Только администратор.

        Порядок: запись в журнал, закрытие разбора без истории, удаление отчёта. Сбой журнала — ничего не удалено.
        В журнале остаются кейс, хеш отчёта, основание, категории ПДн и система, без самих данных: это выгрузка из
        журнала, которой вместе с актом подтверждают уничтожение (приказ Роскомнадзора № 179, п. 2 и 5). Резервные
        копии и снимки хранилища банк уничтожает своей процедурой.
        """
        if principal.role != "admin":
            raise HTTPException(403, "удалить кейс может только администратор")
        report = visible(case_id, principal)
        if audit_log is None and protected():
            raise HTTPException(503, "журнал аудита не настроен: задайте MANDAT_API_JOURNAL")
        basis = ERASE_REASONS[body.reason]
        if audit_log is not None:
            try:
                journal.append(
                    audit_log,
                    action="erase_case",
                    actor=principal.name,
                    role=principal.role,
                    case_id=case_id,
                    payload_hash=report.get("reproducibility_hash"),
                    reason=basis,
                    details={
                        "reason": body.reason,
                        "categories": list(ERASED_CATEGORIES),
                        "system": "Мандат",
                        "via": "api",
                    },
                )
            except (ValueError, OSError, journal.JournalKeyMissing) as exc:
                cause = f"journal: {type(exc).__name__}"
                raise ApiProblem(503, "журнал аудита недоступен, кейс не удалён", cause) from exc
        try:
            close_review_for_erase(case_id, body.reason)
            shared.erase(case_id)
        except ReviewVersionConflict as exc:
            raise ApiProblem(503, "разбор кейса меняется прямо сейчас: повторите удаление", "review: conflict") from exc
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно, кейс не удалён", f"store: {type(exc).__name__}") from exc
        return {"case_id": case_id, "erased": True, "reason": body.reason, "basis": basis}

    def review_target(case_id: str, principal: Principal) -> tuple[dict[str, Any], str]:
        # Свои кейсы видит каждый, auditor и admin — все (только чтение разбора). reviewer видит чужой кейс, пока тот
        # в разборе, или если уже действовал по нему (docs/RUN.md): автоматическое решение чужого кейса ему не видно.
        found = shared.get(case_id)
        if found is not None and (principal.sees_all or found[1] == principal.name):
            return found
        if found is not None and principal.role == "reviewer":
            body, _ = review_state(case_id, *found)
            runs = [body, *(body.get("superseded") or [])]
            acted = any(h.get("actor") == principal.name for run in runs for h in run.get("history") or [])
            if acted or body.get("state") in {s.value for s in review_api.OPEN_STATES}:
                return found
        raise HTTPException(404, "кейс не найден")

    def review_state(case_id: str, report: dict[str, Any], owner: str) -> tuple[dict[str, Any], int]:
        try:
            stored = shared.review_get(case_id)
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно", f"store: {type(exc).__name__}") from exc
        return stored if stored is not None else (review_api.initial(case_id, report, owner), 0)

    @app.get("/v1/cases/{case_id}/review")
    def get_review(case_id: str, principal: Principal = Depends(auth)) -> dict[str, Any]:
        """Состояние разбора и версия, которую надо передать в ``expected_version``."""
        report, owner = review_target(case_id, principal)
        body, version = review_state(case_id, report, owner)
        return review_api.view(case_id, body, version)

    # Тело разбора тоже I-JSON: повтор «reason» иначе молча брал последнее значение в журнал.
    @app.post("/v1/cases/{case_id}/review/{action}", dependencies=[Depends(strict_json_object)])
    def review_action(
        case_id: str, action: str, payload: ReviewAction, principal: Principal = Depends(writer)
    ) -> dict[str, Any]:
        """Действие разбора: appeal, open, accept, reject, request_docs, close, require (docs/REVIEW.md).

        403 — роль, специализация или независимость решения; 409 — переход недопустим или версия устарела;
        422 — нет основания; 503 — журнал не настроен в pilot/prod или хранилище недоступно.
        """
        if action not in JOURNAL_ACTION:
            raise HTTPException(404, f"действие разбора: {', '.join(JOURNAL_ACTION)}")
        if audit_log is None and protected():
            raise HTTPException(503, "журнал аудита не настроен: задайте MANDAT_API_JOURNAL")
        report, owner = review_target(case_id, principal)
        body, version = review_state(case_id, report, owner)
        if payload.expected_version != version:
            raise HTTPException(409, f"версия {payload.expected_version} устарела, сейчас {version}")
        case = review_api.load(body, version)
        try:
            case.apply(
                action,
                actor=principal.name,
                role=principal.role,
                reason=payload.reason,
                journal_path=audit_log,
                expected_version=version,
                specialization=principal.specialization,
                required_specialization=payload.specialization,
            )
        except ReviewForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except ReviewConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ReviewInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        except ReviewError as exc:
            raise ApiProblem(503, "журнал аудита недоступен, решение не принято", f"journal: {exc}") from exc
        except (ValueError, OSError, journal.JournalKeyMissing) as exc:
            raise ApiProblem(503, "журнал аудита недоступен, решение не принято", f"journal: {exc}") from exc
        new_body = review_api.dump(case, body)
        try:
            new_version = shared.review_put(case_id, new_body, version)
        except ReviewVersionConflict as exc:
            raise HTTPException(409, f"версия {exc.expected} устарела, сейчас {exc.current}") from exc
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно, решение не сохранено", f"store: {type(exc).__name__}") from exc
        return review_api.view(case_id, new_body, new_version)

    @app.get("/v1/review/queue")
    def review_queue(principal: Principal = Depends(auth)) -> dict[str, Any]:
        """Кейсы, ждущие человека, по сроку (раньше — выше). operator видит только свои."""
        try:
            rows = shared.review_list()
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно", f"store: {type(exc).__name__}") from exc
        open_states = {s.value for s in review_api.OPEN_STATES}
        items = []
        for case_id, body, version in rows:
            if body.get("state") not in open_states:
                continue
            if not (principal.sees_all or principal.role == "reviewer" or body.get("created_by") == principal.name):
                continue
            item = review_api.view(case_id, body, version)
            item.pop("history")
            item.pop("superseded")
            # Кейс в разборе квота не вытесняет. Если отчёта всё же нет (копия, сбой), кейс не прячется из очереди.
            if shared.get(case_id) is None:
                item["report_missing"] = True
            items.append(item)
        # Просроченные выше, внутри — по поднятому приоритету, затем по сроку.
        items.sort(
            key=lambda i: (not i["overdue"], i["effective_priority"] or "P9", i["due_at"] or "9999", i["case_id"])
        )
        return {"items": items, "states": sorted(open_states), "auto_decided": ReviewState.AUTO_DECIDED.value}

    # --- Реестр заключений юриста по уставам (docs/CONCLUSIONS.md) ----------------------------------------------

    def conclusion_reader(principal: Principal = Depends(auth)) -> Principal:
        """Читают реестр сотрудники и аудитор; учётная запись системы банка (dbo) — нет."""
        if principal.role == "dbo":
            raise ApiProblem(403, "учётная запись системы банка заключения юриста не читает", "conclusions: dbo")
        return principal

    def conclusion_journal() -> None:
        if audit_log is None and protected():
            raise HTTPException(503, "журнал аудита не настроен: задайте MANDAT_API_JOURNAL")

    def conclusion_problem(exc: lifecycle.ConclusionError) -> Exception:
        if isinstance(exc, lifecycle.ConclusionForbidden):
            return HTTPException(403, str(exc))
        if isinstance(exc, lifecycle.ConclusionConflict):
            return HTTPException(409, str(exc))
        if isinstance(exc, lifecycle.ConclusionInvalid):
            return HTTPException(422, str(exc))
        return ApiProblem(503, "журнал аудита недоступен, заключение не записано", f"journal: {exc}")

    def found_conclusion(conclusion_id: str) -> tuple[dict[str, Any], int]:
        if not CONCLUSION_ID.fullmatch(conclusion_id):
            raise HTTPException(404, "заключение не найдено")
        try:
            found = shared.conclusion_get(conclusion_id)
        except SealError:
            raise
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно", f"store: {type(exc).__name__}") from exc
        if found is None:
            raise HTTPException(404, "заключение не найдено")
        return found

    def conclusion_view(conclusion_id: str, body: dict[str, Any], version: int) -> dict[str, Any]:
        rows = registry_rows(str(body.get("ogrn")))
        try:
            tally = shared.conclusion_tallies([conclusion_id]).get(conclusion_id, (0, 0))
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно", f"store: {type(exc).__name__}") from exc
        replaced = next((cid for cid, other, _ in rows if conclusion_id in (other.get("supersedes") or [])), None)
        return conclusion_api.view(
            conclusion_id, body, version, day=conclusion_api.today(), tally=tally, superseded_by=replaced
        )

    def keep_conclusion(conclusion_id: str, body: dict[str, Any], expected_version: int) -> dict[str, Any]:
        """Записать заключение по ожидаемой версии и вернуть его вид. Журнал уже записан вызывающим."""
        try:
            version = shared.conclusion_put(conclusion_id, body, expected_version)
        except ConclusionVersionConflict as exc:
            raise HTTPException(409, f"версия {exc.expected} устарела, сейчас {exc.current}") from exc
        except SealError:
            raise
        except Exception as exc:
            raise ApiProblem(
                503, "хранилище недоступно, заключение не сохранено", f"store: {type(exc).__name__}"
            ) from exc
        return conclusion_view(conclusion_id, body, version)

    def room_for(ogrn: str) -> None:
        if len(registry_rows(ogrn)) >= conclusion_api.MAX_PER_OGRN:
            raise ApiProblem(
                429,
                f"в реестре организации уже {conclusion_api.MAX_PER_OGRN} заключений: прежние архивирует банк",
                f"conclusions: quota {conclusion_api.MAX_PER_OGRN}",
            )

    @app.post("/v1/conclusions", dependencies=[Depends(strict_json_object)])
    def create_conclusion(payload: ConclusionDraft, principal: Principal = Depends(writer)) -> dict[str, Any]:
        """Черновик заключения по редакции устава. Пишет operator или reviewer, утверждает другой юрист.

        403 — роль; 422 — поля вывода; 429 — реестр организации полон; 503 — журнал или хранилище.
        """
        conclusion_journal()
        room_for(payload.ogrn)
        cid = lifecycle.new_id()
        try:
            body = lifecycle.create(
                payload.model_dump(),
                actor=actor_of(principal),
                conclusion_id=cid,
                now=datetime.now(UTC),
                operations=operation_codes,
                journal_path=audit_log,
            )
        except lifecycle.ConclusionError as exc:
            raise conclusion_problem(exc) from exc
        except (ValueError, OSError, journal.JournalKeyMissing) as exc:
            raise ApiProblem(503, "журнал аудита недоступен, заключение не записано", f"journal: {exc}") from exc
        return keep_conclusion(cid, body, 0)

    @app.get("/v1/conclusions")
    def list_conclusions(ogrn: str, principal: Principal = Depends(conclusion_reader)) -> dict[str, Any]:
        """Заключения организации по ОГРН, новые сверху: статус на сегодня, вывод, область, число применений."""
        if not ogrn_ok(ogrn):
            raise HTTPException(422, "ogrn: 13 цифр ОГРН юрлица с верным контрольным числом")
        rows = registry_rows(ogrn)
        try:
            tallies = shared.conclusion_tallies([cid for cid, _, _ in rows])
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно", f"store: {type(exc).__name__}") from exc
        day = conclusion_api.today()
        return {"ogrn": ogrn, "today": day.isoformat(), "items": conclusion_api.listing(rows, day=day, tallies=tallies)}

    @app.get("/v1/conclusions/{conclusion_id}")
    def get_conclusion(conclusion_id: str, principal: Principal = Depends(conclusion_reader)) -> dict[str, Any]:
        body, version = found_conclusion(conclusion_id)
        return conclusion_view(conclusion_id, body, version)

    def conclusion_step(conclusion_id: str, step: Callable[[dict[str, Any], int], dict[str, Any]]) -> dict[str, Any]:
        conclusion_journal()
        body, version = found_conclusion(conclusion_id)
        try:
            new = step(body, version)
        except lifecycle.ConclusionError as exc:
            raise conclusion_problem(exc) from exc
        except (ValueError, OSError, journal.JournalKeyMissing) as exc:
            raise ApiProblem(503, "журнал аудита недоступен, заключение не записано", f"journal: {exc}") from exc
        return keep_conclusion(conclusion_id, new, version)

    @app.post("/v1/conclusions/{conclusion_id}/confirm", dependencies=[Depends(strict_json_object)])
    def confirm_conclusion(
        conclusion_id: str, payload: ConclusionEdit, principal: Principal = Depends(writer)
    ) -> dict[str, Any]:
        """Автор подтверждает черновик или исправляет его поля; без этого проект из кейса не утвердить."""
        edits = payload.model_dump(include=payload.model_fields_set - {"expected_version"})
        return conclusion_step(
            conclusion_id,
            lambda body, version: lifecycle.confirm(
                body,
                version,
                fields=edits,
                actor=actor_of(principal),
                now=datetime.now(UTC),
                operations=operation_codes,
                journal_path=audit_log,
                expected_version=payload.expected_version,
            ),
        )

    @app.post("/v1/conclusions/{conclusion_id}/approve", dependencies=[Depends(strict_json_object)])
    def approve_conclusion(
        conclusion_id: str, payload: ConclusionAction, principal: Principal = Depends(writer)
    ) -> dict[str, Any]:
        """Второй юрист утверждает подтверждённый черновик: с этого момента строгий профиль его применяет."""
        return conclusion_step(
            conclusion_id,
            lambda body, version: lifecycle.approve(
                body,
                version,
                actor=actor_of(principal),
                reason=payload.reason,
                now=datetime.now(UTC),
                operations=operation_codes,
                journal_path=audit_log,
                expected_version=payload.expected_version,
            ),
        )

    @app.post("/v1/conclusions/{conclusion_id}/revoke", dependencies=[Depends(strict_json_object)])
    def revoke_conclusion(
        conclusion_id: str, payload: ConclusionAction, principal: Principal = Depends(writer)
    ) -> dict[str, Any]:
        """Юрист отзывает заключение с причиной: следующие проверки его не применяют."""
        return conclusion_step(
            conclusion_id,
            lambda body, version: lifecycle.revoke(
                body,
                version,
                actor=actor_of(principal),
                reason=payload.reason,
                now=datetime.now(UTC),
                journal_path=audit_log,
                expected_version=payload.expected_version,
            ),
        )

    @app.post("/v1/cases/{case_id}/conclusion-draft", dependencies=[Depends(strict_json_object)])
    def conclusion_from_case(
        case_id: str, payload: ConclusionFromCase, principal: Principal = Depends(writer)
    ) -> dict[str, Any]:
        """Проект заключения по кейсу, который ушёл на разбор из-за устава (R-CHR-002). Готовит юрист.

        С документами кейса и подключённой моделью банка проект заполняют факты устава, прошедшие шлюз цитаты;
        без модели проект пуст. Прежнее заключение по другой редакции устава названо в проекте: разбор начинается
        с него. Проект действует только после подтверждения автором и утверждения вторым юристом.
        """
        conclusion_journal()
        actor = actor_of(principal)
        if not actor.lawyer:
            raise HTTPException(403, "проект заключения по кейсу готовит юрист: reviewer со специализацией lawyer")
        report, _ = review_target(case_id, principal)
        charter = next((r.get("sha256") for r in report.get("inputs") or [] if r.get("kind") == "charter_text"), None)
        if not isinstance(charter, str):
            raise HTTPException(409, "в кейсе нет устава: заключение не к чему привязать")
        findings = report.get("findings") or []
        if not any(f.get("rule_id") == "R-CHR-002" and f.get("effect") == "needs_review" for f in findings):
            raise HTTPException(409, "кейс ушёл на разбор не из-за устава: R-CHR-002 его не останавливает")
        ogrn = str((report.get("organization") or {}).get("ogrn") or "")
        if not ogrn_ok(ogrn):
            raise HTTPException(409, "у организации кейса нет верного ОГРН юрлица: заключение не к чему привязать")
        room_for(ogrn)
        proposal = advisory_draft.Proposal("case", (), "документы кейса не переданы: проект пуст, юрист заполняет его")
        if payload.manifest is not None:
            try:
                case = load_case_inline(payload.manifest)
            except CaseError as exc:
                raise ApiProblem(422, "документы кейса не прошли проверку входных данных", f"CaseError: {exc}") from exc
            if case.charter is None or case.charter.sha256 != charter or case.organization.ogrn != ogrn:
                raise ApiProblem(
                    422, "документы не этого кейса: устав или ОГРН не совпадают с отчётом", "conclusion-draft: mismatch"
                )
            proposal = advisory_draft.propose(case)
        cid = lifecycle.new_id()
        try:
            body = lifecycle.draft_from_case(
                ogrn=ogrn,
                charter_sha256=charter,
                operation=str((report.get("operation") or {}).get("code") or ""),
                source_case=case_id,
                proposals=list(proposal.proposals),
                origin=proposal.origin,
                note=proposal.note,
                previous=conclusion_api.previous(registry_rows(ogrn), charter),
                actor=actor,
                conclusion_id=cid,
                now=datetime.now(UTC),
                journal_path=audit_log,
            )
        except lifecycle.ConclusionError as exc:
            raise conclusion_problem(exc) from exc
        except (ValueError, OSError, journal.JournalKeyMissing) as exc:
            raise ApiProblem(503, "журнал аудита недоступен, заключение не записано", f"journal: {exc}") from exc
        return {**keep_conclusion(cid, body, 0), "dropped": list(proposal.dropped)}

    @app.post("/v1/events", dependencies=[Depends(writer), Depends(strict_json_object)])
    def post_events(payload: dict[str, Any], window_hours: int = 72, notice_days: int = NOTICE_DAYS) -> dict[str, Any]:
        """Только оценка событий и сроков МЧД. Статусы удостоверений этот метод не меняет: их пишет CLI monitor.

        Сроки считаются на ``as_of`` из тела, а без него — на время сервера; момент возвращается в ответе.
        """
        if not isinstance(payload, dict):
            raise HTTPException(422, "тело должно быть объектом")
        if window_hours < 1 or window_hours > 24 * 30:
            raise HTTPException(422, "window_hours от 1 до 720")
        if notice_days < 0 or notice_days > 366:
            raise HTTPException(422, "notice_days от 0 до 366")
        try:
            warnings: list[str] = []
            rights, events = parse_events(payload, warnings)
            as_of = parse_as_of(payload, warnings) or datetime.now(UTC)
        except (KeyError, ValueError, TypeError) as exc:
            # Текст ошибки несёт значения клиента: в журнал сервера — только тип ошибки и путь поля.
            where = f"{getattr(exc, 'kind', type(exc).__name__)} at {getattr(exc, 'field', 'body')}"
            raise ApiProblem(422, "события не разобраны", where) from exc
        try:
            found = evaluate(rights, events, window_hours=window_hours, as_of=as_of, notice_days=notice_days)
        except ValueError as exc:
            raise ApiProblem(422, "пачка слишком велика: разбейте её по организациям", str(exc)) from exc
        moment = as_of.astimezone(MOSCOW).isoformat()
        return {"alerts": [a.as_dict() for a in found], "warnings": warnings, "as_of": moment}

    def admission_ready() -> admission.AdmissionConfig:
        """Допуск настроен и в защищённом режиме у него есть общее хранилище nonce и журнал; иначе 503."""
        if admit is None:
            raise HTTPException(503, "допуск не настроен: задайте MANDAT_ATTESTATION_KEYRING и MANDAT_STATUS_DIR")
        if protected() and (shared.name == "memory" or audit_log is None):
            raise HTTPException(503, "в защищённом режиме допуск требует общего хранилища и журнала аудита")
        return admit

    @app.post("/v1/operations/authorize", dependencies=[Depends(strict_json_object)])
    def authorize_operation(body: AdmissionBody, principal: Principal = Depends(admitter)) -> dict[str, Any]:
        """Допуск одной операции по удостоверению: подлинность, статус, вердикт confirmed и привязка к запросу.

        200 — решение: ``allowed`` и причины отказа. Отказ тоже 200: это ответ, а не ошибка запроса.
        ``already_admitted`` — этот же запрос уже допущен в ``admitted_at``, например ответ потерялся в сети;
        второго допуска нет, операцию по первому допуску ДБО проводит один раз.
        503 — допуск не настроен или в защищённом режиме нет общего хранилища nonce и журнала.
        """
        return admit_operation(body.credential, body.request, body.operation_risk, principal, via="api")

    def admit_operation(
        credential: dict[str, Any],
        fields: AdmissionOperation,
        operation_risk: str | None,
        principal: Principal,
        *,
        via: str,
    ) -> dict[str, Any]:
        """Решение о допуске и запись в журнал. Общая часть ``/v1/operations/authorize`` и AuthZEN."""
        config = admission_ready()
        if principal.audiences and fields.audience not in principal.audiences:
            # Учётная запись одного канала не расходует удостоверения другого; nonce не тронут.
            raise ApiProblem(
                403,
                "учётной записи не разрешён этот получатель удостоверения (audience)",
                f"admission: audience not allowed for {principal.name}",
            )
        if not known_risk(operation_risk):
            # Окно свежести задаёт подписанная операция; незнакомое слово не угадывается, nonce не тронут.
            raise ApiProblem(422, "уровень риска операции не из карты окон свежести", "operation_risk: unknown")
        try:
            operation = OperationRequest(
                operation=fields.operation,
                amount_rub=fields.amount_rub,
                representative_inn=fields.representative_inn,
                audience=fields.audience,
                nonce=fields.nonce,
                organization_inn=fields.organization_inn,
                operation_date=admission.parse_day(fields.operation_date),
            )
        except ValueError as exc:
            raise ApiProblem(422, "запрос операции не соответствует схеме", str(exc)) from exc
        moment = datetime.now(UTC)
        try:
            decision = admission.decide_detailed(
                admission.AdmissionRequest(credential, operation, operation_risk),
                config,
                shared,
                now=moment,
                protected=protected(),
            )
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно, допуск не выдан", f"store: {type(exc).__name__}") from exc
        problems = decision.problems
        allowed = not problems
        subject = credential.get("credentialSubject")
        report_sha = subject.get("reportSha256") if isinstance(subject, dict) else None
        if audit_log is not None:
            try:
                journal.append(
                    audit_log,
                    action="authorize_operation",
                    actor=principal.name,
                    role=principal.role,
                    case_id=str(report_sha)[:64] if isinstance(report_sha, str) else "unknown",
                    payload_hash=hashlib.sha256(
                        json.dumps(
                            {
                                "credential": credential,
                                "request": fields.model_dump(exclude={"representative_inn"}),
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                        ).encode("utf-8")
                    ).hexdigest(),
                    details={
                        "allowed": allowed,
                        "operation": fields.operation,
                        "audience": fields.audience,
                        "problems": len(problems),
                        "already_admitted": decision.already_admitted,
                        "via": via,
                    },
                )
            except (ValueError, OSError, journal.JournalKeyMissing) as exc:
                # Допуск без записи в журнал не выдаётся: в защищённом режиме решение должно быть доказуемо.
                # nonce, который занял этот допуск, освобождается: иначе повтор получил бы «уже допущен».
                cause = f"journal: {type(exc).__name__}"
                if allowed:
                    try:
                        shared.release(admission.nonce_key(fields.audience, fields.nonce))
                    except Exception:  # noqa: BLE001 — недоступное хранилище: повтор получит отказ
                        cause += "; nonce не освобождён"
                raise ApiProblem(503, "журнал аудита недоступен, допуск не выдан", cause) from exc
        return {
            "allowed": allowed,
            "problems": problems,
            "already_admitted": decision.already_admitted,
            "admitted_at": decision.admitted_at,
            "operation": fields.operation,
            "audience": fields.audience,
            "checked_at": moment.isoformat(timespec="seconds"),
        }

    # --- OpenID AuthZEN Authorization API 1.0: тот же допуск в стандартном конверте (api/authzen.py) ---------

    def authzen_admission(evaluation: authzen.EvaluationRequest, principal: Principal) -> dict[str, Any]:
        """Одна оценка AuthZEN: профиль «Мандата» → общий допуск → решение. Ошибка оценки — ``AuthzenError``."""
        request = authzen.admission_of(evaluation)
        try:
            fields = AdmissionOperation(
                organization_inn=request.organization_inn,
                operation=request.operation,
                operation_date=request.operation_date,
                amount_rub=request.amount_rub,
                representative_inn=request.representative_inn,
                audience=request.audience,
                nonce=request.nonce,
            )
        except ValueError as exc:
            raise authzen.AuthzenError(400, authzen.BAD_REQUEST, f"AdmissionOperation: {type(exc).__name__}") from exc
        try:
            result = admit_operation(request.credential, fields, request.operation_risk, principal, via="authzen")
        except ApiProblem as exc:
            status = 400 if exc.status == 422 else exc.status
            raise authzen.AuthzenError(status, exc.detail, exc.cause) from exc
        except HTTPException as exc:
            raise authzen.AuthzenError(exc.status_code, str(exc.detail), f"http {exc.status_code}") from exc
        return authzen.decision(result)

    @app.get(authzen.METADATA_PATH)
    def authzen_configuration(request: Request, principal: Principal = Depends(auth)) -> dict[str, Any]:
        """Метаданные PDP (AuthZEN 1.0, раздел 9). Как и весь API, только с токеном: открыты лишь /health и /readyz.

        Идентификатор PDP — ``MANDAT_AUTHZEN_PDP``; на стенде без него берётся адрес запроса, в pilot и prod — 503.
        """
        admission_ready()
        if pdp_identifier is None and protected():
            raise ApiProblem(503, "идентификатор PDP не настроен", f"authzen: {authzen.PDP_ENV} not set")
        return authzen.metadata(pdp_identifier or str(request.base_url).rstrip("/"))

    @app.post(authzen.EVALUATION_PATH, dependencies=[Depends(strict_json_authzen)])
    def access_evaluation(body: authzen.EvaluationRequest, principal: Principal = Depends(admitter)) -> dict[str, Any]:
        """Одна оценка (раздел 6). Отказ — 200 и ``decision: false``; ошибка запроса — 400 по разделу 10.1.2."""
        admission_ready()
        try:
            return authzen_admission(body, principal)
        except authzen.AuthzenError as exc:
            raise ApiProblem(exc.status, exc.message, f"authzen: {exc.cause}") from exc

    @app.post(authzen.EVALUATIONS_PATH, dependencies=[Depends(strict_json_authzen)])
    def access_evaluations(
        body: authzen.EvaluationsRequest, principal: Principal = Depends(admitter)
    ) -> dict[str, Any]:
        """Пакет оценок (раздел 7). Без ``evaluations`` — одна оценка с прежним ответом, как требует раздел 7.1."""
        admission_ready()
        try:
            evaluations, semantic = authzen.expand(body)
        except authzen.AuthzenError as exc:
            raise ApiProblem(exc.status, exc.message, f"authzen: {exc.cause}") from exc
        if not body.evaluations:
            try:
                return authzen_admission(evaluations[0], principal)
            except authzen.AuthzenError as exc:
                raise ApiProblem(exc.status, exc.message, f"authzen: {exc.cause}") from exc
        results = authzen.run_batch(evaluations, semantic, lambda one: authzen_admission(one, principal))
        return {"evaluations": results}

    if with_console and sessions is not None:
        from mandat.console.issuer import DemoIssuer
        from mandat.console.routes import mount

        mount(app, sessions=sessions, store=shared, issuer=issuer or DemoIssuer(), commit=commit)
    return app
