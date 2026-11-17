"""Состояния кейса на разборе. Перенесено по образцу AeroBIM (``review_state_machine``).

Сотрудник может принять или отклонить кейс только с основанием; решение
сотрудника не переписывает вердикт движка, а ложится рядом с ним в журнал.
Недопустимый переход — исключение, а не тихое игнорирование.

Автоматический вердикт (``auto_decided``) клиент может оспорить (``appeal``): жалобу
подаёт оператор от имени клиента, кейс уходит человеку. Так реализовано право на пересмотр решения
человеком из Кодекса этики в сфере ИИ на финансовом рынке (Банк России, 2025).

Решение сотрудника (``accept``/``reject``, в том числе после ``close``) тоже можно обжаловать: кейс снова
уходит в очередь, а решавшие его сотрудники и подавший жалобу решать его уже не могут («своё не решать»).
``require`` задаёт специализацию, без которой кейс не решить; снять или заменить её нельзя.

Роли и правила переходов (M-09) описаны в docs/REVIEW.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from mandat.audit import journal
from mandat.services.env_policy import protected


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
    # Жалоба на решение человека (M-09).
    (ReviewState.ACCEPTED, "appeal"): ReviewState.QUEUED,
    (ReviewState.REJECTED, "appeal"): ReviewState.QUEUED,
    (ReviewState.CLOSED, "appeal"): ReviewState.QUEUED,
    # Требование специализации: состояние не меняется, версия растёт, запись в журнале.
    (ReviewState.QUEUED, "require"): ReviewState.QUEUED,
    (ReviewState.IN_REVIEW, "require"): ReviewState.IN_REVIEW,
    (ReviewState.DOCS_REQUESTED, "require"): ReviewState.DOCS_REQUESTED,
}
NEEDS_REASON = {"accept", "reject", "request_docs", "appeal", "require"}
JOURNAL_ACTION = {
    "appeal": "review_appeal",
    "open": "review_open",
    "accept": "review_accept",
    "reject": "review_reject",
    "request_docs": "review_request_docs",
    "close": "review_close",
    "require": "review_require",
}
REVIEW_ACTIONS = set(JOURNAL_ACTION.values())
# Специализации роли reviewer. Прежние имена ролей разбора — это они же (совместимость вызовов).
SPECIALIZATIONS = ("lawyer", "compliance", "security", "client_service")
# Роли, которые действуют в разборе. auditor и admin кейсы не двигают.
ACTING_ROLES = {"operator", "reviewer"}
ROLES = ACTING_ROLES | set(SPECIALIZATIONS)
NON_ACTING_ROLES = {
    "auditor": "только чтение",
    "admin": "управляет пользователями и настройками, кейсы не решает",
}
# Кто может выполнить действие.
ALLOWED_ROLES: dict[str, frozenset[str]] = {
    "appeal": frozenset({"operator"}),
    "open": frozenset({"operator", "reviewer"}),
    "request_docs": frozenset({"reviewer"}),
    "accept": frozenset({"reviewer"}),
    "reject": frozenset({"reviewer"}),
    "close": frozenset({"operator", "reviewer"}),
    "require": frozenset({"operator", "reviewer"}),
}
# Действия-решения: к ним применяется запрет решать «свой» кейс и требование специализации.
DECISIONS = {"request_docs", "accept", "reject"}
# Состояния после решения человека: жалоба из них — жалоба на решение сотрудника.
MANUAL_OUTCOMES = {ReviewState.ACCEPTED.value, ReviewState.REJECTED.value, ReviewState.CLOSED.value}
ON_BEHALF_OF = "client"


def resolve_role(role: str, specialization: str | None = None) -> tuple[str, str | None]:
    """Роль и специализация. ``lawyer`` и другие прежние имена — reviewer с этой специализацией."""
    if role in NON_ACTING_ROLES:
        raise ReviewForbidden(f"роль {role!r} не двигает кейсы: {NON_ACTING_ROLES[role]}")
    if role in SPECIALIZATIONS:
        if specialization not in (None, role):
            raise ReviewForbidden(f"роль {role!r} и специализация {specialization!r} не совпадают")
        return "reviewer", role
    if role not in ACTING_ROLES:
        raise ReviewForbidden(f"роль {role!r} не может разбирать кейсы")
    if specialization is not None:
        if role != "reviewer":
            raise ReviewForbidden("специализация есть только у роли reviewer")
        if specialization not in SPECIALIZATIONS:
            raise ReviewForbidden(f"специализация {specialization!r} не из {list(SPECIALIZATIONS)}")
    return role, specialization


class ReviewError(ValueError):
    """Отказ в действии разбора. Подклассы различают причину для ответа API."""


class ReviewForbidden(ReviewError):
    """Роль, специализация или независимость решения не позволяют действие (403)."""


class ReviewConflict(ReviewError):
    """Действие недопустимо из текущего состояния или версия устарела (409)."""


class ReviewInvalid(ReviewError):
    """Неполный запрос: нет основания, версии или журнала (422)."""


@dataclass
class ReviewCase:
    case_id: str
    report_hash: str
    state: ReviewState = ReviewState.QUEUED
    history: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0
    # Кто создал кейс и кто принял обжалуемое решение: им решать этот кейс нельзя.
    created_by: str | None = None
    appealed_decision_by: str | None = None
    # Специализация, без которой кейс не решить (например, lawyer для правового вопроса).
    required_specialization: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Состояние для хранилища (``Store.review_put``)."""
        return {
            "case_id": self.case_id,
            "report_hash": self.report_hash,
            "state": self.state.value,
            "history": list(self.history),
            "version": self.version,
            "created_by": self.created_by,
            "appealed_decision_by": self.appealed_decision_by,
            "required_specialization": self.required_specialization,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewCase:
        return cls(
            case_id=str(data["case_id"]),
            report_hash=str(data["report_hash"]),
            state=ReviewState(data["state"]),
            history=list(data.get("history") or []),
            version=int(data.get("version") or 0),
            created_by=data.get("created_by"),
            appealed_decision_by=data.get("appealed_decision_by"),
            required_specialization=data.get("required_specialization"),
        )

    def conflicted_actors(self) -> set[str]:
        """Кто не может решать кейс: автор, подавшие appeal, автор обжалуемого решения. open не в счёт.

        После жалобы на решение человека решать не могут и все, кто уже принимал решение по кейсу
        (``accept``/``reject``/``request_docs``): жалобу разбирает другой сотрудник.
        """
        actors = {a for a in (self.created_by, self.appealed_decision_by) if a}
        actors |= {h["actor"] for h in self.history if h.get("action") == "appeal"}
        if any(h.get("action") == "appeal" and h.get("from") in MANUAL_OUTCOMES for h in self.history):
            actors |= {h["actor"] for h in self.history if h.get("action") in DECISIONS}
        return actors

    def apply(
        self,
        action: str,
        *,
        actor: str,
        role: str,
        reason: str = "",
        journal_path: Path | None = None,
        expected_version: int | None = None,
        specialization: str | None = None,
        required_specialization: str | None = None,
    ) -> ReviewState:
        role, specialization = resolve_role(role, specialization)
        if action == "require":
            if required_specialization not in SPECIALIZATIONS:
                raise ReviewInvalid(f"нужна специализация из {list(SPECIALIZATIONS)}")
            if self.required_specialization not in (None, required_specialization):
                raise ReviewConflict(
                    f"специализация уже задана: {self.required_specialization!r}; снять или заменить её нельзя"
                )
        elif required_specialization is not None:
            raise ReviewInvalid("специализация задаётся только действием require")
        if action in ALLOWED_ROLES and role not in ALLOWED_ROLES[action]:
            raise ReviewForbidden(f"действие {action!r} не для роли {role!r}")
        if action in DECISIONS:
            if actor in self.conflicted_actors():
                raise ReviewForbidden(
                    "решать кейс нельзя: вы создали его, подали жалобу или приняли обжалуемое решение"
                )
            if self.required_specialization and specialization != self.required_specialization:
                raise ReviewForbidden(f"для решения нужна специализация {self.required_specialization!r}")
        if protected():
            # В защищённом режиме решение без версии и без журнала не принимается: гонка и потеря аудита.
            if expected_version is None:
                raise ReviewInvalid("в защищённом режиме нужна версия кейса (expected_version)")
            if journal_path is None:
                raise ReviewInvalid("в защищённом режиме решение пишется в журнал: передайте journal_path")
        if expected_version is not None and expected_version != self.version:
            raise ReviewConflict(f"версия {expected_version} устарела, сейчас {self.version}")
        target = TRANSITIONS.get((self.state, action))
        if target is None:
            raise ReviewConflict(f"переход {action!r} из состояния {self.state.value!r} недопустим")
        if action in NEEDS_REASON and not reason.strip():
            raise ReviewInvalid(f"для действия {action!r} нужно основание")
        decision = {
            "from": self.state.value,
            "to": target.value,
            "action": action,
            "actor": actor,
            "role": role,
            "specialization": specialization,
            "on_behalf_of": ON_BEHALF_OF if action == "appeal" else None,
            "reason": reason,
            "version": self.version,
            "engine_report_hash": self.report_hash,
        }
        if action == "require":
            decision["required_specialization"] = required_specialization
        if journal_path is not None:
            self._journal(
                journal_path,
                action,
                actor=actor,
                role=role,
                reason=reason,
                target=target,
                specialization=specialization,
                required_specialization=required_specialization,
            )
        if action == "appeal" and self.state.value in MANUAL_OUTCOMES:
            # Автор обжалуемого решения — последний принявший accept/reject.
            last = next((h for h in reversed(self.history) if h.get("action") in {"accept", "reject"}), None)
            self.appealed_decision_by = last["actor"] if last else self.appealed_decision_by
        if action == "require":
            self.required_specialization = required_specialization
        self.history.append(decision)
        self.state = target
        self.version += 1
        return target

    def _journal(
        self,
        path: Path,
        action: str,
        *,
        actor: str,
        role: str,
        reason: str,
        target: ReviewState,
        specialization: str | None = None,
        required_specialization: str | None = None,
    ) -> None:
        """Проверка «по версии ещё нет решения» и запись делаются под одной блокировкой журнала."""
        version = self.version
        report_hash = self.report_hash

        def no_other_decision(records: list[dict[str, Any]]) -> dict[str, Any] | None:
            existing = _decision_for(records, self.case_id, version)
            if existing is None:
                return None
            if existing.get("action") != JOURNAL_ACTION[action]:
                raise ReviewConflict(f"конфликт: версия {version} уже записана с решением {existing.get('action')}")
            if existing.get("payload_hash") != report_hash or existing.get("reason") != reason:
                raise ReviewConflict("конфликт: эта версия уже записана с другим решением")
            if (existing.get("details") or {}).get("actor") not in {None, actor}:
                raise ReviewConflict("конфликт: эту версию записал другой сотрудник")
            return existing  # повтор того же решения: новой записи нет

        try:
            journal.append(
                path,
                action=JOURNAL_ACTION[action],
                actor=actor,
                role=role,
                case_id=self.case_id,
                payload_hash=report_hash,
                reason=reason,
                details=_details(target, version, actor, action, specialization, required_specialization),
                precondition=no_other_decision,
            )
        except journal.JournalError as exc:
            raise ReviewError(f"журнал не цел, решение не принято: {exc}") from exc


def _details(
    target: ReviewState,
    version: int,
    actor: str,
    action: str,
    specialization: str | None,
    required_specialization: str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {"state": target.value, "version": version, "actor": actor}
    if required_specialization:
        details["required_specialization"] = required_specialization
    if specialization:
        details["specialization"] = specialization
    if action == "appeal":
        details["on_behalf_of"] = ON_BEHALF_OF
    return details


def _decision_for(records: list[dict[str, Any]], case_id: str, version: int) -> dict[str, Any] | None:
    """Решение по этой версии кейса, какое бы оно ни было: одна версия — одно решение."""
    for record in records:
        details = record.get("details") or {}
        same = record.get("case_id") == case_id and record.get("action") in REVIEW_ACTIONS
        if same and details.get("version") == version:
            return record
    return None
