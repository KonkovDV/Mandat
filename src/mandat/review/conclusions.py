"""Реестр заключений юриста: черновик, подтверждение автором, утверждение вторым юристом, отзыв, перепроверка.

Порядок «четыре глаза» (maker-checker): черновик записывает один сотрудник, утверждает другой. Черновик пишет
``operator`` или ``reviewer``; проект по кейсу, который готовит модель банка, — только юрист (``reviewer`` со
специализацией ``lawyer``), и такой проект автор сначала подтверждает или исправляет. Утверждает только юрист и
только не автор. Отзывает юрист. Утверждённое заключение уходит на перепроверку, когда в кейсе клиента приходит
другая редакция устава: старый вывод больше не применяется, а новый разбор начинается с него.

Каждый шаг пишется в журнал аудита до записи в хранилище, как решения разбора (``review/state_machine.py``):
одна версия заключения — одно действие, повтор того же действия новой записи не даёт. В записи — отпечаток вывода
(``payload_hash``), а основание и причина — свободный текст сотрудника — отпечатком ``journal.text_digest``.
Функции возвращают новое тело записи; в хранилище его кладёт вызывающий по ожидаемой версии.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from mandat.audit import journal
from mandat.domain.conclusion import (
    EDITABLE_FIELDS,
    MAX_QUOTE_CHARS,
    MAX_RESTRICTIONS,
    STATUS_RU,
    checked_content,
    content_sha256,
    msk_day,
    ogrn_ok,
)
from mandat.services.env_policy import protected

AUTHOR_ROLES = frozenset({"operator", "reviewer"})
LAWYER = "lawyer"
JOURNAL_ACTION = {
    "create": "conclusion_create",
    "confirm": "conclusion_confirm",
    "approve": "conclusion_approve",
    "revoke": "conclusion_revoke",
    "recheck": "conclusion_recheck",
}
LIFECYCLE_ACTIONS = frozenset(JOURNAL_ACTION.values())
APPLY_ACTION = "conclusion_apply"
# Срок черновика из кейса по умолчанию: год, юрист меняет его при подтверждении.
DEFAULT_TERM_DAYS = 365
RECHECK_REASON = "в кейсе клиента другая редакция устава"


class ConclusionError(ValueError):
    """Отказ в действии с заключением. Подклассы различают причину для ответа API."""


class ConclusionForbidden(ConclusionError):
    """Роль, специализация или независимость утверждения не позволяют действие (403)."""


class ConclusionConflict(ConclusionError):
    """Действие недопустимо в текущем состоянии или версия устарела (409)."""


class ConclusionInvalid(ConclusionError):
    """Неполный или неверный запрос: поля вывода, основание, журнал в защищённом режиме (422)."""


@dataclass(frozen=True)
class Actor:
    name: str
    role: str
    specialization: str | None = None

    @property
    def lawyer(self) -> bool:
        return self.role == "reviewer" and self.specialization == LAWYER


def new_id() -> str:
    return "cnc-" + secrets.token_hex(6)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _require_lawyer(actor: Actor, what: str) -> None:
    if not actor.lawyer:
        raise ConclusionForbidden(f"{what} только юрист: роль reviewer со специализацией lawyer")


def _check_journal(journal_path: Path | None) -> None:
    if journal_path is None and protected():
        raise ConclusionInvalid("в защищённом режиме заключение без журнала аудита не записывается")


def _check_version(version: int, expected_version: int) -> None:
    if expected_version != version:
        raise ConclusionConflict(f"версия {expected_version} устарела, сейчас {version}")


def _history(
    body: Mapping[str, Any], action: str, actor: Actor, moment: datetime, version: int, reason: str = ""
) -> list[dict[str, Any]]:
    """История записи для показа юристу: кто, что и когда сделал. Текст причины живёт здесь, а не в журнале."""
    entry: dict[str, Any] = {"action": action, "actor": actor.name, "role": actor.role, "at": _iso(moment)}
    entry["version"] = version
    if reason:
        entry["reason"] = reason
    return [*(body.get("history") or []), entry]


def _append(
    journal_path: Path | None,
    action: str,
    body: Mapping[str, Any],
    *,
    version: int,
    actor: Actor,
    reason: str,
    details: dict[str, Any],
) -> None:
    """Запись шага в журнал под его блокировкой: эта версия заключения ещё не записана другим действием."""
    if journal_path is None:
        return
    name = JOURNAL_ACTION[action]
    cid = str(body["id"])
    payload = content_sha256(body)
    digest = journal.text_digest(reason)

    def once(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        for record in records:
            info = record.get("details") or {}
            if record.get("case_id") != cid or record.get("action") not in LIFECYCLE_ACTIONS:
                continue
            if info.get("version") != version:
                continue
            same = record.get("action") == name and record.get("payload_hash") == payload
            if same and record.get("reason") == digest and info.get("actor") == actor.name:
                return record  # повтор того же действия: новой записи нет
            raise ConclusionConflict(f"версия {version} заключения уже записана в журнал: {record.get('action')}")
        return None

    try:
        journal.append(
            journal_path,
            action=name,
            actor=actor.name,
            role=actor.role,
            case_id=cid,
            payload_hash=payload,
            reason=digest,
            details={"version": version, "actor": actor.name, **details},
            precondition=once,
        )
    except journal.JournalError as exc:
        raise ConclusionError(f"журнал не цел, действие с заключением не записано: {exc}") from exc


def _kinds(body: Mapping[str, Any]) -> list[str]:
    return [str(r.get("kind")) for r in body.get("restrictions") or [] if isinstance(r, Mapping)]


def create(
    fields: Mapping[str, Any],
    *,
    actor: Actor,
    conclusion_id: str,
    now: datetime,
    operations: Collection[str],
    journal_path: Path | None,
) -> dict[str, Any]:
    """Черновик, который автор написал сам: вывод полный и уже подтверждён автором, ждёт второго юриста."""
    if actor.role not in AUTHOR_ROLES:
        raise ConclusionForbidden(f"роль {actor.role} заключения не записывает: черновик — operator или reviewer")
    _check_journal(journal_path)
    try:
        content = checked_content(fields, operations=operations, today=msk_day(now))
    except ValueError as exc:
        raise ConclusionInvalid(str(exc)) from exc
    body: dict[str, Any] = {
        "id": conclusion_id,
        **content,
        "status": "draft",
        "origin": "manual",
        "confirmed": True,
        "author": actor.name,
        "author_role": actor.role,
        "created_at": _iso(now),
        "approver": None,
        "approved_at": None,
    }
    body["history"] = _history(body, "create", actor, now, 0)
    details = {
        "state": "draft",
        "origin": "manual",
        "ogrn": content["ogrn"],
        "charter_sha256": content["charter_sha256"],
        "outcome": content["outcome"],
        "restrictions": _kinds(content),
        "operations": content["operations"],
        "max_amount_rub": content["max_amount_rub"],
        "valid_until": content["valid_until"],
    }
    _append(journal_path, "create", body, version=0, actor=actor, reason=content["basis"], details=details)
    return body


def _proposal_restrictions(proposals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ограничения проекта из предложений модели: вид, сумма и цитата. Неполное поправит автор при подтверждении."""
    out = []
    for item in proposals[:MAX_RESTRICTIONS]:
        quote = str(item.get("quote") or "")[:MAX_QUOTE_CHARS] or None
        value = item.get("value_rub")
        out.append({"kind": item.get("kind"), "value_rub": value, "inclusive": True, "quote": quote})
    return out


def draft_from_case(
    *,
    ogrn: str,
    charter_sha256: str,
    operation: str,
    source_case: str,
    proposals: Sequence[Mapping[str, Any]],
    origin: str,
    note: str,
    previous: Sequence[Mapping[str, Any]],
    actor: Actor,
    conclusion_id: str,
    now: datetime,
    journal_path: Path | None,
) -> dict[str, Any]:
    """Проект заключения по кейсу, ушедшему на разбор из-за устава.

    ``proposals`` — факты устава, которые предложила модель и пропустил шлюз цитаты (``advisory/draft.py``); без
    модели их нет, и проект пуст. ``previous`` — заключения по прежней редакции устава: новый разбор начинается с
    них. Проект не действует, пока автор его не подтвердит, а второй юрист не утвердит.
    """
    _require_lawyer(actor, "проект заключения по кейсу готовит")
    _check_journal(journal_path)
    if not ogrn_ok(ogrn):
        raise ConclusionInvalid("у организации кейса нет верного ОГРН юрлица: заключение не к чему привязать")
    restrictions = _proposal_restrictions(proposals)
    quotes = "; ".join(f"«{r['quote']}»" for r in restrictions if r["quote"])
    body: dict[str, Any] = {
        "id": conclusion_id,
        "ogrn": ogrn,
        "charter_sha256": charter_sha256,
        "outcome": "restriction" if restrictions else None,
        "restrictions": restrictions,
        "basis": f"Проект модели по уставу: {quotes}" if quotes else "",
        "operations": [operation] if operation else [],
        "max_amount_rub": None,
        "valid_until": (msk_day(now) + timedelta(days=DEFAULT_TERM_DAYS)).isoformat(),
        "status": "draft",
        "origin": origin,
        "confirmed": False,
        "source_case": source_case,
        "proposals": [dict(p) for p in proposals],
        "model_note": note,
        "supersedes": [str(p.get("id")) for p in previous if p.get("id")],
        "previous": [dict(p) for p in previous],
        "author": actor.name,
        "author_role": actor.role,
        "created_at": _iso(now),
        "approver": None,
        "approved_at": None,
    }
    body["history"] = _history(body, "create", actor, now, 0)
    details = {
        "state": "draft",
        "origin": origin,
        "ogrn": ogrn,
        "charter_sha256": charter_sha256,
        "source_case": source_case,
        "proposals": len(proposals),
        "supersedes": body["supersedes"],
    }
    _append(journal_path, "create", body, version=0, actor=actor, reason=body["basis"], details=details)
    return body


def confirm(
    body: Mapping[str, Any],
    version: int,
    *,
    fields: Mapping[str, Any],
    actor: Actor,
    now: datetime,
    operations: Collection[str],
    journal_path: Path | None,
    expected_version: int,
) -> dict[str, Any]:
    """Автор подтверждает черновик или исправляет его поля вывода. Без этого шага проект из кейса не утвердить."""
    _check_version(version, expected_version)
    if body.get("status") != "draft":
        raise ConclusionConflict(f"подтвердить можно только черновик, сейчас: {STATUS_RU.get(str(body.get('status')))}")
    if actor.name != body.get("author"):
        raise ConclusionForbidden("подтверждает и исправляет черновик его автор")
    _check_journal(journal_path)
    merged = {name: fields[name] if name in fields else body.get(name) for name in EDITABLE_FIELDS}
    merged |= {"ogrn": body.get("ogrn"), "charter_sha256": body.get("charter_sha256")}
    try:
        content = checked_content(merged, operations=operations, today=msk_day(now))
    except ValueError as exc:
        raise ConclusionInvalid(str(exc)) from exc
    new = {**body, **content, "confirmed": True, "confirmed_at": _iso(now)}
    new["history"] = _history(body, "confirm", actor, now, version)
    details = {"state": "draft", "confirmed": True, "outcome": content["outcome"], "restrictions": _kinds(content)}
    _append(journal_path, "confirm", new, version=version, actor=actor, reason=content["basis"], details=details)
    return new


def approve(
    body: Mapping[str, Any],
    version: int,
    *,
    actor: Actor,
    reason: str,
    now: datetime,
    operations: Collection[str],
    journal_path: Path | None,
    expected_version: int,
) -> dict[str, Any]:
    """Второй юрист утверждает подтверждённый черновик. Автор своё заключение не утверждает."""
    _check_version(version, expected_version)
    _require_lawyer(actor, "утверждает заключение")
    if actor.name == body.get("author"):
        raise ConclusionForbidden("автор не утверждает своё заключение: нужен второй юрист")
    if body.get("status") != "draft":
        raise ConclusionConflict(f"утвердить можно только черновик, сейчас: {STATUS_RU.get(str(body.get('status')))}")
    if body.get("confirmed") is not True:
        raise ConclusionConflict("автор ещё не подтвердил проект: сначала confirm")
    _check_journal(journal_path)
    try:
        checked_content(body, operations=operations, today=msk_day(now))
    except ValueError as exc:
        raise ConclusionConflict(f"черновик не утвердить: {exc}") from exc
    new = {**body, "status": "approved", "approver": actor.name, "approved_at": _iso(now)}
    new["history"] = _history(body, "approve", actor, now, version, reason.strip())
    details = {
        "state": "approved",
        "author": body.get("author"),
        "specialization": actor.specialization,
        "ogrn": body.get("ogrn"),
        "charter_sha256": body.get("charter_sha256"),
        "outcome": body.get("outcome"),
        "valid_until": body.get("valid_until"),
    }
    _append(journal_path, "approve", new, version=version, actor=actor, reason=reason.strip(), details=details)
    return new


def revoke(
    body: Mapping[str, Any],
    version: int,
    *,
    actor: Actor,
    reason: str,
    now: datetime,
    journal_path: Path | None,
    expected_version: int,
) -> dict[str, Any]:
    """Юрист отзывает черновик, утверждённое заключение или заключение на перепроверке. Нужна причина."""
    _check_version(version, expected_version)
    _require_lawyer(actor, "отзывает заключение")
    status = str(body.get("status"))
    if status not in {"draft", "approved", "recheck"}:
        raise ConclusionConflict(f"отозвать нельзя: заключение {STATUS_RU.get(status, status)}")
    if not reason.strip():
        raise ConclusionInvalid("для отзыва нужна причина")
    _check_journal(journal_path)
    new = {**body, "status": "revoked", "revoked_by": actor.name, "revoked_at": _iso(now)}
    new["history"] = _history(body, "revoke", actor, now, version, reason.strip())
    details = {"state": "revoked", "from": status, "specialization": actor.specialization}
    _append(journal_path, "revoke", new, version=version, actor=actor, reason=reason.strip(), details=details)
    return new


def recheck(
    body: Mapping[str, Any],
    version: int,
    *,
    actor: Actor,
    case_id: str,
    charter_sha256: str,
    now: datetime,
    journal_path: Path | None,
) -> dict[str, Any]:
    """Утверждённое заключение — на перепроверку: в кейсе ``case_id`` устав другой редакции.

    Делает это система от имени того, кто проверял кейс. Заключение на перепроверке не применяется ни к старой,
    ни к новой редакции; новый вывод юрист записывает отдельным заключением.
    """
    if body.get("status") != "approved":
        raise ConclusionConflict("на перепроверку уходит только утверждённое заключение")
    _check_journal(journal_path)
    new = {
        **body,
        "status": "recheck",
        "recheck_at": _iso(now),
        "recheck_case": case_id,
        "recheck_charter_sha256": charter_sha256,
    }
    new["history"] = _history(body, "recheck", actor, now, version, RECHECK_REASON)
    details = {"state": "recheck", "case": case_id, "charter_sha256": charter_sha256}
    _append(journal_path, "recheck", new, version=version, actor=actor, reason=RECHECK_REASON, details=details)
    return new


def record_apply(
    journal_path: Path,
    *,
    actor: Actor,
    case_id: str,
    report_hash: str,
    applied: Mapping[str, Any],
    attempt: str | None = None,
    via: str = "api",
) -> None:
    """Применение заключения в проверке кейса: какое заключение, какой версии и с каким отпечатком вывода."""
    details: dict[str, Any] = {
        "conclusion": applied.get("id"),
        "conclusion_version": applied.get("version"),
        "conclusion_sha256": applied.get("content_sha256"),
        "outcome": applied.get("outcome"),
        "via": via,
    }
    if attempt is not None:
        details["attempt"] = attempt
    journal.append(
        journal_path,
        action=APPLY_ACTION,
        actor=actor.name,
        role=actor.role,
        case_id=case_id,
        payload_hash=report_hash,
        details=details,
    )


def effective_status(body: Mapping[str, Any], day: date) -> str:
    """Статус для показа: утверждённое после ``valid_until`` — истёкшее."""
    status = str(body.get("status"))
    until = body.get("valid_until")
    if status == "approved" and isinstance(until, str) and until < day.isoformat():
        return "expired"
    return status
