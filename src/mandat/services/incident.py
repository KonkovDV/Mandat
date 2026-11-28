"""Инцидент с персональными данными: два срока от момента выявления и факты из журнала аудита.

152-ФЗ, ч. 3.1 ст. 21: о неправомерной или случайной передаче ПДн, повлёкшей нарушение прав субъектов, оператор
уведомляет Роскомнадзор в течение 24 часов, а в течение 72 часов сообщает результаты внутреннего расследования и
сведения о лицах, чьи действия стали его причиной. Оба срока считаются от выявления, а не один за другим: это
72 часа, а не 24 + 72 (воркшоп «Данные, безопасность и комплаенс», Академия Финтеха, 20.11.2026, слайд 30). Оператор,
у которого налажено взаимодействие с ГосСОПКА, сообщает и в НКЦКИ (приказ ФСБ России от 13.02.2023 № 77).

Отчёт собирает из журнала то, что нужно расследованию: кто, когда и сколько раз создавал, читал, удалял кейсы и
запрашивал допуск, в каком окне времени. ПДн клиентов в журнале нет — только идентификаторы кейсов, роли и хеши
отчётов. Если журнал не проходит проверку целостности, фактов из него нет: отчёт так и говорит, и расследование
опирается на другие источники. Решение об уведомлении принимает ответственный банка, а не программа.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mandat.audit import journal

MOSCOW = ZoneInfo("Europe/Moscow")
NOTICE = timedelta(hours=24)
INVESTIGATION = timedelta(hours=72)
# Действия журнала с персональными данными клиентов и что они значат для расследования.
PD_ACTIONS = {
    "verify": "создание отчёта",
    "read_case": "чтение отчёта",
    "read_report": "чтение карточки",
    "erase_case": "удаление",
    "evict_case": "вытеснение по квоте",
    "authorize_operation": "допуск операции",
    "review_open": "разбор",
    "review_accept": "разбор",
    "review_reject": "разбор",
    "review_request_docs": "разбор",
    "review_close": "разбор",
    "review_appeal": "разбор",
    "review_require": "разбор",
    "review_reset": "разбор",
}
CHANNELS = (
    "Роскомнадзор: уведомление за 24 часа и результаты расследования за 72 часа (152-ФЗ, ч. 3.1 ст. 21)",
    "НКЦКИ: если у банка налажено взаимодействие с ГосСОПКА (приказ ФСБ России № 77)",
    "ФинЦЕРТ Банка России: по регламенту банка",
)


@dataclass
class ActorFacts:
    actor: str
    roles: list[str]
    actions: dict[str, int]
    cases: int
    first_at: str
    last_at: str


@dataclass
class IncidentReport:
    detected_at: str
    notice_due: str
    investigation_due: str
    rule: str
    window_since: str | None
    window_until: str
    journal_ok: bool
    journal_reason: str | None
    events: int = 0
    cases: int = 0
    actors: list[ActorFacts] = field(default_factory=list)
    channels: tuple[str, ...] = CHANNELS
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def deadlines(detected_at: datetime) -> tuple[datetime, datetime]:
    """Сроки уведомления и результатов расследования. Оба — от выявления, второй не прибавляется к первому."""
    if detected_at.tzinfo is None:
        raise ValueError("момент выявления без часового пояса")
    return detected_at + NOTICE, detected_at + INVESTIGATION


def parse_moment(value: str, warnings: list[str], name: str) -> datetime:
    """ISO 8601. Время без пояса читается как московское, с предупреждением — как в событиях мониторинга."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        warnings.append(f"{name}: время без часового пояса прочитано как московское")
        moment = moment.replace(tzinfo=MOSCOW)
    return moment


def _at(record: dict[str, Any]) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(record.get("at")))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=MOSCOW)


def incident_report(
    journal_path: Path,
    *,
    detected_at: datetime,
    since: datetime | None = None,
    until: datetime | None = None,
    actor: str | None = None,
    warnings: list[str] | None = None,
) -> IncidentReport:
    """Сроки и факты для отчёта об инциденте. Окно по умолчанию — вся история до момента выявления."""
    notice, investigation = deadlines(detected_at)
    end = until or detected_at
    check = journal.verify_journal(journal_path)
    report = IncidentReport(
        detected_at=detected_at.astimezone(MOSCOW).isoformat(timespec="minutes"),
        notice_due=notice.astimezone(MOSCOW).isoformat(timespec="minutes"),
        investigation_due=investigation.astimezone(MOSCOW).isoformat(timespec="minutes"),
        rule="оба срока от выявления: 24 часа на уведомление, 72 часа на результаты расследования (не 24 + 72)",
        window_since=since.astimezone(MOSCOW).isoformat(timespec="minutes") if since else None,
        window_until=end.astimezone(MOSCOW).isoformat(timespec="minutes"),
        journal_ok=check.ok,
        journal_reason=None if check.ok else check.reason,
        warnings=list(warnings or []),
    )
    if not check.ok:
        report.warnings.append("журнал не прошёл проверку целостности: факты из него не приводятся")
        return report
    by_actor: dict[str, dict[str, Any]] = {}
    touched: set[str] = set()
    for record in journal.read_journal(journal_path):
        action = record.get("action")
        moment = _at(record)
        if action not in PD_ACTIONS or moment is None or moment > end or (since is not None and moment < since):
            continue
        name = str(record.get("actor") or "")
        if actor is not None and name != actor:
            continue
        report.events += 1
        case_id = str(record.get("case_id") or "")
        touched.add(case_id)
        fresh = {"roles": set(), "actions": {}, "cases": set(), "first": moment, "last": moment}
        row = by_actor.setdefault(name, fresh)
        row["roles"].add(str(record.get("role") or ""))
        row["actions"][action] = row["actions"].get(action, 0) + 1
        row["cases"].add(case_id)
        row["first"], row["last"] = min(row["first"], moment), max(row["last"], moment)
    report.cases = len(touched)
    report.actors = [
        ActorFacts(
            actor=name,
            roles=sorted(row["roles"]),
            actions=dict(sorted(row["actions"].items())),
            cases=len(row["cases"]),
            first_at=row["first"].astimezone(MOSCOW).isoformat(timespec="seconds"),
            last_at=row["last"].astimezone(MOSCOW).isoformat(timespec="seconds"),
        )
        for name, row in sorted(by_actor.items(), key=lambda item: (-len(item[1]["cases"]), item[0]))
    ]
    return report
