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
Отчёты держатся в памяти процесса; хранилище кейсов — задача пилота (Postgres в контуре банка).
"""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mandat import ENGINE, __version__
from mandat.api.principals import Directory, Principal, load_users
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.report.html import render
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case

MAX_BODY = 8 * 1024 * 1024
# Отчёты живут в памяти процесса. Без потолка поток POST /v1/cases съедает память (OWASP API4:2023).
MAX_REPORTS = 1000
MAX_CLIENTS = 10_000


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def create_app(
    token: str | None = None,
    *,
    users: str | None = None,
    docs: bool | None = None,
    rate_per_min: int | None = None,
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
    reports: dict[str, dict[str, Any]] = {}
    owners: dict[str, str] = {}
    hits: dict[str, deque[float]] = defaultdict(deque)

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        if limit > 0 and request.url.path != "/health":
            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            if len(hits) > MAX_CLIENTS:
                # Поток запросов с разных адресов не должен раздувать таблицу: старые окна выбрасываем.
                for stale in [k for k, w in hits.items() if not w or now - w[-1] > 60]:
                    del hits[stale]
            window = hits[client]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= limit:
                return JSONResponse(
                    {"detail": "слишком много запросов"}, status_code=429, headers={"Retry-After": "60"}
                )
            window.append(now)
        return await call_next(request)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):  # type: ignore[no-untyped-def]
        length = request.headers.get("content-length")
        if length:
            try:
                declared = int(length)
            except ValueError:
                return JSONResponse({"detail": "некорректный Content-Length"}, status_code=400)
            if declared > MAX_BODY:
                return JSONResponse({"detail": "запрос больше допустимого"}, status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"detail": "запрос больше допустимого"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

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
        if case_id not in reports or not (principal.sees_all or owners.get(case_id) == principal.name):
            raise HTTPException(404, "кейс не найден")
        return reports[case_id]

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine": ENGINE,
            "auth_configured": directory.configured,
            "mode": "users" if directory.multi_user else "single-token",
        }

    @app.post("/v1/cases")
    def create_case(
        manifest: dict[str, Any],
        profile: str = "demo",
        replace: bool = False,
        principal: Principal = Depends(writer),
    ) -> dict[str, Any]:
        if profile not in {"demo", "strict"}:
            raise HTTPException(422, "profile: demo или strict")
        client_ref = manifest.get("case_id")
        if directory.multi_user:
            manifest = {**manifest, "case_id": f"case-{secrets.token_hex(8)}"}
        try:
            case = load_case_inline(manifest)
        except CaseError as exc:
            raise HTTPException(422, str(exc)) from exc
        report = verify_case(case, profile=profile).as_dict()
        case_id = report["case_id"]
        if case_id in reports and not replace:
            raise HTTPException(409, "кейс с таким case_id уже есть: передайте replace=true, чтобы перезаписать")
        if case_id in reports and not (principal.sees_all or owners.get(case_id) == principal.name):
            raise HTTPException(409, "кейс с таким case_id принадлежит другому пользователю")
        if directory.multi_user and client_ref is not None:
            report = {**report, "client_case_id": str(client_ref)}
        reports.pop(case_id, None)
        reports[case_id] = report
        owners[case_id] = principal.name
        while len(reports) > MAX_REPORTS:
            oldest = next(iter(reports))
            reports.pop(oldest)
            owners.pop(oldest, None)
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
            rights, events = parse_events(payload)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, f"события не разобраны: {exc}") from exc
        return {"alerts": [a.as_dict() for a in evaluate(rights, events, window_hours=window_hours)]}

    return app
