"""Как правила устава применяют реестр заключений юриста (R-CHR-002, R-CHR-001).

Заключение применяется в строгом профиле, только если оно утверждено вторым юристом, не отозвано, не на
перепроверке, не истекло на день проверки и на день операции, сделано по той же редакции устава (sha256 документа
в кейсе) для того же ОГРН и его область покрывает операцию: код из списка заключения и сумма не выше предела.
Применённое заключение называет находка R-CHR-002: номер, кто и когда утвердил, основание, срок. Ограничения
заключения становятся фактами устава, и R-CHR-001 проверяет их так же, как найденные в тексте: порог, совместную
подпись, одобрение крупной сделки. Вывод «ограничений нет» не гасит ограничение другого действующего заключения:
при расхождении применяются ограничения, а расхождение уходит на разбор.

Заключение, которое не подошло, не применяется, и причина стоит в отчёте. Кейс тогда идёт тем же путём, что и без
реестра. Заключения той же организации по другой редакции устава попадают в ``previous``: API ставит их на
перепроверку, а разбор показывает юристу, с какого заключения начать.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from mandat.domain.conclusion import MSK, Conclusion, Restriction, msk_day, rub
from mandat.domain.model import Case, EvidenceRef, ExtractionMethod
from mandat.intake.charter import CandidateFact

# Сколько неприменённых заключений называть находками; в разделе отчёта — до MAX_LISTED.
MAX_NOTES = 3
MAX_LISTED = 10
MAX_PREVIOUS = 3
# Утверждение «позже момента проверки» с допуском на расхождение часов, как у снимков источников.
CLOCK_SKEW = timedelta(minutes=5)
_LIVE = {"approved", "recheck"}


def check_day(case: Case, now: datetime | None) -> date:
    """День проверки по Москве; без момента проверки — день операции (синтетика живёт в своих датах)."""
    return msk_day(now) if now is not None else case.operation.on_date


def _day(moment: datetime | None) -> str:
    return f"{moment.astimezone(MSK):%d.%m.%Y}" if moment is not None else "—"


def _why_not(item: Conclusion, case: Case, day: date, now: datetime | None) -> str | None:
    """Почему заключение не применяется к кейсу. ``None`` — применяется."""
    charter = case.charter
    if charter is None:
        return "устава в кейсе нет: редакцию, по которой сделано заключение, не с чем сверить"
    if item.charter_sha256 != charter.sha256:
        return (
            f"заключение сделано по другой редакции устава (sha256 {item.charter_sha256[:12]}…), "
            f"а в кейсе устав sha256 {charter.sha256[:12]}…: нужна перепроверка юристом"
        )
    if item.status == "draft":
        missing = "" if item.confirmed else ", автор ещё не подтвердил проект"
        return f"черновик: второй юрист его ещё не утвердил{missing}"
    if item.status == "revoked":
        return f"заключение отозвано {_day(item.revoked_at)}"
    if item.status == "recheck":
        return f"заключение на перепроверке с {_day(item.recheck_at)}: у клиента появилась другая редакция устава"
    operation = case.operation
    if day > item.valid_until or operation.on_date > item.valid_until:
        return f"срок заключения истёк {item.valid_until:%d.%m.%Y}"
    if now is not None and item.approved_at is not None and item.approved_at > now + CLOCK_SKEW:
        return "заключение утверждено позже момента проверки"
    if item.operations and operation.code not in item.operations:
        return f"операция {operation.code} вне области заключения ({', '.join(item.operations)})"
    if item.max_amount_rub is not None:
        if operation.amount_rub is None:
            return f"сумма операции не указана, а заключение действует для сумм до {rub(item.max_amount_rub)}"
        if operation.amount_rub > item.max_amount_rub:
            return (
                f"сумма операции {rub(operation.amount_rub)} выше предела заключения {rub(item.max_amount_rub)}: "
                "крупную операцию юрист смотрит отдельно"
            )
    return None


def _recent(item: Conclusion) -> datetime:
    return item.approved_at or item.created_at


@dataclass(frozen=True)
class Match:
    """Что реестр дал кейсу: применённые заключения, неприменённые с причиной и заключения прежней редакции."""

    applied: tuple[Conclusion, ...]
    skipped: tuple[tuple[Conclusion, str], ...]
    conflict: tuple[tuple[Conclusion, str], ...]
    previous: tuple[Conclusion, ...]

    def section(self) -> dict[str, Any]:
        """Раздел отчёта ``charter_conclusions``: по нему API пишет журнал и ставит прежние на перепроверку."""
        return {
            "applied": [
                {
                    "id": c.id,
                    "version": c.version,
                    "content_sha256": c.content_sha256,
                    "outcome": c.outcome,
                    "restrictions": [r.as_dict() for r in c.restrictions],
                    "approver": c.approver,
                    "approved_at": c.approved_at.isoformat() if c.approved_at else None,
                    "valid_until": c.valid_until.isoformat(),
                }
                for c in self.applied
            ],
            "not_applied": [
                {"id": c.id, "version": c.version, "status": c.status, "reason": reason}
                for c, reason in [*self.conflict, *self.skipped][:MAX_LISTED]
            ],
            "previous": [
                {
                    "id": c.id,
                    "version": c.version,
                    "status": c.status,
                    "outcome": c.outcome,
                    "restrictions": [r.as_dict() for r in c.restrictions],
                    "approver": c.approver,
                    "approved_at": c.approved_at.isoformat() if c.approved_at else None,
                    "valid_until": c.valid_until.isoformat(),
                    "charter_sha256": c.charter_sha256,
                }
                for c in self.previous[:MAX_PREVIOUS]
            ],
        }


def match(conclusions: Iterable[Conclusion], case: Case, now: datetime | None) -> Match | None:
    """Заключения организации кейса против кейса. ``None`` — у ОГРН кейса заключений нет."""
    own = sorted((c for c in conclusions if c.ogrn == case.organization.ogrn), key=_recent, reverse=True)
    if not own:
        return None
    day = check_day(case, now)
    charter = case.charter
    applicable: list[Conclusion] = []
    skipped: list[tuple[Conclusion, str]] = []
    previous: list[Conclusion] = []
    for item in own:
        reason = _why_not(item, case, day, now)
        if reason is None:
            applicable.append(item)
            continue
        same_version = charter is not None and item.charter_sha256 == charter.sha256
        if charter is not None and not same_version and item.status in _LIVE:
            previous.append(item)
        # По другой редакции называются только действовавшие заключения: старые черновики и отзывы — шум.
        if same_version or item.status in _LIVE:
            skipped.append((item, reason))
    clearing = [c for c in applicable if c.outcome == "no_restriction"]
    restricting = [c for c in applicable if c.outcome == "restriction"]
    conflict: list[tuple[Conclusion, str]] = []
    if clearing and restricting:
        numbers = ", ".join(c.id for c in restricting)
        conflict = [
            (c, f"противоречит заключению № {numbers}: по этой редакции устава выводы юристов расходятся")
            for c in clearing
        ]
        applicable = restricting
    return Match(tuple(applicable), tuple(skipped), tuple(conflict), tuple(previous))


def ref(item: Conclusion, quote: str | None = None) -> EvidenceRef:
    """Ссылка на заключение: номер, отпечаток вывода и редакция устава, к которой оно привязано."""
    text = quote or item.basis
    return EvidenceRef(f"conclusion:{item.id}", item.content_sha256, f"charter:{item.charter_sha256[:16]}", text[:400])


def facts(applied: Iterable[Conclusion]) -> list[CandidateFact]:
    """Ограничения применённых заключений как факты устава: их проверяет R-CHR-001."""
    out: list[CandidateFact] = []
    for item in applied:
        for restriction in item.restrictions:
            out.append(_fact(item, restriction))
    return out


def _fact(item: Conclusion, restriction: Restriction) -> CandidateFact:
    return CandidateFact(
        restriction.kind,
        restriction.value_rub,
        restriction.quote or item.basis,
        ref(item, restriction.quote),
        ExtractionMethod.MANUAL,
        1.0,
        inclusive=restriction.inclusive,
    )


def source(fact: CandidateFact) -> str:
    """Хвост сообщения R-CHR-001 для ограничения из заключения; у факта из текста устава хвоста нет."""
    doc = fact.ref.doc_id
    return f" (заключение юриста № {doc.removeprefix('conclusion:')})" if doc.startswith("conclusion:") else ""


def _short(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def applied_message(item: Conclusion, operation: str, found: str) -> str:
    """Находка о применённом заключении: какое, кто и когда утвердил, на каком основании, до какого дня."""
    if item.outcome == "no_restriction":
        head = (
            f"Применено заключение юриста № {item.id}: по этой редакции устава применимых ограничений "
            f"для операции {operation} нет"
        )
    else:
        listed = "; ".join(r.describe() for r in item.restrictions)
        head = (
            f"Применено заключение юриста № {item.id}: устав ограничивает полномочия руководителя — {listed}. "
            "Ограничение проверяет R-CHR-001"
        )
    text = (
        f"{head}. Утвердил {item.approver} {_day(item.approved_at)}, основание: «{_short(item.basis)}». "
        f"Действует до {item.valid_until:%d.%m.%Y}"
    )
    if item.scope_text():
        text += f", область: {item.scope_text()}"
    if found:
        text += f". Шаблоны нашли в тексте: {found}; вывод юриста по ним — в заключении"
    return text


def skipped_message(item: Conclusion, reason: str) -> str:
    return f"Заключение юриста № {item.id} не применено: {reason}"
