"""Постоянный контроль выданных прав: события ЕГРЮЛ, реестра МЧД и интернет-банка.

Права по МЧД сменой руководителя не отзываются: по позиции ФНС она не прекращает
выданные МЧД (п. 1 ст. 188 ГК РФ, ADR-002). «Мандат» находит права, которые зависят от изменившегося
звена, и предлагает действие. Комбинация «смена руководителя + новая МЧД + смена
реквизитов» в коротком окне — признак возможного захвата: высокий риск, рекомендация
приостановить автоматическую выдачу прав, кейс в антифрод банка.

Руководитель без доверенности действует сам как орган юрлица (п. 1 ст. 53 ГК РФ): его полномочие —
сама должность. С новой записью ЕГРЮЛ о другом руководителе оно кончилось, поэтому удостоверения
цепочки ``eio_direct`` отзываются; если новый руководитель в событии не назван, они приостанавливаются
до перепроверки.

Отмена основной МЧД обрывает всю цепочку передоверия (п. 3 ст. 188 ГК РФ): права,
в цепочке которых есть отменённая МЧД, попадают в тревогу с рекомендацией
приостановить их, даже если сама МЧД представителя — производная. Сами права
в ДБО модуль не меняет: он выставляет статус удостоверений и пишет рекомендации.
У тревоги есть действие над удостоверениями «Мандата»:
``revocation`` (МЧД отменена или истекла, руководитель без МЧД сменился) или ``suspension``
(признаки захвата, смена руководителя без имени нового); его исполняет ``mandat monitor --status-dir``
через списки статусов W3C.

Сроки МЧД видны из самих прав, без событий реестра: у права может быть последний день действия каждой
МЧД цепочки. На дату проверки за ``notice_days`` дней до конца срока выходит тревога ``poa_expiring``:
банк напоминает клиенту перевыпустить МЧД, пока платёж не сорвался. Передоверие не живёт дольше
основной МЧД (п. 4 ст. 187 ГК РФ), поэтому срок права — самый ранний в цепочке. Срок прошёл, а события
об этом не было — права падают так же, как по ``POA_EXPIRED`` (подп. 1 п. 1 ст. 188 ГК РФ):
молчание реестра не продлевает доверенность.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from mandat.data import sha256_json
from mandat.domain.identifiers import normalize_inn, normalize_poa_number

EVENT_KINDS = {"EIO_CHANGED", "POA_REVOKED", "POA_EXPIRED", "NEW_POA", "REQUISITES_CHANGE_REQUEST"}
HIJACK_PATTERN = {"EIO_CHANGED", "NEW_POA", "REQUISITES_CHANGE_REQUEST"}
# За сколько дней до конца срока МЧД предупреждать: месяца хватает перевыпустить и зарегистрировать новую.
NOTICE_DAYS = 30


@dataclass(frozen=True)
class GrantedRight:
    right_id: str
    org_inn: str
    representative_inn: str
    operation: str
    poa_numbers: tuple[str, ...]
    eio_inn: str | None
    # Последний день действия МЧД цепочки, включительно, по московскому календарю: (номер, дата).
    poa_valid_until: tuple[tuple[str, date], ...] = ()


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
    # Каких удостоверений касается действие: ``eio_direct`` — только руководителя без МЧД, None — по номеру МЧД
    # или всех удостоверений организации.
    chain_kind: str | None = None

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
            "chain_kind": self.chain_kind,
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
    if not 1900 <= parsed.year <= 9998:
        # На краях календаря сдвиг пояса и окно в сутки выходят за datetime: был OverflowError и 500.
        raise ValueError(f"время события вне диапазона 1900–9998 годов: {value!r}")
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


def _valid_until(value: Any, numbers: tuple[str, ...]) -> tuple[tuple[str, date], ...]:
    """Сроки МЧД права: {номер: "ГГГГ-ММ-ДД"}. Срок МЧД не из цепочки права — ошибка, а не тихий пропуск."""
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValueError("poa_valid_until должен быть объектом «номер МЧД → дата»")
    out = []
    for raw, day in value.items():
        number = normalize_poa_number(raw)
        if not number or number not in numbers:
            raise ValueError(f"срок указан для МЧД {raw!r}, которой нет в цепочке права")
        if not isinstance(day, str):
            raise ValueError("дата окончания МЧД должна быть строкой ГГГГ-ММ-ДД")
        try:
            out.append((number, date.fromisoformat(day)))
        except ValueError as exc:
            raise ValueError(f"дата окончания МЧД не разобрана: {day!r}") from exc
    return tuple(sorted(out, key=lambda pair: numbers.index(pair[0])))


def parse_as_of(payload: dict[str, Any], warnings: list[str] | None = None) -> datetime | None:
    """Момент проверки сроков — поле ``as_of`` пачки. Нет поля — None: вызывающий берёт текущее время."""
    value = payload.get("as_of") if isinstance(payload, dict) else None
    return None if value is None else _event_time(value, warnings)


def parse_events(payload: dict[str, Any], warnings: list[str] | None = None) -> tuple[list[GrantedRight], list[Event]]:
    """Права и события. Номера МЧД и ИНН приводятся к одному виду, отмена без номера МЧД — ошибка."""
    rights = []
    for row in _rows(payload, "rights"):
        if not isinstance(row, dict):
            raise ValueError("право должно быть объектом")
        numbers = _numbers(row.get("poa_numbers"))
        rights.append(
            GrantedRight(
                right_id=str(row["right_id"]),
                org_inn=normalize_inn(row["org_inn"]) or "",
                representative_inn=normalize_inn(row["representative_inn"]) or "",
                operation=str(row["operation"]),
                poa_numbers=numbers,
                eio_inn=normalize_inn(row.get("eio_inn")),
                poa_valid_until=_valid_until(row.get("poa_valid_until"), numbers),
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


def _days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    return f"{n} {'дня' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'дней'}"


def _expiry_alerts(rights: list[GrantedRight], events: list[Event], as_of: datetime, notice_days: int) -> list[Alert]:
    """Сроки МЧД из самих прав: предупреждение до конца срока и отзыв после, если реестр об этом промолчал."""
    today = as_of.astimezone(MOSCOW).date()
    closed = {(e.org_inn, e.payload.get("number")) for e in events if e.kind in {"POA_REVOKED", "POA_EXPIRED"}}
    soon: dict[tuple[str, str, date], list[GrantedRight]] = {}
    gone: dict[tuple[str, str, date], list[GrantedRight]] = {}
    for right in rights:
        # Право, которое событие этой пачки уже обрывает, второй тревоги по сроку не получает.
        if not right.poa_valid_until or any((right.org_inn, n) in closed for n in right.poa_numbers):
            continue
        # Право живёт до самого раннего срока цепочки: передоверие не дольше основной МЧД (п. 4 ст. 187 ГК РФ).
        number, last = min(right.poa_valid_until, key=lambda pair: pair[1])
        left = (last - today).days
        if left < 0:
            gone.setdefault((right.org_inn, number, last), []).append(right)
        elif 0 <= left <= notice_days:
            soon.setdefault((right.org_inn, number, last), []).append(right)
    alerts = []
    for (org, number, last), hit in sorted(soon.items(), key=lambda item: (item[0][2], item[0][0], item[0][1])):
        left = (last - today).days
        reasons = [
            f"МЧД {number} действует по {last:%d.%m.%Y} включительно: "
            + ("последний день — сегодня" if left == 0 else f"до конца срока {_days(left)}")
        ]
        if any(r.poa_numbers[-1] != number for r in hit):
            reasons.append("передоверие не действует дольше основной МЧД (п. 4 ст. 187 ГК РФ)")
        alerts.append(
            Alert(
                "poa_expiring",
                "low",
                org,
                tuple(r.right_id for r in hit),
                (
                    f"напомнить клиенту перевыпустить МЧД до {last:%d.%m.%Y}",
                    "предупредить ДБО: после этой даты операции по праву не пройдут",
                ),
                tuple(reasons),
                (),
                poa_number=number,
            )
        )
    for (org, number, last), hit in sorted(gone.items(), key=lambda item: (item[0][2], item[0][0], item[0][1])):
        reasons = [
            f"МЧД {number} действовала по {last:%d.%m.%Y}: срок истёк (подп. 1 п. 1 ст. 188 ГК РФ), "
            "события реестра не было"
        ]
        if any(r.poa_numbers[-1] != number for r in hit):
            reasons.append("вместе с ней теряет силу передоверие (п. 3 ст. 188 ГК РФ)")
        alerts.append(
            Alert(
                "poa_invalidated",
                "medium",
                org,
                tuple(r.right_id for r in hit),
                ("приостановить права, основанные на этой МЧД и её передоверии", "запросить новую МЧД"),
                tuple(reasons),
                (),
                attestation_action="revocation",
                poa_number=number,
            )
        )
    return alerts


def evaluate(
    rights: list[GrantedRight],
    events: list[Event],
    *,
    window_hours: int = 72,
    as_of: datetime | None = None,
    notice_days: int = NOTICE_DAYS,
) -> list[Alert]:
    """Тревоги по событиям, а с моментом ``as_of`` — ещё и по срокам МЧД из самих прав."""
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
                former = [r for r in org_rights if r.eio_inn and r.eio_inn != new_inn]
                # Право самого руководителя без МЧД держится на должности (п. 1 ст. 53 ГК РФ), а не на доверенности.
                own = tuple(r.right_id for r in former if not r.poa_numbers and r.representative_inn == r.eio_inn)
                by_poa = tuple(r.right_id for r in former if r.right_id not in own)
                grn = f"новая запись ЕГРЮЛ: ГРН {ev.payload.get('grn', '—')}"
                if by_poa:
                    alerts.append(
                        Alert(
                            "eio_changed",
                            "medium",
                            org,
                            by_poa,
                            (
                                "перепроверить права по новой цепочке",
                                "не отзывать автоматически: смена руководителя МЧД не прекращает",
                            ),
                            (grn,),
                            (ev.event_id,),
                        )
                    )
                if own:
                    # Новый руководитель не назван: возможно, переназначен тот же человек. Приостановка обратима.
                    known = new_inn is not None
                    alerts.append(
                        Alert(
                            "eio_authority_ended",
                            "medium",
                            org,
                            own,
                            (
                                "приостановить права прежнего руководителя, действовавшего без доверенности",
                                "проверить полномочия нового руководителя по свежей выписке ЕГРЮЛ",
                            ),
                            (
                                grn,
                                "руководитель действовал без доверенности: с новой записью ЕГРЮЛ его полномочие "
                                "кончилось (п. 1 ст. 53 ГК РФ)"
                                if known
                                else "новый руководитель в событии не назван: удостоверения приостановлены до проверки",
                            ),
                            (ev.event_id,),
                            attestation_action="revocation" if known else "suspension",
                            chain_kind="eio_direct",
                        )
                    )
    if as_of is not None:
        alerts.extend(_expiry_alerts(rights, events, as_of, notice_days))
    return alerts
