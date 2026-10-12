"""HTTP API «Мандата».

Доступ по токену ``MANDAT_API_TOKEN`` (заголовок ``Authorization: Bearer …``).
Без токена API отвечает только на /health: закрыто по умолчанию.
Отчёты держатся в памяти процесса; хранилище кейсов — задача пилота (Postgres в контуре банка).
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mandat import ENGINE, __version__
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.report.html import render
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case

MAX_BODY = 8 * 1024 * 1024


def create_app(token: str | None = None) -> FastAPI:
    expected = token if token is not None else os.environ.get("MANDAT_API_TOKEN", "")
    app = FastAPI(title="Мандат API", version=__version__, docs_url="/docs", redoc_url=None)
    reports: dict[str, dict[str, Any]] = {}

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

    def auth(authorization: str = Header(default="")) -> None:
        if not expected:
            raise HTTPException(503, "API закрыт: задайте MANDAT_API_TOKEN")
        scheme, _, value = authorization.partition(" ")
        presented = value.strip().encode("utf-8")
        required = expected.encode("utf-8")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented, required):
            raise HTTPException(401, "нужен токен доступа")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "engine": ENGINE, "auth_configured": bool(expected)}

    @app.post("/v1/cases", dependencies=[Depends(auth)])
    def create_case(manifest: dict[str, Any], profile: str = "demo") -> dict[str, Any]:
        if profile not in {"demo", "strict"}:
            raise HTTPException(422, "profile: demo или strict")
        try:
            case = load_case_inline(manifest)
        except CaseError as exc:
            raise HTTPException(422, str(exc)) from exc
        report = verify_case(case, profile=profile).as_dict()
        reports[report["case_id"]] = report
        return report

    @app.get("/v1/cases/{case_id}", dependencies=[Depends(auth)])
    def get_case(case_id: str) -> dict[str, Any]:
        if case_id not in reports:
            raise HTTPException(404, "кейс не найден")
        return reports[case_id]

    @app.get("/v1/cases/{case_id}/report.html", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def get_case_html(case_id: str) -> HTMLResponse:
        if case_id not in reports:
            raise HTTPException(404, "кейс не найден")
        return HTMLResponse(
            render(reports[case_id]),
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    @app.post("/v1/events", dependencies=[Depends(auth)])
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
