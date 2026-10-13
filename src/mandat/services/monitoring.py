"""Постоянный контроль выданных прав: события ЕГРЮЛ, реестра МЧД и интернет-банка.

Права не отзываются автоматически: по позиции ФНС смена руководителя не прекращает
выданные МЧД (ADR-002). «Мандат» находит права, которые зависят от изменившегося
звена, и предлагает действие. Комбинация «смена руководителя + новая МЧД + смена
реквизитов» в коротком окне — признак возможного захвата: высокий риск, рекомендация
приостановить автоматическую выдачу прав, кейс в антифрод банка.

Отмена основной МЧД обрывает всю цепочку передоверия (п. 3 ст. 188 ГК РФ): права,
в цепочке которых есть отменённая МЧД, попадают в тревогу с рекомендацией
приостановить их, даже если сама МЧД представителя — производная. Сами права
в ДБО модуль не меняет: он выставляет статус удостоверений и пишет рекомендации.
У тревоги есть действие над удостоверениями «Мандата»:
``revocation`` (МЧД отменена или истекла) или ``suspension`` (признаки захвата);
его исполняет ``mandat monitor --status-dir`` через списки статусов W3C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from mandat.data import sha256_json
from mandat.domain.identifiers import normalize_inn, normalize_poa_number

EVENT_KINDS = {"EIO_CHANGED", "POA_REVOKED", "POA_EXPIRED", "NEW_POA", "REQUISITES_CHANGE_REQUEST"}
HIJACK_PATTERN = {"EIO_CHANGED", "NEW_POA", "REQUISITES_CHANGE_REQUEST"}


@dataclass(frozen=True)
class GrantedRight:
    right_id: str
    org_inn: str
    representative_inn: str
    operation: str
    poa_numbers: tuple[str, ...]
    eio_inn: str | None


@dataclass(frozen=True)
class Event:
    kind: str
    org_inn: str
    at: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return sha256_json({"kind": self.kind, "org": self.org_inn, "at": self.at.isoformat(), "p": self.payload})[:16]


@dataclass(frozen=True)
class Alert:
    kind: str
    risk: str
    org_inn: str
    affected_rights: tuple[str, ...]
    actions: tuple[str, ...]
    reasons: tuple[str, ...]
    events: tuple[str, ...]
    attestation_action: str | None = None
    poa_number: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "risk": self.risk,
            "org_inn": self.org_inn,
            "affected_rights": list(self.affected_rights),
            "actions": list(self.actions),
            "reasons": list(self.reasons),
            "events": list(self.events),
            "attestation_action": self.attestation_action,
            "poa_number": self.poa_number,
        }


MOSCOW = timezone(timedelta(hours=3), "MSK")


def _event_time(value: Any, warnings: list[str] | None = None) -> datetime:
    """Время ISO 8601 строкой. Без часового пояса — московское время (UTC+3) и предупреждение в ответе.

    Раньше такое время роняло всю пачку, и отмена МЧД из той же пачки терялась.
    """
    if not isinstance(value, str):
        raise ValueError(f"время события должно быть строкой ISO 8601, а не {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"время события не разобрано: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
        if warnings is not None:
            warnings.append(f"время {value} без часового пояса, принято как московское (UTC+3)")
    return parsed.astimezone(UTC)


def _rows(payload: dict[str, Any], key: str) -> list[Any]:
    if not isinstance(payload, dict):
        raise ValueError("тело событий должно быть объектом")
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        raise ValueError(f"{key} должен быть списком")
    return rows


def _numbers(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("poa_numbers должен быть списком")
    return tuple(n for n in (normalize_poa_number(v) for v in value) if n)


def parse_events(payload: dict[str, Any], warnings: list[str] | None = None) -> tuple[list[GrantedRight], list[Event]]:
    """Права и события. Номера МЧД и ИНН приводятся к одному виду, отмена без номера МЧД — ошибка."""
    rights = []
    for row in _rows(payload, "rights"):
        if not isinstance(row, dict):
            raise ValueError("право должно быть объектом")
        rights.append(
            GrantedRight(
                right_id=str(row["right_id"]),
                org_inn=normalize_inn(row["org_inn"]) or "",
                representative_inn=normalize_inn(row["representative_inn"]) or "",
                operation=str(row["operation"]),
                poa_numbers=_numbers(row.get("poa_numbers")),
                eio_inn=normalize_inn(row.get("eio_inn")),
            )
        )
    events = []
    for row in _rows(payload, "events"):
        if not isinstance(row, dict):
            raise ValueError("событие должно быть объектом")
        if row["kind"] not in EVENT_KINDS:
            raise ValueError(f"неизвестный тип события {row['kind']!r}")
        body = row.get("payload") or {}
        if not isinstance(body, dict):
            raise ValueError("payload события должен быть объектом")
        if row["kind"] in {"POA_REVOKED", "POA_EXPIRED"}:
            number = normalize_poa_number(body.get("number"))
            if not number:
                raise ValueError(f"событие {row['kind']} без номера МЧД: неполное событие не пропускается молча")
            body = {**body, "number": number}
        org = normalize_inn(row["org_inn"])
        if not org:
            raise ValueError("у события нет ИНН организации")
        events.append(Event(row["kind"], org, _event_time(row["at"], warnings), body))
    return rights, events


def evaluate(rights: list[GrantedRight], events: list[Event], *, window_hours: int = 72) -> list[Alert]:
    alerts: list[Alert] = []
    by_org: dict[str, list[Event]] = {}
    for ev in sorted(events, key=lambda e: e.at):
        by_org.setdefault(ev.org_inn, []).append(ev)

    for org, evs in by_org.items():
        org_rights = [r for r in rights if r.org_inn == org]
        window = timedelta(hours=window_hours)
        # Окно не привязано к смене руководителя: любой из трёх сигналов может быть первым.
        # Тревога захвата не отменяет остальные сигналы той же пачки: отмена МЧД
        # остаётся отдельным вето.
        hijack = next(
            (
                [e for e in evs if start.at <= e.at <= start.at + window]
                for start in evs
                if HIJACK_PATTERN <= {e.kind for e in evs if start.at <= e.at <= start.at + window}
            ),
            None,
        )
        if hijack:
            alerts.append(
                Alert(
                    kind="possible_takeover",
                    risk="high",
                    org_inn=org,
                    affected_rights=tuple(r.right_id for r in org_rights),
                    actions=(
                        "приостановить автоматическую выдачу прав по организации",
                        "передать кейс в антифрод банка",
                        "уведомить прежних подписантов по известным каналам",
                        "перепроверить все выданные права по новой цепочке",
                    ),
                    reasons=(f"за {window_hours} ч: смена руководителя, новая МЧД и заявка на смену реквизитов",),
                    events=tuple(e.event_id for e in hijack),
                    attestation_action="suspension",
                )
            )
        for ev in evs:
            if ev.kind in {"POA_REVOKED", "POA_EXPIRED"}:
                number = str(ev.payload.get("number", ""))
                hit_rights = [r for r in org_rights if number in r.poa_numbers]
                if hit_rights:
                    word = "отменена" if ev.kind == "POA_REVOKED" else "истекла"
                    reasons = [f"МЧД {number} {word}"]
                    if any(r.poa_numbers and r.poa_numbers[-1] != number for r in hit_rights):
                        reasons.append("вместе с ней теряет силу передоверие (п. 3 ст. 188 ГК РФ)")
                    alerts.append(
                        Alert(
                            "poa_invalidated",
                            "medium",
                            org,
                            tuple(r.right_id for r in hit_rights),
                            ("приостановить права, основанные на этой МЧД и её передоверии", "запросить новую МЧД"),
                            tuple(reasons),
                            (ev.event_id,),
                            attestation_action="revocation",
                            poa_number=number,
                        )
                    )
            elif ev.kind == "EIO_CHANGED":
                new_inn = normalize_inn(ev.payload.get("new_eio_inn"))
                hit = tuple(r.right_id for r in org_rights if r.eio_inn and r.eio_inn != new_inn)
                if hit:
                    alerts.append(
                        Alert(
                            "eio_changed",
                            "medium",
                            org,
                            hit,
                            (
                                "перепроверить права по новой цепочке",
                                "не отзывать автоматически: смена руководителя МЧД не прекращает",
                            ),
                            (f"новая запись ЕГРЮЛ: ГРН {ev.payload.get('grn', '—')}",),
                            (ev.event_id,),
                        )
                    )
    return alerts
