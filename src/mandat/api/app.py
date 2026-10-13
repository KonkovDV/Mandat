"""HTTP API «Мандата».

Доступ по токену (заголовок ``Authorization: Bearer …``). Два режима:

* стенд: один ``MANDAT_API_TOKEN``, его владелец — администратор, ``case_id`` задаёт клиент
  (при заданном ``MANDAT_API_USERS`` этот токен не действует);
* пользователи: ``MANDAT_API_USERS`` (см. ``api/principals.py``), у каждого роль, ``case_id`` выдаёт сервер,
  а кейс видит только тот, кто его создал, плюс аудитор и администратор.

Без токенов API отвечает только на /health: закрыто по умолчанию.
Swagger на /docs включается флагом ``MANDAT_API_DOCS=1``. Частота запросов ограничена
``MANDAT_API_RATE_PER_MIN`` (по умолчанию 120 в минуту на адрес клиента, 0 выключает). Адрес берётся
из соединения, ``X-Forwarded-For`` не читается: за прокси лимит надо ставить на самом прокси.
Окно лимита и отчёты лежат в хранилище ``MANDAT_API_STORE`` (``api/storage.py``): по умолчанию в памяти
процесса, для нескольких воркеров — SQLite или Redis.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from mandat import ENGINE, __version__
from mandat.api.principals import Directory, Principal, load_users
from mandat.api.storage import Store, open_store
from mandat.audit import journal
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.report.html import render
from mandat.services.env_policy import PROFILES, ProfileNotAllowed, protected, resolve_profile
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case

MAX_BODY = 8 * 1024 * 1024
OPEN_PATHS = {"/health"}
# Глубина вложенности JSON. Глубокий массив в 2 КБ вызывал RecursionError при разборе ошибки валидации (500).
MAX_JSON_DEPTH = 64
SECURITY_HEADERS = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]


def json_depth_exceeds(body: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Глубина скобок ``[`` и ``{`` вне строк превышает ``limit``. Линейный проход без разбора JSON."""
    depth = 0
    in_string = escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # обратная косая черта
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > limit:
                return True
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return False


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

        async def reply(status: int, detail: str, extra: dict[str, str] | None = None) -> None:
            await JSONResponse({"detail": detail}, status_code=status, headers=extra)(scope, receive, send)

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

        async def secured(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *SECURITY_HEADERS]}
            await send(message)

        await self.app(scope, replay, secured)


# Отчёты живут в памяти процесса. Без потолка поток POST /v1/cases съедает память (OWASP API4:2023).
MAX_REPORTS = 1000


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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
    directory = Directory(load_users(users), expected)
    show_docs = docs if docs is not None else _env_flag("MANDAT_API_DOCS")
    limit = rate_per_min if rate_per_min is not None else int(os.environ.get("MANDAT_API_RATE_PER_MIN", "120"))
    app = FastAPI(
        title="Мандат API",
        version=__version__,
        docs_url="/docs" if show_docs else None,
        openapi_url="/openapi.json" if show_docs else None,
        redoc_url=None,
    )
    shared = store if store is not None else open_store()
    env_journal = os.environ.get("MANDAT_API_JOURNAL", "").strip()
    audit_log = journal_path if journal_path is not None else (Path(env_journal) if env_journal else None)

    app.add_middleware(Guard, directory=directory, limit=limit, store=shared)

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

    @app.post("/v1/cases")
    def create_case(
        manifest: dict[str, Any],
        profile: str | None = None,
        replace: bool = False,
        principal: Principal = Depends(writer),
    ) -> dict[str, Any]:
        if profile is not None and profile not in PROFILES:
            raise HTTPException(422, "profile: demo или strict")
        try:
            profile = resolve_profile(profile)
        except ProfileNotAllowed as exc:
            raise HTTPException(403, str(exc)) from exc
        client_ref = manifest.get("case_id")
        if directory.multi_user:
            manifest = {**manifest, "case_id": f"case-{secrets.token_hex(8)}"}
        try:
            case = load_case_inline(manifest)
        except CaseError as exc:
            raise HTTPException(422, str(exc)) from exc
        report = verify_case(case, profile=profile).as_dict()
        case_id = report["case_id"]
        existing = shared.get(case_id)
        if existing is not None and not replace:
            raise HTTPException(409, "кейс с таким case_id уже есть: передайте replace=true, чтобы перезаписать")
        if existing is not None and not (principal.sees_all or existing[1] == principal.name):
            raise HTTPException(409, "кейс с таким case_id принадлежит другому пользователю")
        if directory.multi_user and client_ref is not None:
            report = {**report, "client_case_id": str(client_ref)}
        if audit_log is None and protected():
            raise HTTPException(503, "журнал аудита не настроен: задайте MANDAT_API_JOURNAL")
        if audit_log is not None:
            # Сначала журнал, потом хранилище: отчёт без записи в журнале не появляется. Если хранилище
            # упадёт после журнала, в журнале останется запись о проверке без сохранённого отчёта — это видно.
            try:
                journal.append(
                    audit_log,
                    action="verify",
                    actor=principal.name,
                    role=principal.role,
                    case_id=case_id,
                    payload_hash=report["reproducibility_hash"],
                    details={"verdict": report["verdict"], "profile": report["profile"], "via": "api"},
                )
            except (ValueError, OSError, journal.JournalKeyMissing) as exc:
                raise HTTPException(503, f"журнал аудита недоступен, кейс не сохранён: {exc}") from exc
        shared.put(case_id, report, principal.name, MAX_REPORTS)
        return report

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str, principal: Principal = Depends(auth)) -> dict[str, Any]:
        return visible(case_id, principal)

    @app.get("/v1/cases/{case_id}/report.html", response_class=HTMLResponse)
    def get_case_html(case_id: str, principal: Principal = Depends(auth)) -> HTMLResponse:
        return HTMLResponse(
            render(visible(case_id, principal)),
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    @app.post("/v1/events", dependencies=[Depends(writer)])
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
            raise HTTPException(422, f"события не разобраны: {exc}") from exc
        alerts = [a.as_dict() for a in evaluate(rights, events, window_hours=window_hours)]
        return {"alerts": alerts, "warnings": warnings}

    return app
