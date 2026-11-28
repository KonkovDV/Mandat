"""Маршруты консоли стенда и правила звена ``Guard`` для них.

Страница и её файлы отдаются по белому списку, ответы API консоли — JSON. Браузер держит только cookie сессии;
каждое действие консоль выполняет запросом в HTTP API от демо-учётной записи этой сессии (``console.inproc``),
так что роли, владение кейсом, журнал аудита и строгий JSON работают как для любого клиента API.

Защита как у стенда «Свода»: заголовки безопасности и CSP без встроенных скриптов и стилей, ``noindex``, HEAD только
на безопасных адресах, POST только с ``application/json`` и без чужого ``Origin``, потолок тела запроса, отдельные
лимиты на тяжёлые действия (прогон корпуса, справка модели) и один прогон за раз.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from mandat.api.storage import Store
from mandat.audit.attestation import NONCE_MIN_CHARS
from mandat.console import analytics, secure_cookie
from mandat.console.advisor import (
    ADVICE_LABEL,
    cached_advice,
    deterministic,
    gate_proof,
    model_advice,
    model_client,
    usage_view,
)
from mandat.console.demo import DBO_CASE, UploadError, catalog, demo_manifest, operations, upload_manifest
from mandat.console.inproc import AsgiApp, Reply, call
from mandat.console.issuer import DemoIssuer, IssuerExhausted, bank_today, rebased_today
from mandat.console.sessions import AUDIENCE, LIFETIME_SECONDS, DboState, Session, Sessions
from mandat.domain.verdict import CHECK_LABEL_RU, VERDICT_LABEL_RU, Verdict
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case
from mandat.strictjson import strict_loads

PREFIX = "/console"
COOKIE = "mandat_console"
# Файлы страницы: имя → тип. Другого консоль не отдаёт, путь из запроса в файловую систему не попадает.
STATIC: dict[str, str] = {
    "console.css": "text/css; charset=utf-8",
    "console.js": "text/javascript; charset=utf-8",
    "theme.js": "text/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
    "GolosText-Regular.woff2": "font/woff2",
    "GolosText-SemiBold.woff2": "font/woff2",
    "GolosText-Bold.woff2": "font/woff2",
    "Manrope-cyrillic.woff2": "font/woff2",
    "Manrope-latin.woff2": "font/woff2",
    "CormorantGaramond-SemiBoldItalic-cyrillic.woff2": "font/woff2",
    **{f"anna-{name}-480.webp": "image/webp" for name in ("hello", "explain", "lawyer", "phone", "timer")},
    **{f"anna-{name}-160.webp": "image/webp" for name in ("ok", "stop", "think", "docs")},
    "anna-full-chart-640.webp": "image/webp",
}
PAGE_PATHS = {"/", "/robots.txt", f"{PREFIX}/"}
# HEAD — только там, где GET ничего не меняет и не пишет в журнал: страница, её файлы и список демо-кейсов.
HEAD_OK = PAGE_PATHS | {f"{PREFIX}/static/{name}" for name in STATIC} | {f"{PREFIX}/api/cases"}
GET_API = (
    f"{PREFIX}/api/cases",
    f"{PREFIX}/api/review",
    f"{PREFIX}/api/report/",
    f"{PREFIX}/api/stats",
    f"{PREFIX}/api/analytics",
)
MAX_BODY = 512 * 1024
PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
CARD_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
# Заголовки всех ответов консоли сверх общих заголовков API.
EXTRA_HEADERS = [
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"x-robots-tag", b"noindex, nofollow"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
# Запросов в минуту с одного адреса: новые сессии, прогон корпуса, справка модели.
SESSION_PER_MIN = 20
EVAL_PER_MIN = 4
MODEL_PER_MIN = 20
EVAL_MAX_CASES = 50
EVAL_CACHE_SECONDS = 600.0
# Сколько ждать справку модели: дольше — пояснение из фактов отчёта, а не ожидание.
MODEL_DEADLINE_SECONDS = 8.0
# Удостоверений на одну сессию: каждое — запись в реестре статусов стенда.
DBO_PER_SESSION = 20
REVIEW_ACTIONS = {"open": "reviewer", "request_docs": "reviewer", "accept": "reviewer", "reject": "reviewer"}
REVIEW_ACTIONS |= {"close": "reviewer", "appeal": "operator"}
# Реестр заключений: черновик пишет оператор сессии, утверждает и отзывает юрист сессии (два разных лица).
REGISTRY_CASE = "01_ok"
REGISTRY_ACTIONS = {"approve": "reviewer", "revoke": "reviewer"}
REGISTRY_DRAFTS_PER_SESSION = 10
REGISTRY_READING: dict[str, dict[str, Any]] = {
    "no_restriction": {"outcome": "no_restriction", "restrictions": [], "max_amount_rub": 1_000_000},
    "threshold": {
        "outcome": "restriction",
        "restrictions": [{"kind": "approval_threshold", "value_rub": 300_000, "inclusive": False}],
    },
}
DBO_SCENARIOS = ("pay", "replay", "over", "takeover")
EFFECT_ORDER = {"refutes": 0, "needs_review": 1, "not_verified": 2, "supports": 3, "info": 4}
SOURCE_LABEL = {
    "emchd_xml": "МЧД",
    "egrul_json": "ЕГРЮЛ",
    "charter_text": "Устав",
    "paper_poa_text": "Доверенность (текст)",
}


class ConsoleGate:
    """Правила звена ``Guard`` для путей консоли: вместо токена — эти проверки и cookie сессии в маршрутах."""

    max_body = MAX_BODY
    extra_headers = EXTRA_HEADERS

    @staticmethod
    def owns(path: str) -> bool:
        return path in PAGE_PATHS or path == PREFIX or path.startswith(f"{PREFIX}/")

    @staticmethod
    def allow_header(path: str) -> str:
        if path in HEAD_OK:
            return "GET, HEAD"
        return "GET" if path.startswith(GET_API) else "POST"

    def screen(self, method: str, path: str, headers: dict[str, str]) -> tuple[int, str, dict[str, str]] | None:
        """Отказ до маршрута: (код, текст, заголовки) или ``None``, если запрос можно пропустить дальше."""
        if method == "HEAD" and path not in HEAD_OK:
            return 405, "HEAD только для страницы и её файлов", {"Allow": self.allow_header(path)}
        if method not in {"GET", "HEAD", "POST"}:
            return 405, "метод не поддерживается", {"Allow": self.allow_header(path)}
        if method == "POST":
            kind = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if kind != "application/json":
                return 415, "нужен Content-Type: application/json", {}
            origin = headers.get("origin", "").strip()
            if origin and urlsplit(origin).netloc.lower() != headers.get("host", "").strip().lower():
                return 403, "запрос с чужой страницы", {}
        return None


class ConsoleProblem(Exception):
    def __init__(self, status: int, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.headers = status, detail, headers or {}


@dataclass
class EvalState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cache: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)


def _static_bytes(name: str) -> bytes:
    return resources.files("mandat.console").joinpath("static", name).read_bytes()


def _version() -> str:
    """Метка сборки файлов страницы: после выкладки браузер берёт новые CSS и JS, а не закэшированные."""
    digest = hashlib.sha256()
    for name in ("console.css", "console.js", "theme.js"):
        digest.update(_static_bytes(name))
    return digest.hexdigest()[:12]


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def evidence(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Карточка доказательств: находка, норма и откуда факт — поле МЧД, запись ЕГРЮЛ, фраза устава, реестр."""
    kinds = {row.get("doc_id"): row.get("kind") for row in report.get("inputs") or []}
    rows = []
    for finding in report.get("findings") or []:
        sources = []
        for ref in finding.get("refs") or []:
            doc = str(ref.get("doc_id") or "")
            label = SOURCE_LABEL.get(str(kinds.get(doc))) or (
                "Реестр МЧД"
                if doc.startswith("registry")
                else "Заключение юриста"
                if doc.startswith("conclusion:")
                else "Сведения"
            )
            sources.append({"source": label, "locator": ref.get("locator"), "quote": ref.get("quote")})
        rows.append(
            {
                "rule_id": finding.get("rule_id"),
                "check": CHECK_LABEL_RU.get(str(finding.get("check")), finding.get("check")),
                "effect": finding.get("effect"),
                "origin": finding.get("origin"),
                "message": finding.get("message"),
                "norm_ref": finding.get("norm_ref"),
                "next_step": finding.get("next_step"),
                "unit": finding.get("unit"),
                "sources": sources,
            }
        )
    rows.sort(key=lambda r: EFFECT_ORDER.get(str(r["effect"]), 9))
    return rows


def summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": report.get("case_id"),
        "verdict": report.get("verdict"),
        "verdict_label": report.get("verdict_label"),
        "objection": report.get("objection"),
        "organization": report.get("organization"),
        "operation": report.get("operation"),
        "checks": report.get("checks"),
        "routing": report.get("routing"),
        "next_steps": report.get("next_steps"),
        "warnings": report.get("warnings"),
        "stand": report.get("stand"),
        "chain": (report.get("chain") or {}).get("links"),
        "evidence": evidence(report),
        "report_url": f"{PREFIX}/api/report/{report.get('case_id')}",
    }


def _credential_view(credential: dict[str, Any]) -> dict[str, Any]:
    subject = dict(credential.get("credentialSubject") or {})
    if "representativeToken" in subject:
        subject["representativeToken"] = str(subject["representativeToken"])[:12] + "…"
    proof = credential.get("proof") or {}
    return {
        "type": credential.get("type"),
        "issuer": credential.get("issuer"),
        "validFrom": credential.get("validFrom"),
        "validUntil": credential.get("validUntil"),
        "subject": subject,
        "proof": {"type": proof.get("type"), "cryptosuite": proof.get("cryptosuite")},
        "status": [s.get("statusPurpose") for s in credential.get("credentialStatus") or []],
    }


def mount(app: FastAPI, *, sessions: Sessions, store: Store, issuer: DemoIssuer, commit: str | None = None) -> None:
    """Маршруты консоли на приложении API. Вызывается из ``create_app`` только в режиме стенда."""
    version = _version()
    index = _static_bytes("index.html").decode("utf-8").replace("{{version}}", version).encode("utf-8")
    evals = EvalState()
    # Для эксплуатации и тестов: сессии и демо-издатель этого приложения.
    app.state.console_sessions, app.state.console_issuer, app.state.console_evals = sessions, issuer, evals

    @app.exception_handler(ConsoleProblem)
    async def console_problem(request: Request, exc: ConsoleProblem) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status, headers=exc.headers)

    def limited(request: Request, bucket: str, per_min: int) -> None:
        if not store.allow(f"console:{bucket}:{_client(request)}", per_min):
            raise ConsoleProblem(429, "слишком часто: повторите через минуту", {"Retry-After": "60"})

    def session_of(request: Request) -> Session:
        found = sessions.get(request.cookies.get(COOKIE))
        if found is None:
            raise ConsoleProblem(401, "сессия консоли закончилась: обновите страницу")
        return found

    async def api(
        session: Session, role: str, method: str, path: str, body: Any = None, query: dict[str, str] | None = None
    ) -> Reply:
        target: AsgiApp = app
        tag = f"console-{session.tag}"
        return await call(target, method, path, token=session.token(role), client=tag, body=body, query=query)

    async def json_body(request: Request) -> dict[str, Any]:
        try:
            value = strict_loads(await request.body())
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConsoleProblem(422, "тело запроса — объект JSON") from exc
        if not isinstance(value, dict):
            raise ConsoleProblem(422, "тело запроса — объект JSON")
        return value

    def owned(session: Session, case_id: str) -> None:
        if not session.owns(case_id):
            raise ConsoleProblem(404, "кейс не найден")

    def passthrough(reply: Reply) -> JSONResponse:
        payload = reply.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return JSONResponse({"detail": detail or "API отказал", "api_status": reply.status}, status_code=reply.status)

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(f"{PREFIX}/", status_code=307)

    @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def robots() -> Response:
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain; charset=utf-8")

    @app.api_route(PREFIX, methods=["GET", "HEAD"], include_in_schema=False)
    def console_bare() -> RedirectResponse:
        return RedirectResponse(f"{PREFIX}/", status_code=307)

    @app.api_route(f"{PREFIX}/", methods=["GET", "HEAD"], include_in_schema=False)
    def page() -> HTMLResponse:
        return HTMLResponse(index, headers={"Content-Security-Policy": PAGE_CSP})

    @app.api_route(f"{PREFIX}/static/{{name}}", methods=["GET", "HEAD"], include_in_schema=False)
    def static(name: str) -> Response:
        kind = STATIC.get(name)
        if kind is None:
            raise ConsoleProblem(404, "файла нет")
        cache = "public, max-age=86400" if name.endswith((".woff2", ".webp", ".svg")) else "public, max-age=3600"
        return Response(_static_bytes(name), media_type=kind, headers={"Cache-Control": cache})

    @app.api_route(f"{PREFIX}/api/cases", methods=["GET", "HEAD"], include_in_schema=False)
    def cases() -> dict[str, Any]:
        return {"demo": catalog(), "operations": operations(), "today": bank_today()}

    @app.get(f"{PREFIX}/api/cases/{{name}}", include_in_schema=False)
    def case_documents(name: str) -> dict[str, Any]:
        """Документы демо-кейса для формы загрузки: их можно поменять и проверить снова."""
        try:
            manifest = demo_manifest(name)
        except KeyError as exc:
            raise ConsoleProblem(404, "такого демо-кейса нет") from exc
        docs = manifest["documents"]
        egrul: Any = next((d["content"] for d in docs if d["kind"] == "egrul_json"), {})
        statuses = {
            str(v.get("status") if isinstance(v, dict) else v) for v in (manifest.get("registry") or {}).values()
        }
        return {
            "name": name,
            "title": manifest.get("title"),
            "poas": [d["content"] for d in docs if d["kind"] == "emchd_xml"],
            "egrul": json.dumps(egrul, ensure_ascii=False, indent=2),
            "charter": next((d["content"] for d in docs if d["kind"] == "charter_text"), ""),
            "operation": manifest["operation"],
            "requester": manifest.get("requester") or {},
            "registry_status": "revoked" if "revoked" in statuses else ("active" if statuses else "unknown"),
        }

    @app.post(f"{PREFIX}/api/session", include_in_schema=False)
    async def session_open(request: Request) -> JSONResponse:
        await json_body(request)
        found = sessions.get(request.cookies.get(COOKIE))
        fresh = found is None
        if found is None:
            limited(request, "session", SESSION_PER_MIN)
            found = sessions.create()
        state, _ = model_client()
        response = JSONResponse(
            {
                "session": found.tag,
                "principals": [
                    {"role": role, "name": found.principal_name(role)} for role in ("operator", "reviewer", "dbo")
                ],
                "cases": found.cases,
                "model": state.as_dict(),
                "advice_label": ADVICE_LABEL,
                "today": bank_today(),
                "dbo_ready": found.dbo is not None,
                "commit": commit,
            }
        )
        if fresh:
            response.set_cookie(
                COOKIE,
                found.sid,
                max_age=int(LIFETIME_SECONDS),
                path=PREFIX,
                httponly=True,
                samesite="strict",
                secure=secure_cookie(),
            )
        return response

    @app.post(f"{PREFIX}/api/check", include_in_schema=False)
    async def check(request: Request) -> JSONResponse:
        session = session_of(request)
        body = await json_body(request)
        if isinstance(body.get("demo"), str):
            try:
                manifest = demo_manifest(body["demo"])
            except KeyError as exc:
                raise ConsoleProblem(404, "такого демо-кейса нет") from exc
            title, source = str(manifest.get("title") or body["demo"]), {"demo": body["demo"]}
        elif isinstance(body.get("upload"), dict):
            try:
                manifest = upload_manifest(body["upload"])
            except UploadError as exc:
                raise ConsoleProblem(422, str(exc)) from exc
            title, source = "Загруженные документы", {"manifest": manifest}
        else:
            raise ConsoleProblem(422, "выберите демо-кейс или загрузите документы")
        started = time.perf_counter()
        reply = await api(session, "operator", "POST", "/v1/cases", manifest)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        if not reply.ok:
            return passthrough(reply)
        report = reply.json()
        session.remember(str(report["case_id"]), title, source)
        return JSONResponse({**summary(report), "title": title, "elapsed_ms": elapsed, "cases": session.cases})

    @app.get(f"{PREFIX}/api/report/{{case_id}}", include_in_schema=False)
    async def report_card(case_id: str, request: Request) -> Response:
        session = session_of(request)
        owned(session, case_id)
        reply = await api(session, "operator", "GET", f"/v1/cases/{case_id}/report.html")
        if not reply.ok:
            return passthrough(reply)
        return HTMLResponse(reply.body, headers={"Content-Security-Policy": CARD_CSP})

    @app.post(f"{PREFIX}/api/consult", include_in_schema=False)
    async def consult(request: Request) -> JSONResponse:
        session = session_of(request)
        body = await json_body(request)
        case_id = str(body.get("case_id") or "")
        owned(session, case_id)
        reply = await api(session, "operator", "GET", f"/v1/cases/{case_id}")
        if not reply.ok:
            return passthrough(reply)
        report = reply.json()
        source = session.sources.get(case_id) or {}
        case = None
        try:
            if "manifest" in source:
                case = load_case_inline(source["manifest"])
            elif "demo" in source:
                case = load_case_inline(demo_manifest(str(source["demo"])))
        except (CaseError, KeyError):
            case = None
        state, client = model_client()
        advice = None
        if body.get("use_model") is True and state.enabled and case is not None:
            # Тот же обезличенный запрос модель читает один раз: принятый ответ берётся из кэша без лимита и слота.
            advice = await run_in_threadpool(cached_advice, case, client)
            if advice is None and not store.allow(f"console:model:{_client(request)}", MODEL_PER_MIN):
                busy = "модель отвечает другим сотрудникам: пояснение собрано из фактов отчёта"
                advice = {"label": ADVICE_LABEL, "error": busy, "busy": True}
            elif advice is None:
                try:
                    task = run_in_threadpool(model_advice, case, client, MODEL_DEADLINE_SECONDS)
                    advice = await asyncio.wait_for(task, MODEL_DEADLINE_SECONDS)
                except TimeoutError:
                    late = f"модель не ответила за {MODEL_DEADLINE_SECONDS:g} с: пояснение собрано из фактов отчёта"
                    advice = {"label": ADVICE_LABEL, "error": late, "late": True}
        return JSONResponse(
            {
                "case_id": case_id,
                "verdict_label": report.get("verdict_label"),
                **deterministic(case, report),
                "model": state.as_dict(),
                "advice": advice,
                "advice_label": ADVICE_LABEL,
            }
        )

    @app.get(f"{PREFIX}/api/review", include_in_schema=False)
    async def review_queue(request: Request) -> JSONResponse:
        session = session_of(request)
        reply = await api(session, "reviewer", "GET", "/v1/review/queue")
        if not reply.ok:
            return passthrough(reply)
        titles = {c["case_id"]: c["title"] for c in session.cases}
        items = [
            {**item, "title": titles[item["case_id"]]} for item in reply.json()["items"] if item["case_id"] in titles
        ]
        return JSONResponse({"items": items, "reviewer": session.principal_name("reviewer")})

    @app.post(f"{PREFIX}/api/review/{{case_id}}/{{action}}", include_in_schema=False)
    async def review_action(case_id: str, action: str, request: Request) -> JSONResponse:
        session = session_of(request)
        body = await json_body(request)
        owned(session, case_id)
        role = REVIEW_ACTIONS.get(action)
        if role is None:
            raise ConsoleProblem(404, "действие разбора: " + ", ".join(REVIEW_ACTIONS))
        version, reason = body.get("expected_version"), body.get("reason", "")
        if isinstance(version, bool) or not isinstance(version, int) or not isinstance(reason, str):
            raise ConsoleProblem(422, "нужны expected_version и reason")
        payload = {"expected_version": version, "reason": reason.strip()[:300]}
        reply = await api(session, role, "POST", f"/v1/cases/{case_id}/review/{action}", payload)
        if not reply.ok:
            return passthrough(reply)
        return JSONResponse({**reply.json(), "actor": session.principal_name(role)})

    def registry_client() -> dict[str, Any]:
        """Демо-клиент реестра: организация демо-кейсов и sha256 его устава в той же записи, что у загрузчика."""
        manifest = demo_manifest(REGISTRY_CASE)
        charter = next(d["content"] for d in manifest["documents"] if d["kind"] == "charter_text")
        organization = manifest["organization"]
        return {
            "name": organization.get("name"),
            "ogrn": organization.get("ogrn"),
            "charter_sha256": hashlib.sha256(str(charter).encode("utf-8")).hexdigest(),
            "case": REGISTRY_CASE,
        }

    @app.get(f"{PREFIX}/api/conclusions", include_in_schema=False)
    async def registry_list(request: Request) -> JSONResponse:
        session = session_of(request)
        client = registry_client()
        reply = await api(session, "reviewer", "GET", "/v1/conclusions", None, {"ogrn": str(client["ogrn"])})
        if not reply.ok:
            return passthrough(reply)
        mine = {session.principal_name(role) for role in ("operator", "reviewer")}
        items = [{**item, "mine": item.get("author") in mine} for item in reply.json()["items"][:20]]
        return JSONResponse({"client": client, "items": items, "lawyer": session.principal_name("reviewer")})

    @app.post(f"{PREFIX}/api/conclusions", include_in_schema=False)
    async def registry_draft(request: Request) -> JSONResponse:
        session = session_of(request)
        body = await json_body(request)
        reading = REGISTRY_READING.get(str(body.get("reading")))
        basis = body.get("basis", "")
        if reading is None or not isinstance(basis, str) or not basis.strip():
            raise ConsoleProblem(422, "вывод: no_restriction или threshold; основание — непустой текст")
        if session.drafts >= REGISTRY_DRAFTS_PER_SESSION:
            raise ConsoleProblem(429, f"в одной сессии не больше {REGISTRY_DRAFTS_PER_SESSION} черновиков")
        client = registry_client()
        until = bank_today()
        payload = {
            "ogrn": client["ogrn"],
            "charter_sha256": client["charter_sha256"],
            "basis": basis.strip()[:300],
            "operations": ["PAYMENT_SIGN"],
            "valid_until": f"{int(until[:4]) + 1}{until[4:]}"
            if until[5:] != "02-29"
            else f"{int(until[:4]) + 1}-02-28",
            **reading,
        }
        reply = await api(session, "operator", "POST", "/v1/conclusions", payload)
        if not reply.ok:
            return passthrough(reply)
        session.drafts += 1
        return JSONResponse({**reply.json(), "actor": session.principal_name("operator")})

    @app.post(f"{PREFIX}/api/conclusions/check", include_in_schema=False)
    async def registry_check(request: Request) -> JSONResponse:
        """Демо-кейс в строгом профиле: находка R-CHR-002 называет применённое заключение или причину отказа."""
        session = session_of(request)
        await json_body(request)
        reply = await api(session, "operator", "POST", "/v1/cases", demo_manifest(REGISTRY_CASE), {"profile": "strict"})
        if not reply.ok:
            return passthrough(reply)
        report = reply.json()
        session.remember(str(report["case_id"]), "Реестр: кейс 01_ok в строгом профиле", {"demo": REGISTRY_CASE})
        charter = [f for f in report.get("findings") or [] if str(f.get("rule_id")).startswith("R-CHR")]
        return JSONResponse(
            {
                **summary(report),
                "title": "Кейс 01_ok в строгом профиле",
                "charter": charter,
                "conclusions": report.get("charter_conclusions") or {"applied": [], "not_applied": []},
                "cases": session.cases,
            }
        )

    @app.post(f"{PREFIX}/api/conclusions/{{conclusion_id}}/{{action}}", include_in_schema=False)
    async def registry_action(conclusion_id: str, action: str, request: Request) -> JSONResponse:
        session = session_of(request)
        body = await json_body(request)
        role = REGISTRY_ACTIONS.get(action)
        if role is None:
            raise ConsoleProblem(404, "действие с заключением: " + ", ".join(REGISTRY_ACTIONS))
        version, reason = body.get("expected_version"), body.get("reason", "")
        if isinstance(version, bool) or not isinstance(version, int) or not isinstance(reason, str):
            raise ConsoleProblem(422, "нужны expected_version и reason")
        payload = {"expected_version": version, "reason": reason.strip()[:300]}
        reply = await api(session, role, "POST", f"/v1/conclusions/{conclusion_id}/{action}", payload)
        if not reply.ok:
            return passthrough(reply)
        return JSONResponse({**reply.json(), "actor": session.principal_name(role)})

    @app.post(f"{PREFIX}/api/dbo/issue", include_in_schema=False)
    async def dbo_issue(request: Request) -> JSONResponse:
        session = session_of(request)
        await json_body(request)
        if session.issued >= DBO_PER_SESSION:
            raise ConsoleProblem(429, f"в одной сессии не больше {DBO_PER_SESSION} удостоверений")
        reply = await api(session, "operator", "POST", "/v1/cases", rebased_today(demo_manifest(DBO_CASE)))
        if not reply.ok:
            return passthrough(reply)
        report = reply.json()
        if report.get("verdict") != Verdict.CONFIRMED.value:
            raise ConsoleProblem(
                409, f"кейс получил вердикт {report.get('verdict_label')}: удостоверение не выпускается"
            )
        nonce = "stand-" + secrets.token_hex(max(8, NONCE_MIN_CHARS // 2))
        try:
            credential, index = await run_in_threadpool(issuer.issue, report, audience=AUDIENCE, nonce=nonce)
        except IssuerExhausted as exc:
            raise ConsoleProblem(429, str(exc)) from exc
        session.issued += 1
        operation = report["operation"]
        session.dbo = DboState(
            credential=credential,
            nonce=nonce,
            index=index,
            case_id=str(report["case_id"]),
            amount_rub=int(operation["amount_rub"]),
            organization_inn=str(report["organization"]["inn"]),
            representative_inn=str(report["requester"]["inn"]),
            operation=str(operation["code"]),
            operation_date=str(operation["date"]),
        )
        session.remember(str(report["case_id"]), "ДБО: платёж сегодня", {"demo": DBO_CASE})
        return JSONResponse(
            {
                **summary(report),
                "credential": _credential_view(credential),
                "amount_rub": session.dbo.amount_rub,
                "audience": AUDIENCE,
                "cases": session.cases,
            }
        )

    def takeover(session: Session, state: DboState) -> list[str]:
        """Признаки захвата по организации: мониторинг приостанавливает удостоверение, как в ``mandat showcase``."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        payload = {
            "rights": [
                {
                    "right_id": f"R-{session.tag}",
                    "org_inn": state.organization_inn,
                    "representative_inn": state.representative_inn,
                    "operation": state.operation,
                    "poa_numbers": (state.credential.get("credentialSubject") or {}).get("authorityChain") or [],
                }
            ],
            "events": [
                {
                    "kind": "EIO_CHANGED",
                    "org_inn": state.organization_inn,
                    "at": now,
                    "payload": {"grn": "2267700999001"},
                },
                {
                    "kind": "NEW_POA",
                    "org_inn": state.organization_inn,
                    "at": now,
                    "payload": {"number": "11111111-2222-3333-4444-555555555555"},
                },
                {
                    "kind": "REQUISITES_CHANGE_REQUEST",
                    "org_inn": state.organization_inn,
                    "at": now,
                    "payload": {"field": "счёт для выплат"},
                },
            ],
        }
        rights, events = parse_events(payload)
        alerts = [a for a in evaluate(rights, events) if a.attestation_action]
        if alerts and issuer.suspend(state.index, alerts[0].kind):
            state.suspended = True
        return sorted({a.kind for a in alerts})

    @app.post(f"{PREFIX}/api/dbo/authorize", include_in_schema=False)
    async def dbo_authorize(request: Request) -> JSONResponse:
        session = session_of(request)
        body = await json_body(request)
        scenario, channel = body.get("scenario"), body.get("channel", "rest")
        if scenario not in DBO_SCENARIOS or channel not in {"rest", "authzen"}:
            raise ConsoleProblem(422, "сценарий: pay, replay, over, takeover; канал: rest или authzen")
        state = session.dbo
        if state is None:
            raise ConsoleProblem(409, "сначала выпустите удостоверение")
        alerts: list[str] = []
        if scenario == "takeover":
            alerts = await run_in_threadpool(takeover, session, state)
        amount = state.amount_rub * 4 if scenario == "over" else state.amount_rub
        if channel == "rest":
            path = "/v1/operations/authorize"
            payload: dict[str, Any] = {
                "credential": state.credential,
                "request": {
                    "organization_inn": state.organization_inn,
                    "operation": state.operation,
                    "operation_date": state.operation_date,
                    "amount_rub": amount,
                    "representative_inn": state.representative_inn,
                    "audience": AUDIENCE,
                    "nonce": state.nonce,
                },
                "operation_risk": "payment",
            }
        else:
            path = "/access/v1/evaluation"
            payload = {
                "subject": {"type": "representative", "id": state.representative_inn},
                "resource": {"type": "organization", "id": state.organization_inn},
                "action": {
                    "name": state.operation,
                    "properties": {"operation_date": state.operation_date, "amount_rub": amount},
                },
                "context": {
                    "credential": state.credential,
                    "nonce": state.nonce,
                    "audience": AUDIENCE,
                    "operation_risk": "payment",
                },
            }
        reply = await api(session, "dbo", "POST", path, payload)
        if not reply.ok:
            return passthrough(reply)
        answer = reply.json()
        if channel == "rest":
            allowed, problems = bool(answer.get("allowed")), list(answer.get("problems") or [])
        else:
            allowed = bool(answer.get("decision"))
            problems = [str(p) for p in (answer.get("context") or {}).get("problems") or []]
        return JSONResponse(
            {
                "scenario": scenario,
                "channel": channel,
                "endpoint": path,
                "allowed": allowed,
                "problems": problems,
                "amount_rub": amount,
                "alerts": alerts,
                "answer": answer,
            }
        )

    @app.get(f"{PREFIX}/api/gate", include_in_schema=False)
    async def gate(request: Request) -> JSONResponse:
        """Шлюз на подставных ответах модели: результат считает настоящий код шлюза, а не таблица."""
        session_of(request)
        return JSONResponse(await run_in_threadpool(gate_proof))

    @app.get(f"{PREFIX}/api/stats", include_in_schema=False)
    def stats(request: Request) -> JSONResponse:
        """Блок «Расход модели»: счётчики консультанта с запуска службы — без ключа, запросов и ПДн."""
        session_of(request)
        return JSONResponse(usage_view())

    @app.get(f"{PREFIX}/api/analytics", include_in_schema=False)
    def analytics_tab(request: Request) -> JSONResponse:
        """Вкладка «Аналитика»: только чтение — счётчики сессии, evidence.json, модель и расход; без ПДн."""
        return JSONResponse(analytics.view(session_of(request), store))

    @app.post(f"{PREFIX}/api/eval", include_in_schema=False)
    async def eval_run(request: Request) -> JSONResponse:
        session_of(request)
        body = await json_body(request)
        items = _eval_items(body)
        key = json.dumps([body.get("source", "demo"), [label for label, _, _ in items]], ensure_ascii=False)
        cached = evals.cache.get(key) if body.get("source", "demo") != "pasted" else None
        if cached is not None and time.monotonic() - cached[0] < EVAL_CACHE_SECONDS:
            return JSONResponse({**cached[1], "cached": True})
        limited(request, "eval", EVAL_PER_MIN)
        if not evals.lock.acquire(blocking=False):
            raise ConsoleProblem(429, "прогон уже идёт: результат будет через секунду", {"Retry-After": "5"})
        try:
            result = await run_in_threadpool(_eval, items)
        finally:
            evals.lock.release()
        evals.cache[key] = (time.monotonic(), result)
        if len(evals.cache) > 64:
            evals.cache.pop(next(iter(evals.cache)))
        return JSONResponse({**result, "cached": False})


def _eval_items(body: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """Кейсы прогона с ожидаемым вердиктом: выбранные демо-кейсы, корпус по видам дефекта или свои, до 50."""
    source = body.get("source", "demo")
    items: list[tuple[str, dict[str, Any], str]] = []
    if source == "demo":
        names = body.get("names") or [row["name"] for row in catalog()]
        expected = {row["name"]: row["expected"] for row in catalog()}
        if not isinstance(names, list) or not all(isinstance(n, str) and n in expected for n in names):
            raise ConsoleProblem(422, "names: имена демо-кейсов из списка")
        items = [(name, demo_manifest(name), expected[name]) for name in dict.fromkeys(names)]
    elif source == "corpus":
        from mandat.eval.synth import DEFECTS, build

        items = [(defect, build(defect, seed=7000).inline_manifest(), expected) for defect, expected in DEFECTS.items()]
    elif source == "pasted":
        cases = body.get("cases")
        if not isinstance(cases, list) or not cases or not all(isinstance(c, dict) for c in cases):
            raise ConsoleProblem(422, "cases: непустой список манифестов JSON с полем expected_verdict")
        for n, manifest in enumerate(cases[: EVAL_MAX_CASES + 1], start=1):
            want = manifest.get("expected_verdict")
            if want not in {v.value for v in Verdict}:
                raise ConsoleProblem(
                    422, f"кейс {n}: expected_verdict — confirmed, refuted, needs_review или insufficient_data"
                )
            items.append((str(manifest.get("case_id") or f"кейс {n}")[:80], manifest, str(want)))
    else:
        raise ConsoleProblem(422, "source: demo, corpus или pasted")
    if len(items) > EVAL_MAX_CASES:
        raise ConsoleProblem(422, f"в одном прогоне не больше {EVAL_MAX_CASES} кейсов")
    return items


def _eval(items: list[tuple[str, dict[str, Any], str]]) -> dict[str, Any]:
    """Прогон без записи кейсов: тот же движок, профиль demo; ложное ПОДТВЕРЖДЕНО считается отдельно."""
    started = time.perf_counter()
    rows = []
    for label, manifest, expected in items:
        try:
            predicted: str = verify_case(load_case_inline(manifest), profile="demo").verdict.value
        except CaseError:
            predicted = "error"
        rows.append({"case": label, "expected": expected, "predicted": predicted})
    false_confirmed = sum(r["predicted"] == "confirmed" and r["expected"] != "confirmed" for r in rows)
    for row in rows:
        row["expected_label"] = VERDICT_LABEL_RU[Verdict(row["expected"])]
        row["predicted_label"] = (
            VERDICT_LABEL_RU[Verdict(row["predicted"])] if row["predicted"] != "error" else "ошибка входа"
        )
    return {
        "cases": len(rows),
        "correct": sum(r["expected"] == r["predicted"] for r in rows),
        "false_confirmed": false_confirmed,
        "false_confirmed_label": f"ложных ПОДТВЕРЖДЕНО: {false_confirmed}",
        "to_review": sum(r["predicted"] in {"needs_review", "insufficient_data"} for r in rows),
        "refuted": sum(r["predicted"] == "refuted" for r in rows),
        "errors": sum(r["predicted"] == "error" for r in rows),
        "rows": rows,
        "seconds": round(time.perf_counter() - started, 3),
        "boundary": "демо-кейсы и синтетика проверяют логику правил; точность на документах банка измеряет пилот",
    }
