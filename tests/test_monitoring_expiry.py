"""Сроки МЧД в мониторинге: предупреждение до конца срока и отзыв после, если реестр промолчал.

Срок права — самый ранний в цепочке: передоверие не действует дольше основной МЧД (п. 4 ст. 187 ГК РФ).
Истечение срока прекращает доверенность само по себе (подп. 1 п. 1 ст. 188 ГК РФ), без события реестра.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.storage import MemoryStore
from mandat.cli import main
from mandat.services.monitoring import evaluate, parse_as_of, parse_events

ORG = "7729985006"
MAIN = "ee2afcb0-9003-7ff0-ef03-8dad405558ea"
SUB = "f3c69a25-a0a0-3e35-723f-2ad0f5e063e7"


def _batch(valid, as_of="2026-11-23T09:00:00+03:00", events=()):
    right = {
        "right_id": "R-1",
        "org_inn": ORG,
        "representative_inn": "777588807322",
        "operation": "PAYMENT_SIGN",
        "poa_numbers": [MAIN, SUB],
        "eio_inn": None,
        "poa_valid_until": valid,
    }
    return {"as_of": as_of, "rights": [right], "events": list(events)}


def _alerts(payload, **kw):
    rights, events = parse_events(payload)
    return evaluate(rights, events, as_of=parse_as_of(payload), **kw)


def test_warning_names_the_poa_the_date_and_the_days_left():
    (alert,) = _alerts(_batch({MAIN: "2027-06-30", SUB: "2026-12-03"}))
    assert (alert.kind, alert.risk, alert.poa_number) == ("poa_expiring", "low", SUB)
    assert alert.affected_rights == ("R-1",)
    assert alert.reasons == (f"МЧД {SUB} действует по 03.12.2026 включительно: до конца срока 10 дней",)
    assert alert.actions[0] == "напомнить клиенту перевыпустить МЧД до 03.12.2026"
    # Предупреждение не меняет статус удостоверений: право ещё действует.
    assert alert.attestation_action is None and alert.events == ()


def test_main_poa_ending_first_takes_the_subdelegation_with_it():
    (alert,) = _alerts(_batch({MAIN: "2026-12-01", SUB: "2027-01-01"}))
    assert alert.poa_number == MAIN
    assert "до конца срока 8 дней" in alert.reasons[0]
    assert alert.reasons[1] == "передоверие не действует дольше основной МЧД (п. 4 ст. 187 ГК РФ)"


@pytest.mark.parametrize(
    ("last", "words"),
    [("2026-11-24", "1 день"), ("2026-11-25", "2 дня"), ("2026-11-28", "5 дней"), ("2026-12-14", "21 день")],
)
def test_days_left_are_spelled_in_russian(last, words):
    (alert,) = _alerts(_batch({SUB: last}))
    assert alert.reasons[0].endswith(f"до конца срока {words}")


def test_far_deadline_is_quiet_until_the_notice_window():
    payload = _batch({SUB: "2027-01-15"})
    assert _alerts(payload) == []
    (alert,) = _alerts(payload, notice_days=60)
    assert "до конца срока 53 дня" in alert.reasons[0]


def test_last_day_counts_by_the_moscow_calendar():
    # 03.12 23:30 по Москве — ещё последний день; 21:30 UTC того же дня — уже 04.12 по Москве.
    (today,) = _alerts(_batch({SUB: "2026-12-03"}, as_of="2026-12-03T23:30:00+03:00"))
    assert today.kind == "poa_expiring" and today.reasons[0].endswith("последний день — сегодня")
    (late,) = _alerts(_batch({SUB: "2026-12-03"}, as_of="2026-12-03T21:30:00+00:00"))
    assert late.kind == "poa_invalidated"


def test_registry_silence_does_not_keep_an_expired_poa_alive():
    (alert,) = _alerts(_batch({MAIN: "2026-11-20", SUB: "2026-12-31"}))
    assert (alert.kind, alert.attestation_action, alert.poa_number) == ("poa_invalidated", "revocation", MAIN)
    assert alert.reasons[0].startswith(f"МЧД {MAIN} действовала по 20.11.2026: срок истёк (подп. 1 п. 1 ст. 188")
    assert alert.reasons[1] == "вместе с ней теряет силу передоверие (п. 3 ст. 188 ГК РФ)"


@pytest.mark.parametrize("kind", ["POA_EXPIRED", "POA_REVOKED"])
def test_right_cut_by_an_event_gets_no_second_alert_for_its_date(kind):
    event = {"kind": kind, "org_inn": ORG, "at": "2026-11-21T10:00:00+03:00", "payload": {"number": SUB}}
    for last in ("2026-11-20", "2026-12-01"):
        alerts = _alerts(_batch({SUB: last}, events=[event]))
        assert [(a.kind, a.events != ()) for a in alerts] == [("poa_invalidated", True)]


def test_without_a_moment_dates_are_not_checked():
    rights, events = parse_events(_batch({SUB: "2026-11-01"}))
    assert evaluate(rights, events) == []


@pytest.mark.parametrize(
    ("valid", "message"),
    [
        ({"71ad2f2e-9941-cb17-2164-f172376af807": "2027-01-01"}, "которой нет в цепочке права"),
        ({SUB: "03.12.2026"}, "дата окончания МЧД не разобрана"),
        ({SUB: 20261203}, "строкой ГГГГ-ММ-ДД"),
        ([SUB, "2026-12-03"], "объектом «номер МЧД → дата»"),
    ],
)
def test_bad_deadlines_are_errors_not_silence(valid, message):
    with pytest.raises(ValueError, match=message):
        parse_events(_batch(valid))


def test_example_batch_warns_and_revokes(root: Path):
    payload = json.loads((root / "examples" / "events" / "poa_expiring.json").read_text(encoding="utf-8"))
    alerts = _alerts(payload)
    assert [(a.kind, a.affected_rights) for a in alerts] == [
        ("poa_expiring", ("R-021",)),
        ("poa_invalidated", ("R-022",)),
    ]


def test_cli_takes_the_moment_from_the_batch_or_the_flag(root: Path, capsys):
    path = str(root / "examples" / "events" / "poa_expiring.json")
    assert main(["monitor", path]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["as_of"] == "2026-11-23T09:00:00+03:00"
    assert [a["kind"] for a in out["alerts"]] == ["poa_expiring", "poa_invalidated"]
    # Флаг важнее пачки: на 01.11 обе МЧД ещё далеко от конца срока, кроме R-022 (20.11, через 19 дней).
    assert main(["monitor", path, "--as-of", "2026-11-01T12:00:00", "--notice-days", "20"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["as_of"] == "2026-11-01T12:00:00+03:00"
    assert [(a["kind"], a["affected_rights"]) for a in out["alerts"]] == [("poa_expiring", ["R-022"])]


def test_api_returns_the_moment_and_checks_notice_days(root: Path):
    payload = json.loads((root / "examples" / "events" / "poa_expiring.json").read_text(encoding="utf-8"))
    client = TestClient(create_app(token="t", rate_per_min=0, store=MemoryStore()))
    auth = {"Authorization": "Bearer t"}
    body = client.post("/v1/events", json=payload, headers=auth).json()
    assert body["as_of"] == "2026-11-23T09:00:00+03:00"
    assert [a["kind"] for a in body["alerts"]] == ["poa_expiring", "poa_invalidated"]
    assert client.post("/v1/events?notice_days=400", json=payload, headers=auth).status_code == 422
    # Без as_of в теле сроки считаются на время сервера.
    payload.pop("as_of")
    moment = datetime.fromisoformat(client.post("/v1/events", json=payload, headers=auth).json()["as_of"])
    assert abs((moment - datetime.now(UTC)).total_seconds()) < 60
