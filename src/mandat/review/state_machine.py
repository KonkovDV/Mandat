"""Состояния кейса на разборе. Перенесено по образцу AeroBIM (``review_state_machine``).

Сотрудник может принять или отклонить кейс только с основанием; решение
сотрудника не переписывает вердикт движка, а ложится рядом с ним в журнал.
Недопустимый переход — исключение, а не тихое игнорирование.

Автоматический вердикт (``auto_decided``) клиент или сотрудник может оспорить
(``appeal``): кейс уходит человеку. Так реализовано право на пересмотр решения
человеком из Кодекса этики в сфере ИИ на финансовом рынке (Банк России, 2025).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from mandat.audit import journal


class ReviewState(StrEnum):
    AUTO_DECIDED = "auto_decided"
    QUEUED = "queued"
    IN_REVIEW = "in_review"
    DOCS_REQUESTED = "docs_requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CLOSED = "closed"


TRANSITIONS: dict[tuple[ReviewState, str], ReviewState] = {
    (ReviewState.AUTO_DECIDED, "appeal"): ReviewState.QUEUED,
    (ReviewState.QUEUED, "open"): ReviewState.IN_REVIEW,
    (ReviewState.IN_REVIEW, "request_docs"): ReviewState.DOCS_REQUESTED,
    (ReviewState.DOCS_REQUESTED, "open"): ReviewState.IN_REVIEW,
    (ReviewState.IN_REVIEW, "accept"): ReviewState.ACCEPTED,
    (ReviewState.IN_REVIEW, "reject"): ReviewState.REJECTED,
    (ReviewState.ACCEPTED, "close"): ReviewState.CLOSED,
    (ReviewState.REJECTED, "close"): ReviewState.CLOSED,
}
NEEDS_REASON = {"accept", "reject", "request_docs", "appeal"}
JOURNAL_ACTION = {
    "appeal": "review_appeal",
    "open": "review_open",
    "accept": "review_accept",
    "reject": "review_reject",
    "request_docs": "review_request_docs",
    "close": "review_close",
}
ROLES = {"operator", "lawyer", "compliance", "security", "client_service"}


class ReviewError(ValueError):
    pass


@dataclass
class ReviewCase:
    case_id: str
    report_hash: str
    state: ReviewState = ReviewState.QUEUED
    history: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0

    def apply(
        self,
        action: str,
        *,
        actor: str,
        role: str,
        reason: str = "",
        journal_path: Path | None = None,
        expected_version: int | None = None,
    ) -> ReviewState:
        if role not in ROLES:
            raise ReviewError(f"роль {role!r} не может разбирать кейсы")
        if expected_version is not None and expected_version != self.version:
            raise ReviewError(f"версия {expected_version} устарела, сейчас {self.version}")
        target = TRANSITIONS.get((self.state, action))
        if target is None:
            raise ReviewError(f"переход {action!r} из состояния {self.state.value!r} недопустим")
        if action in NEEDS_REASON and not reason.strip():
            raise ReviewError(f"для действия {action!r} нужно основание")
        decision = {
            "from": self.state.value,
            "to": target.value,
            "action": action,
            "actor": actor,
            "role": role,
            "reason": reason,
            "version": self.version,
            "engine_report_hash": self.report_hash,
        }
        if journal_path is not None:
            existing = _committed(journal_path, self.case_id, self.version, action)
            if existing is None:
                journal.append(
                    journal_path,
                    action=JOURNAL_ACTION[action],
                    actor=actor,
                    role=role,
                    case_id=self.case_id,
                    payload_hash=self.report_hash,
                    reason=reason,
                    details={"state": target.value, "version": self.version, "actor": actor},
                )
            elif existing.get("payload_hash") != self.report_hash or existing.get("reason") != reason:
                raise ReviewError("конфликт: эта версия уже записана с другим решением")
            elif (existing.get("details") or {}).get("actor") not in {None, actor}:
                raise ReviewError("конфликт: эту версию записал другой сотрудник")
        self.history.append(decision)
        self.state = target
        self.version += 1
        return target


def _committed(path: Path, case_id: str, version: int, action: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    check = journal.verify_journal(path)
    if not check.ok:
        raise ReviewError(f"журнал не цел, решение не принято: {check.reason}")
    for record in journal.read_journal(path):
        details = record.get("details") or {}
        same = record.get("case_id") == case_id and record.get("action") == JOURNAL_ACTION[action]
        if same and details.get("version") == version:
            return record
    return None
