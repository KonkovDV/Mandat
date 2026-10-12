"""Постоянный контроль выданных прав: события ЕГРЮЛ, реестра МЧД и интернет-банка.

Права не отзываются автоматически: по позиции ФНС смена руководителя не прекращает
выданные МЧД (ADR-002). «Мандат» находит права, которые зависят от изменившегося
звена, и предлагает действие. Комбинация «смена руководителя + новая МЧД + смена
реквизитов» в коротком окне — признак возможного захвата: высокий риск,
приостановка автоматической выдачи, кейс в антифрод банка.

Отмена основной МЧД обрывает всю цепочку передоверия (п. 3 ст. 188 ГК РФ): права,
в цепочке которых есть отменённая МЧД, приостанавливаются, даже если сама МЧД
представителя — производная. У тревоги есть действие над удостоверениями «Мандата»:
``revocation`` (МЧД отменена или истекла) или ``suspension`` (признаки захвата);
его исполняет ``mandat monitor --status-dir`` через списки статусов W3C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from mandat.data import sha256_json

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


def parse_events(payload: dict[str, Any]) -> tuple[list[GrantedRight], list[Event]]:
    rights = [
        GrantedRight(
            right_id=row["right_id"],
            org_inn=row["org_inn"],
            representative_inn=row["representative_inn"],
            operation=row["operation"],
            poa_numbers=tuple(row.get("poa_numbers") or ()),
            eio_inn=row.get("eio_inn"),
        )
        for row in payload.get("rights") or []
    ]
    events = []
    for row in payload.get("events") or []:
        if row["kind"] not in EVENT_KINDS:
            raise ValueError(f"неизвестный тип события {row['kind']!r}")
        events.append(Event(row["kind"], row["org_inn"], datetime.fromisoformat(row["at"]), row.get("payload") or {}))
    return rights, events


def evaluate(rights: list[GrantedRight], events: list[Event], *, window_hours: int = 72) -> list[Alert]:
    alerts: list[Alert] = []
    by_org: dict[str, list[Event]] = {}
    for ev in sorted(events, key=lambda e: e.at):
        by_org.setdefault(ev.org_inn, []).append(ev)

    for org, evs in by_org.items():
        org_rights = [r for r in rights if r.org_inn == org]
        window = timedelta(hours=window_hours)
        hijack = next(
            (
                [e for e in evs if start.at <= e.at <= start.at + window]
                for start in evs
                if start.kind == "EIO_CHANGED"
                and HIJACK_PATTERN <= {e.kind for e in evs if start.at <= e.at <= start.at + window}
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
            continue
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
                new_inn = ev.payload.get("new_eio_inn")
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
