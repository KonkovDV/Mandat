"""Разбор кейса через API (M-09c): начальное состояние, очередь, сроки.

Состояние разбора лежит в хранилище (``Store.review_get``/``review_put``) отдельно от отчёта. Версия разбора —
версия записи в хранилище: клиент передаёт её в ``expected_version``, устаревшая версия даёт 409.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mandat.review.state_machine import ReviewCase, ReviewState
from mandat.services.next_steps import sla_hours

# Состояния, в которых кейс ждёт человека и попадает в очередь.
OPEN_STATES = {ReviewState.QUEUED, ReviewState.IN_REVIEW, ReviewState.DOCS_REQUESTED}
# Приоритет обжалованного автоматического решения: у самого отчёта приоритета нет (docs/REVIEW.md).
APPEAL_PRIORITY = "P2"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def initial(case_id: str, report: dict[str, Any], owner: str) -> dict[str, Any]:
    """Состояние разбора до первого действия: подтверждённый вердикт — автоматическое решение, иначе очередь."""
    auto = (report.get("routing") or {}).get("queue") == "auto_recommendation"
    case = ReviewCase(
        case_id=case_id,
        report_hash=str(report.get("reproducibility_hash", "")),
        state=ReviewState.AUTO_DECIDED if auto else ReviewState.QUEUED,
        created_by=owner,
    )
    body = case.to_dict()
    body["queued_at"] = None if auto else report.get("generated_at") or now_iso()
    body["priority"] = (report.get("routing") or {}).get("priority")
    return body


def load(body: dict[str, Any], version: int) -> ReviewCase:
    case = ReviewCase.from_dict(body)
    case.version = version
    return case


def dump(case: ReviewCase, previous: dict[str, Any]) -> dict[str, Any]:
    """Новое состояние для хранилища с полями очереди (когда встал в очередь, приоритет)."""
    body = case.to_dict()
    body["priority"] = previous.get("priority")
    body["queued_at"] = previous.get("queued_at")
    if case.state is ReviewState.QUEUED and previous.get("state") != ReviewState.QUEUED.value:
        body["queued_at"] = now_iso()
        body["priority"] = body["priority"] or APPEAL_PRIORITY
    return body


def view(case_id: str, body: dict[str, Any], version: int) -> dict[str, Any]:
    """Ответ API: состояние, версия для expected_version, срок."""
    priority = body.get("priority")
    hours = sla_hours(priority) if priority and body.get("state") in {s.value for s in OPEN_STATES} else None
    due = None
    if hours is not None and body.get("queued_at"):
        due = (datetime.fromisoformat(body["queued_at"]) + timedelta(hours=hours)).isoformat(timespec="seconds")
    return {
        "case_id": case_id,
        "state": body.get("state"),
        "version": version,
        "priority": priority,
        "sla_hours": hours,
        "queued_at": body.get("queued_at"),
        "due_at": due,
        "required_specialization": body.get("required_specialization"),
        "history": body.get("history") or [],
    }
