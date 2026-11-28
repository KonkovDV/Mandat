"""MCP-сервер «Мандата»: те же проверка кейса и допуск операции для ИИ-агентов банка.

ИИ-агент, который действует за клиента — готовит или проводит платёж, ведёт корпоративного клиента, — тоже
представитель: прежде чем подписать или отправить операцию, он спрашивает «Мандат» и ответ переписать не может.
Model Context Protocol 2025-06-18, транспорт stdio: одно сообщение JSON-RPC 2.0 на строку во входе и в выходе,
в выход не пишется ничего, кроме сообщений, журнал сервера идёт в stderr. Своего SDK нет — стандартная библиотека.

Инструменты:

* ``check_authority`` — манифест кейса, как в теле ``POST /v1/cases``: тот же ``verify_case`` с профилем по
  ``env_policy``, запись ``verify`` в журнал аудита; в ответе вердикт, причины, следующие шаги и ``report_id``;
* ``admit_operation`` — запрос OpenID AuthZEN, как у ``/access/v1/evaluation``: решение ``admission.admit`` с теми
  же отказами, тем же одноразовым ``nonce`` в общем хранилище и записью ``authorize_operation``;
* ``get_report`` — полный отчёт, но только по ``report_id``, который выдала эта сессия: перебором идентификаторов
  чужой кейс не прочитать. Чтение пишется в журнал (``read_case``).

Кто спрашивает — учётная запись API по токену ``MANDAT_MCP_TOKEN`` из ``MANDAT_API_USERS`` (на стенде — токен
``MANDAT_API_TOKEN``): её роль и ``audiences`` решают то же, что в HTTP, её имя пишется в журнал. Вход — строгий
JSON с пределами HTTP API (``strictjson``). Модели внутри нет, в сеть сервер не ходит: ответ считают правила.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import sys
import traceback
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from mandat import __version__
from mandat.api import authzen
from mandat.api.principals import Directory, Principal, access_problem, load_users
from mandat.api.sealing import sealed
from mandat.api.storage import Store, open_store
from mandat.audit import journal
from mandat.domain.verdict import get_profile
from mandat.intake.case_loader import KINDS, CaseError, load_case_inline
from mandat.services import admission
from mandat.services.env_policy import PROFILES, ProfileNotAllowed, protected, resolve_profile
from mandat.services.next_steps import check_sla
from mandat.services.verify import verify_case
from mandat.strictjson import MAX_BODY, strict_loads

PROTOCOL_VERSION = "2025-06-18"
TITLE = "Мандат — проверка полномочий"
TOKEN_ENV = "MANDAT_MCP_TOKEN"  # noqa: S105 - имя переменной, не значение
# Тот же журнал аудита, что у HTTP API: проверки и допуски из обоих каналов лежат в одной цепочке.
JOURNAL_ENV = "MANDAT_API_JOURNAL"
# Отчёты сессии живут в памяти процесса: старые вытесняются, чтобы долгая сессия агента не съела память.
MAX_SESSION_REPORTS = 100
MAX_REASONS = 10
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

log = logging.getLogger("mandat.mcp")

INSTRUCTIONS = (
    "«Мандат» отвечает, вправе ли представитель юрлица провести операцию за организацию: по МЧД, выписке ЕГРЮЛ "
    "и уставу. Агент, который действует за клиента, — тоже представитель: перед подписью или отправкой операции "
    "спросите «Мандат». check_authority проверяет кейс по документам, admit_operation допускает одну операцию по "
    "удостоверению, get_report отдаёт полный отчёт проверки этой сессии. Ответ считают правила с нормой, а не "
    "модель, и он окончательный: не смягчайте его, не толкуйте по-своему и не повторяйте запрос с изменёнными "
    "данными ради другого ответа. Вердикты «на разбор» и «недостаточно данных» передайте сотруднику банка вместе с "
    "report_id и следующими шагами."
)
GUIDANCE = {
    "confirmed": "Полномочие подтверждено правилами на эту операцию, дату и сумму. Это ещё не допуск: перед "
    "операцией нужен admit_operation по удостоверению «Мандата».",
    "refuted": "Полномочие опровергнуто правилами. Операцию не проводите и обход не ищите: передайте причины и "
    "следующие шаги сотруднику банка.",
    "needs_review": "Автоматического решения нет: кейс решает человек. Передайте сотруднику банка report_id и "
    "следующие шаги; причины сами не толкуйте и проверку с изменёнными данными не повторяйте.",
    "insufficient_data": "Данных для решения недостаточно: кейс решает человек. Передайте сотруднику банка report_id "
    "и следующие шаги; недостающие документы сами не додумывайте.",
}
ADMITTED = "Операция допущена по удостоверению «Мандата» один раз: этот nonce второго допуска не даст."
DENIED = (
    "Операция не допущена. Не проводите её и не повторяйте запрос с другими данными: передайте причины сотруднику "
    "банка."
)
REPLAYED = (
    "Этот же запрос уже получил допуск (admitted_at), второго допуска нет. Если неизвестно, проведена ли операция "
    "по первому допуску, — спросите сотрудника банка, а не повторяйте запрос."
)
NO_TOKEN = f"токен {TOKEN_ENV} не действует: учётной записи нет, срок истёк или она отозвана"
INTERNAL = "внутренняя ошибка «Мандата»: ответа нет. Нет ответа — нет допуска: передайте запрос сотруднику банка."

_INN = {"type": "string", "pattern": r"^\d{10}(\d{2})?$"}
TOOLS: list[dict[str, Any]] = [
    {
        "name": "check_authority",
        "title": "Проверить полномочие представителя",
        "description": (
            "Проверяет полномочие представителя юрлица на операцию по документам кейса: МЧД, выписка ЕГРЮЛ, устав, "
            "передоверие, банкротство. Вход — манифест кейса, тот же JSON, что принимает POST /v1/cases. Ответ: "
            "verdict (confirmed — подтверждено, refuted — опровергнуто, needs_review — на разбор, insufficient_data "
            "— недостаточно данных), reasons — находки правил, которые привели к вердикту, next_steps — что "
            "сделать сотруднику банка и какому подразделению, report_id для get_report и reproducibility_hash "
            "отчёта. Вердикт окончательный: его считают правила с нормой, модели внутри нет. needs_review и "
            "insufficient_data передайте человеку — сотруднику из next_steps вместе с report_id; причины сами не "
            "толкуйте и документы не дополняйте. confirmed — ещё не допуск: перед операцией нужен admit_operation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "manifest": {
                    "type": "object",
                    "description": "Манифест кейса, контракт docs/api/case-manifest.schema.json. Реквизиты — "
                    "строками: число теряет ведущие нули. Ещё можно передать registry, bankruptcy, "
                    "charter_disposition и subdelegation_forms.",
                    "required": ["organization", "operation"],
                    "properties": {
                        "organization": {"type": "object", "description": "inn, ogrn, kpp, name"},
                        "requester": {"type": "object", "description": "заявитель: full_name, inn, snils, position"},
                        "operation": {
                            "type": "object",
                            "description": "code (например PAYMENT_SIGN), date ГГГГ-ММ-ДД, amount_rub в целых рублях",
                        },
                        "documents": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["id", "kind", "content"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "kind": {"enum": sorted(KINDS)},
                                    "content": {
                                        "description": "XML МЧД или текст строкой, выписка ЕГРЮЛ — объектом или строкой"
                                    },
                                },
                            },
                        },
                    },
                },
                "profile": {
                    "type": "string",
                    "enum": sorted(PROFILES),
                    "description": "Профиль проверки; по умолчанию demo на стенде и strict в pilot и prod, где demo "
                    "запрещён",
                },
            },
            "required": ["manifest"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "admit_operation",
        "title": "Допустить операцию по удостоверению",
        "description": (
            "Допуск одной операции по удостоверению «Мандата» перед тем, как её провести. Вход — тот же запрос "
            "OpenID AuthZEN, что ДБО присылает на /access/v1/evaluation: subject — представитель по ИНН, resource — "
            "организация по ИНН, action — код операции с днём операции по Москве и суммой в целых рублях, context — "
            "удостоверение целиком, одноразовый nonce и получатель audience. Ответ: decision (true — допуск), "
            "причины отказа в context.problems, evidence — хеш отчёта, по которому выпущено удостоверение, и запись "
            "решения в журнале аудита. Решение окончательное: при отказе операцию не проводите и не повторяйте "
            "запрос с другими данными, передайте причины сотруднику банка. Один nonce даёт не больше одного "
            "допуска, повтор того же запроса — отказ с already_admitted. Нет ответа — нет допуска."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "object",
                    "required": ["type", "id"],
                    "properties": {"type": {"const": authzen.SUBJECT_TYPE}, "id": _INN},
                },
                "resource": {
                    "type": "object",
                    "required": ["type", "id"],
                    "properties": {"type": {"const": authzen.RESOURCE_TYPE}, "id": _INN},
                },
                "action": {
                    "type": "object",
                    "required": ["name", "properties"],
                    "properties": {
                        "name": {"type": "string", "description": "код операции, например PAYMENT_SIGN"},
                        "properties": {
                            "type": "object",
                            "required": ["operation_date"],
                            "properties": {
                                "operation_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                                "amount_rub": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                },
                "context": {
                    "type": "object",
                    "required": ["credential", "nonce", "audience"],
                    "properties": {
                        "credential": {"type": "object", "description": "удостоверение «Мандата» целиком"},
                        "nonce": {"type": "string", "minLength": 16, "maxLength": 200},
                        "audience": {"type": "string", "description": "система банка, для которой выпущено"},
                        "operation_risk": {"type": "string", "description": "может только сузить окно свежести"},
                    },
                },
            },
            "required": ["subject", "action", "resource", "context"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_report",
        "title": "Полный отчёт проверки этой сессии",
        "description": (
            "Полный отчёт проверки по report_id из ответа check_authority: цепочка полномочий, все находки с "
            "доказательствами и нормами, предупреждения, маршрут разбора. Выдаёт только отчёты, которые "
            "check_authority выдал этой сессии; другой report_id не выдаётся. В отчёте персональные данные: "
            "показывайте его только сотруднику банка, которому он нужен для работы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"report_id": {"type": "string", "description": "report_id из ответа check_authority"}},
            "required": ["report_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


class ToolError(Exception):
    """Отказ инструмента: текст по-русски уходит вызывающей модели как результат с ``isError``."""


class InvalidParams(Exception):
    """Запрос MCP не по протоколу: ошибка JSON-RPC -32602."""


Tool = Callable[[Principal, dict[str, Any]], dict[str, Any]]


class Session:
    """Сессия MCP — один процесс: кто спрашивает, хранилище ``nonce``, журнал и отчёты, выданные этой сессии."""

    def __init__(
        self,
        *,
        directory: Directory,
        token: str,
        store: Store,
        journal_path: Path | None = None,
        admission_config: admission.AdmissionConfig | None = None,
    ) -> None:
        self.directory, self.token, self.store = directory, token, store
        self.journal_path, self.admission_config = journal_path, admission_config
        self.reports: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.tools: dict[str, Tool] = {
            "check_authority": self.check_authority,
            "admit_operation": self.admit_operation,
            "get_report": self.get_report,
        }

    @classmethod
    def from_env(cls) -> Session:
        """Сессия из окружения с проверками ``mandat serve`` при старте. Неполная настройка — ``ValueError``."""
        from mandat.audit.anchor import missing_anchor_problem
        from mandat.services.env_policy import key_problem, require_attestation_status

        require_attestation_status()
        check_sla()
        problem = access_problem() or key_problem() or missing_anchor_problem()
        if problem:
            raise ValueError(problem)
        raw_users = os.environ.get("MANDAT_API_USERS", "").strip()
        users_file = Path(raw_users) if raw_users and not raw_users.startswith("[") else None
        raw_journal = os.environ.get(JOURNAL_ENV, "").strip()
        session = cls(
            directory=Directory(load_users(), os.environ.get("MANDAT_API_TOKEN", ""), reload_path=users_file),
            token=os.environ.get(TOKEN_ENV, "").strip(),
            store=sealed(open_store()),
            journal_path=Path(raw_journal) if raw_journal else None,
            admission_config=admission.AdmissionConfig.from_env(protected=protected()),
        )
        if session.caller() is None:
            raise ValueError(
                f"{NO_TOKEN}: задайте токен учётной записи из MANDAT_API_USERS (на стенде — MANDAT_API_TOKEN)"
            )
        if protected() and session.journal_path is None:
            raise ValueError(f"в защищённом режиме задайте {JOURNAL_ENV}: без журнала аудита инструменты не отвечают")
        return session

    def caller(self) -> Principal | None:
        """Учётная запись по токену. Проверяется на каждом вызове: отзыв и срок действуют без перезапуска."""
        return self.directory.find(self.token) if self.token else None

    # --- JSON-RPC ---------------------------------------------------------------------------------------------

    def receive(self, line: bytes) -> dict[str, Any] | None:
        """Ответ на одну строку входа; ``None`` — отвечать не нужно (уведомление или ответ клиента)."""
        try:
            message = strict_loads(line)
        except json.JSONDecodeError:
            return _error(None, PARSE_ERROR, "сообщение не строгий JSON: синтаксис, повтор ключа, NaN или глубина")
        return self.handle(message)

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "сообщение — объект JSON-RPC 2.0; пакетов MCP 2025-06-18 нет")
        method = message.get("method")
        if method is None and ("result" in message or "error" in message):
            return None  # ответ клиента: сервер сам запросов не шлёт
        request_id = message.get("id")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str) or ("id" in message and not _id(request_id)):
            return _error(request_id if _id(request_id) else None, INVALID_REQUEST, "нужны jsonrpc 2.0 и method")
        if "id" not in message:
            return None  # уведомление (notifications/initialized, notifications/cancelled): ответа нет
        params = message.get("params")
        if not isinstance(params, dict | None):
            return _error(request_id, INVALID_PARAMS, "params — объект")
        params = params or {}
        try:
            if method == "initialize":
                result = self.initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call(params)
            else:
                return _error(request_id, METHOD_NOT_FOUND, f"метод {method[:64]!r} не поддерживается")
        except InvalidParams as exc:
            return _error(request_id, INVALID_PARAMS, str(exc))
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        offered = params.get("protocolVersion")
        return {
            "protocolVersion": offered if isinstance(offered, str) and 0 < len(offered) <= 32 else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mandat", "title": TITLE, "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or name not in self.tools:
            raise InvalidParams(f"инструмента {str(name)[:64]!r} нет: {', '.join(self.tools)}")
        arguments = params.get("arguments")
        try:
            if not isinstance(arguments, dict | None):
                raise ToolError("arguments — объект JSON с аргументами инструмента")
            principal = self.caller()
            if principal is None:
                raise ToolError(NO_TOKEN)
            payload = self.tools[name](principal, arguments or {})
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception as exc:
            frames = " < ".join(
                f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
            )
            log.warning("mcp tool=%s error=%s @ %s", name, type(exc).__name__, frames)
            return {"content": [{"type": "text", "text": INTERNAL}], "isError": True}
        text = json.dumps(payload, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "structuredContent": payload, "isError": False}

    # --- инструменты ------------------------------------------------------------------------------------------

    def check_authority(self, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
        """Тот же ``verify_case``, что за ``POST /v1/cases``: профиль по политике режима, ``case_id`` от сервера."""
        _only(args, "manifest", "profile")
        if not principal.can_write:
            raise ToolError(
                f"роль {principal.role} кейсы не проверяет: нужна учётная запись operator, reviewer или admin"
            )
        manifest = args.get("manifest")
        if not isinstance(manifest, dict):
            raise ToolError("manifest — объект JSON: манифест кейса, как в теле POST /v1/cases")
        profile = args.get("profile")
        if profile is not None and (not isinstance(profile, str) or profile not in PROFILES):
            raise ToolError("profile: demo или strict")
        try:
            resolved = resolve_profile(profile)
        except ProfileNotAllowed as exc:
            raise ToolError(str(exc)) from exc
        self._need_journal()
        case_id = f"case-{secrets.token_hex(8)}"
        try:
            case = load_case_inline({**manifest, "case_id": case_id})
        except CaseError as exc:
            raise ToolError(f"кейс не прошёл проверку входных данных: {exc}") from exc
        # В pilot и prod снимки источников сверяются с часами сервера: снимок «из будущего» не доказательство.
        report = verify_case(case, profile=resolved, now=datetime.now(UTC) if protected() else None).as_dict()
        if manifest.get("case_id") is not None:
            report["client_case_id"] = str(manifest["case_id"])
        details = {"verdict": report["verdict"], "profile": report["profile"], "via": "mcp"}
        self._journal("verify", principal, case_id, report["reproducibility_hash"], details, "кейс не сохранён")
        self.reports[case_id] = report
        while len(self.reports) > MAX_SESSION_REPORTS:
            self.reports.popitem(last=False)
        return verdict_summary(report)

    def admit_operation(self, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
        """Тот же допуск, что за ``/access/v1/evaluation``: роль, настройка, профиль AuthZEN, ``admission.admit``."""
        if not admission.may_request(principal, protected=protected()):
            raise ToolError("допуск операции выдаётся только учётной записи системы банка (роль dbo)")
        try:
            config = admission.ready(self.admission_config, self.store, self.journal_path, protected=protected())
            request = authzen.admission_of(args)
            admitted = admission.admit(
                request.credential,
                request.fields(),
                request.operation_risk,
                principal,
                config=config,
                store=self.store,
                journal_path=self.journal_path,
                via="mcp",
                protected=protected(),
            )
        except authzen.AuthzenError as exc:
            raise ToolError(f"{exc.message}: {exc.cause}") from exc
        except admission.AdmissionRefused as exc:
            raise ToolError(exc.detail) from exc
        return admission_summary(admitted, request.credential)

    def get_report(self, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
        """Отчёт только из этой сессии; чтение ПДн пишется в журнал, как ``GET /v1/cases/{id}``."""
        _only(args, "report_id")
        report_id = args.get("report_id")
        if not isinstance(report_id, str) or not report_id:
            raise ToolError("report_id — строка из ответа check_authority")
        report = self.reports.get(report_id)
        if report is None:
            raise ToolError(
                "такого report_id эта сессия не выдавала: get_report отдаёт только отчёты check_authority этой "
                f"сессии, последние {MAX_SESSION_REPORTS}"
            )
        self._need_journal()
        self._journal(
            "read_case", principal, report_id, report["reproducibility_hash"], {"via": "mcp"}, "отчёт не выдан"
        )
        return report

    def _need_journal(self) -> None:
        if self.journal_path is None and protected():
            raise ToolError(f"журнал аудита не настроен: задайте {JOURNAL_ENV}")

    def _journal(
        self, action: str, principal: Principal, case_id: str, payload: str, details: dict[str, Any], outcome: str
    ) -> None:
        """Запись о действии, как у HTTP API. Журнал недоступен — действия нет: ``outcome`` объясняет, чего нет."""
        if self.journal_path is None:
            return
        try:
            journal.append(
                self.journal_path,
                action=action,
                actor=principal.name,
                role=principal.role,
                case_id=case_id,
                payload_hash=payload,
                details=details,
            )
        except (ValueError, OSError, journal.JournalKeyMissing) as exc:
            raise ToolError(f"журнал аудита недоступен, {outcome}") from exc


def verdict_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Короткий ответ проверки: вердикт, находки, которые к нему привели, шаги сотрудника и ссылка на отчёт.

    Причины — находки правил с эффектом «опровергает», «не проверено» или «на разбор» по проверкам, которые профиль
    выполняет: те же, из которых собраны следующие шаги. Справка модели вердикт не меняет и в причины не попадает.
    """
    verdict = report["verdict"]
    waived = get_profile(report["profile"]).waived
    reasons = [
        {"rule_id": f["rule_id"], "effect": f["effect"], "message": f["message"], "norm": f["norm_ref"]}
        for f in report["findings"]
        if f["effect"] in {"refutes", "not_verified", "needs_review"}
        and f["check"] not in waived
        and f["origin"] == "deterministic"
    ]
    return {
        "report_id": report["case_id"],
        "verdict": verdict,
        "verdict_label": report["verdict_label"],
        "hand_to_human": verdict in {"needs_review", "insufficient_data"},
        "what_to_do": GUIDANCE[verdict],
        "reasons": reasons[:MAX_REASONS],
        "next_steps": [{"action": s["action"], "unit": s["unit"]} for s in report["next_steps"]],
        "routing": report["routing"],
        "signals": report["signals"],
        "warnings": report["warnings"],
        "operation": report["operation"],
        "profile": report["profile"],
        "reproducibility_hash": report["reproducibility_hash"],
    }


def admission_summary(admitted: admission.Admitted, credential: dict[str, Any]) -> dict[str, Any]:
    """Решение в конверте AuthZEN, как у ``/access/v1/evaluation``, со ссылкой на доказательство и подсказкой."""
    answer, record = admitted.answer, admitted.record
    subject = credential.get("credentialSubject")
    report_sha = subject.get("reportSha256") if isinstance(subject, dict) else None
    if answer["allowed"]:
        what = ADMITTED
    else:
        what = REPLAYED if answer["already_admitted"] else DENIED
    return {
        **authzen.decision(answer),
        "evidence": {
            "report_sha256": report_sha[:64] if isinstance(report_sha, str) else None,
            "journal_record": {"seq": record["seq"], "hash": record["hash"]} if record else None,
        },
        "what_to_do": what,
    }


def _only(args: dict[str, Any], *allowed: str) -> None:
    extra = sorted(set(args) - set(allowed))
    if extra:
        raise ToolError(f"лишний аргумент {extra[0][:64]!r}: допустимы {', '.join(allowed)}")


def _id(value: Any) -> bool:
    """Идентификатор запроса MCP — строка или целое (не null и не дробь)."""
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _encode(message: dict[str, Any]) -> bytes:
    """Одна строка UTF-8 без переводов строки внутри: их экранирует JSON."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace") + b"\n"


def serve(stdin: BinaryIO, stdout: BinaryIO, session: Session | None = None) -> int:
    """Читать сообщения до конца входа. Строка больше ``MAX_BODY`` не читается целиком: отказ, сессия идёт дальше.

    Случайный ``print`` в коде проверки уходит в stderr, а не портит поток сообщений.
    """
    session = session or Session.from_env()
    with contextlib.redirect_stdout(sys.stderr):
        while line := stdin.readline(MAX_BODY + 1):
            if len(line) > MAX_BODY and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):
                    line = stdin.readline(MAX_BODY + 1)
                reply: dict[str, Any] | None = _error(
                    None, INVALID_REQUEST, f"сообщение больше {MAX_BODY // 2**20} МиБ: не читается"
                )
            elif not line.strip():
                continue
            else:
                reply = session.receive(line)
            if reply is not None:
                stdout.write(_encode(reply))
                stdout.flush()
    return 0
