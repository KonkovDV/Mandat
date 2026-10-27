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
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mandat import ENGINE, __version__
from mandat.api import review as review_api
from mandat.api.principals import Directory, Principal, load_users
from mandat.api.storage import Idem, Pending, ReviewVersionConflict, Store, open_store
from mandat.audit import journal
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.report.html import render
from mandat.review.state_machine import (
    JOURNAL_ACTION,
    ReviewConflict,
    ReviewError,
    ReviewForbidden,
    ReviewInvalid,
    ReviewState,
)
from mandat.services.env_policy import PROFILES, ProfileNotAllowed, protected, resolve_profile
from mandat.services.monitoring import evaluate, parse_events
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


# R2-API-3 (OWASP API8:2023, ASVS 5.0 V16): клиент получает общий текст и request_id, подробности — только
# в журнале сервера, и без ПДн: значения полей из ошибок схемы не пишутся, длинные числа (ИНН, ОГРН,
# СНИЛС, паспорт) и адреса почты маскируются.
log = logging.getLogger("mandat.api")
REQUEST_ID_HEADER = b"x-request-id"
_DIGITS = re.compile(r"\d{5,}")
_EMAIL = re.compile(r"[^\s@'\"]+@[^\s@'\"]+")


def scrub(text: str, limit: int = 300) -> str:
    """Текст для журнала сервера: без длинных чисел и адресов почты, не длиннее ``limit``."""
    return _EMAIL.sub("<email>", _DIGITS.sub("<num>", text))[:limit]


def request_id(scope: MutableMapping[str, Any]) -> str:
    state = scope.setdefault("state", {})
    if "request_id" not in state:
        state["request_id"] = secrets.token_hex(8)
    return str(state["request_id"])


def problem(request: Request, status: int, detail: str, cause: str) -> JSONResponse:
    rid = request_id(request.scope)
    log.warning("request_id=%s status=%s path=%s cause=%s", rid, status, request.url.path, scrub(cause))
    headers = {k.decode(): v.decode() for k, v in [*SECURITY_HEADERS, DEFAULT_CSP]}
    headers[REQUEST_ID_HEADER.decode()] = rid
    return JSONResponse({"detail": detail, "request_id": rid}, status_code=status, headers=headers)


class ReviewAction(BaseModel):
    """Действие разбора. ``expected_version`` — поле ``version`` из GET /v1/cases/{id}/review."""

    expected_version: int = Field(ge=0)
    reason: str = Field(default="", max_length=4000)
    # Только для действия require: специализация, без которой кейс не решить (M-09).
    specialization: str | None = Field(default=None, max_length=64)


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


class Guard:
    """Чистое ASGI-звено перед приложением: лимит частоты, токен, размер тела — именно в этом порядке.

    Токен проверяется по заголовкам до чтения тела: без токена сервер не читает ни байта
    запроса. Тело читается потоком со счётчиком и отвергается с 413, как только превысит
    ``MAX_BODY``, даже без ``Content-Length`` (chunked). ``BaseHTTPMiddleware`` с
    ``await request.body()`` здесь нельзя: он буферизует всё тело до проверок.
    """

    def __init__(self, app: Any, *, directory: Directory, limit: int, store: Store) -> None:
        self.app, self.directory, self.limit, self.store = app, directory, limit, store

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        rid = request_id(scope).encode("ascii")

        async def secured(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = [(k, v) for k, v in message.get("headers", []) if k.lower() != REQUEST_ID_HEADER]
                extra = [] if any(k.lower() == b"content-security-policy" for k, _ in existing) else [DEFAULT_CSP]
                message = {**message, "headers": [*existing, *SECURITY_HEADERS, *extra, (REQUEST_ID_HEADER, rid)]}
            await send(message)

        async def reply(status: int, detail: str, extra: dict[str, str] | None = None) -> None:
            # Отказы (401, 413, 429) идут с теми же заголовками безопасности, что и обычные ответы.
            await JSONResponse({"detail": detail}, status_code=status, headers=extra)(scope, receive, secured)

        if path not in OPEN_PATHS:
            if self.limit > 0:
                client = (scope.get("client") or ("unknown",))[0]
                if not self.store.allow(str(client), self.limit):
                    await reply(429, "слишком много запросов", {"Retry-After": "60"})
                    return
            if not self.directory.configured:
                await reply(503, "API закрыт: задайте MANDAT_API_TOKEN или MANDAT_API_USERS")
                return
            scheme, _, value = headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not value.strip() or self.directory.find(value.strip()) is None:
                await reply(401, "нужен токен доступа")
                return
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit():
                await reply(400, "некорректный Content-Length")
                return
            if int(length) > MAX_BODY:
                await reply(413, "запрос больше допустимого")
                return
        messages: list[dict[str, Any]] = []
        total = 0
        if scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    messages.append(message)
                    break
                total += len(message.get("body", b""))
                if total > MAX_BODY:
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


# Отчёты живут в памяти процесса. Без потолка поток POST /v1/cases съедает память (OWASP API4:2023).
MAX_REPORTS = 1000


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# Сколько держится заявка на создание кейса: дольше, чем занимает проверка и запись в журнал.
CREATE_CLAIM_SECONDS = 30.0
# Idempotency-Key: безопасный набор символов, чтобы ключ не попал в журнал/логи как произвольный текст.
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._~-]{1,128}")


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
) -> FastAPI:
    expected = token if token is not None else os.environ.get("MANDAT_API_TOKEN", "")
    raw_users = (users if users is not None else os.environ.get("MANDAT_API_USERS", "")).strip()
    users_file = Path(raw_users) if raw_users and not raw_users.startswith("[") else None
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
    shared = store if store is not None else open_store()
    env_journal = os.environ.get("MANDAT_API_JOURNAL", "").strip()
    audit_log = journal_path if journal_path is not None else (Path(env_journal) if env_journal else None)

    app.add_middleware(Guard, directory=directory, limit=limit, store=shared)

    @app.exception_handler(RequestValidationError)
    async def schema_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Только место и тип ошибки: поле «input» может содержать ПДн.
        where = "; ".join(f"{'.'.join(map(str, e.get('loc', ())))}:{e.get('type', '')}" for e in exc.errors())
        return problem(request, 422, "запрос не соответствует схеме", where)

    @app.exception_handler(ApiProblem)
    async def api_problem(request: Request, exc: ApiProblem) -> JSONResponse:
        return problem(request, exc.status, exc.detail, exc.cause)

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
            raise HTTPException(401, "нужен токен доступа")
        return principal

    def writer(principal: Principal = Depends(auth)) -> Principal:
        if not principal.can_write:
            raise HTTPException(403, f"роль {principal.role} только читает")
        return principal

    def visible(case_id: str, principal: Principal) -> dict[str, Any]:
        # Чужой кейс неотличим от несуществующего: 404, а не 403.
        found = shared.get(case_id)
        if found is None or not (principal.sees_all or found[1] == principal.name):
            raise HTTPException(404, "кейс не найден")
        return found[0]

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine": ENGINE,
            "auth_configured": directory.configured,
            "mode": "users" if directory.multi_user else "single-token",
            "store": shared.name,
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
        """Свести ожидающую запись с журналом после сбоя (M-10).

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
                return "committed" if shared.commit(pend.case_id, pend.attempt, MAX_REPORTS) else "superseded"
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
                found = shared.get(done[0])
                if found is None:
                    raise HTTPException(409, "результат запроса с этим Idempotency-Key больше не хранится")
                return found[0]
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
        # В pilot и prod снимки источников сверяются с часами сервера: снимок «из будущего» не доказательство.
        if protected():
            report = verify_case(case, profile=profile, now=datetime.now(UTC)).as_dict()
        else:
            report = verify_case(case, profile=profile).as_dict()
        case_id = report["case_id"]
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
        # Порядок (M-10): stage в хранилище → запись журнала → commit. Отчёт виден (GET) и 200 уходит только после
        # commit. Сбой между шагами оставляет «ожидающую» запись, которую сверка (reconcile) подтверждает, если
        # запись журнала этой попытки есть, и отбрасывает, если её нет. Это не распределённая транзакция и не
        # exactly-once: журнал и хранилище остаются двумя системами, а повтор без Idempotency-Key после
        # сбоя может получить 409 вместо повторного ответа.
        attempt = secrets.token_hex(16)
        try:
            shared.stage(case_id, report, principal.name, attempt, idem)
        except Exception as exc:
            raise ApiProblem(503, "хранилище недоступно, кейс не сохранён", f"store: {type(exc).__name__}") from exc
        if audit_log is not None:
            try:
                journal.append(
                    audit_log,
                    action="verify",
                    actor=principal.name,
                    role=principal.role,
                    case_id=case_id,
                    payload_hash=report["reproducibility_hash"],
                    details={
                        "verdict": report["verdict"],
                        "profile": report["profile"],
                        "via": "api",
                        "attempt": attempt,
                    },
                )
            except (ValueError, OSError, journal.JournalKeyMissing) as exc:
                with contextlib.suppress(Exception):
                    shared.discard(case_id, attempt)
                cause = f"journal: {type(exc).__name__}: {exc}"
                raise ApiProblem(503, "журнал аудита недоступен, кейс не сохранён", cause) from exc
        try:
            committed = shared.commit(case_id, attempt, MAX_REPORTS)
        except Exception as exc:
            # Запись в журнале уже есть, отчёт ждёт подтверждения: клиенту не 200, сверка завершит позже.
            raise ApiProblem(
                503, "хранилище недоступно: кейс записан в журнал, но не подтверждён", f"store: {type(exc).__name__}"
            ) from exc
        if not committed:
            raise HTTPException(409, "запись вытеснена другой попыткой: повторите запрос")
        # Кейс без автоматического решения сразу встаёт в очередь разбора. При replace=true разбор начинается
        # заново по новому отчёту (M-09): прежний разбор уходит в «superseded» и в журнал (review_reset).
        # Сбой записи не отменяет кейс: разбор создастся при первом действии, но до того кейса нет в очереди,
        # поэтому сбой пишется в журнал сервера.
        try:
            seed_review(case_id, report, principal)
        except Exception as exc:
            log.warning("review_seed_failed case_id=%s error=%s", case_id, type(exc).__name__)
        return report

    def seed_review(case_id: str, report: dict[str, Any], principal: Principal) -> None:
        fresh = review_api.initial(case_id, report, principal.name)
        stored = shared.review_get(case_id)
        if stored is None:
            with contextlib.suppress(ReviewVersionConflict):
                shared.review_put(case_id, fresh, 0)
            return
        previous, version = stored
        if previous.get("report_hash") == fresh["report_hash"]:
            return  # тот же отчёт: разбор продолжается
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

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str, principal: Principal = Depends(auth)) -> dict[str, Any]:
        return visible(case_id, principal)

    @app.get("/v1/cases/{case_id}/report.html", response_class=HTMLResponse)
    def get_case_html(case_id: str, principal: Principal = Depends(auth)) -> HTMLResponse:
        return HTMLResponse(
            render(visible(case_id, principal)),
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
            },
        )

    def review_target(case_id: str, principal: Principal) -> tuple[dict[str, Any], str]:
        # reviewer разбирает любые кейсы, остальные видят свои; auditor и admin — все (только чтение разбора).
        found = shared.get(case_id)
        if found is None or not (principal.sees_all or principal.role == "reviewer" or found[1] == principal.name):
            raise HTTPException(404, "кейс не найден")
        return found

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
            if shared.get(case_id) is None:  # отчёт вытеснен из хранилища
                continue
            item = review_api.view(case_id, body, version)
            item.pop("history")
            item.pop("superseded")
            items.append(item)
        # Просроченные выше, внутри — по поднятому приоритету, затем по сроку.
        items.sort(
            key=lambda i: (not i["overdue"], i["effective_priority"] or "P9", i["due_at"] or "9999", i["case_id"])
        )
        return {"items": items, "states": sorted(open_states), "auto_decided": ReviewState.AUTO_DECIDED.value}

    @app.post("/v1/events", dependencies=[Depends(writer), Depends(strict_json_object)])
    def post_events(payload: dict[str, Any], window_hours: int = 72) -> dict[str, Any]:
        """Только оценка событий. Статусы удостоверений этот метод не меняет: их пишет CLI monitor."""
        if not isinstance(payload, dict):
            raise HTTPException(422, "тело должно быть объектом")
        if window_hours < 1 or window_hours > 24 * 30:
            raise HTTPException(422, "window_hours от 1 до 720")
        try:
            warnings: list[str] = []
            rights, events = parse_events(payload, warnings)
        except (KeyError, ValueError, TypeError) as exc:
            raise ApiProblem(422, "события не разобраны", f"{type(exc).__name__}: {exc}") from exc
        alerts = [a.as_dict() for a in evaluate(rights, events, window_hours=window_hours)]
        return {"alerts": alerts, "warnings": warnings}

    return app
