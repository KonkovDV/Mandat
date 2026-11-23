"""Мониторинг на больших пачках: окно захвата, отмены и сроки МЧД считаются за линейное время.

Пачка до 8 МБ приходит в ``POST /v1/events`` от любой учётной записи с правом записи. Квадратичный разбор
держал бы процесс минуты: 20 тысяч событий одной компании считались 23 секунды, цепочка из 20 тысяч сроков
разбиралась почти 3 секунды. Скользящее окно сверено с прежним прямым перебором на случайных пачках.
"""

from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from mandat.services.monitoring import HIJACK_PATTERN, Event, _takeover_window, evaluate, parse_events

ORG = "7729985006"
KINDS = ["EIO_CHANGED", "NEW_POA", "REQUISITES_CHANGE_REQUEST", "POA_REVOKED"]


def _reference(evs: list[Event], window: timedelta) -> list[Event] | None:
    """Прежний прямой перебор: эталон смысла, квадратичный по времени."""
    return next(
        (
            [e for e in evs if start.at <= e.at <= start.at + window]
            for start in evs
            if HIJACK_PATTERN <= {e.kind for e in evs if start.at <= e.at <= start.at + window}
        ),
        None,
    )


def test_sliding_window_finds_the_same_takeover_as_the_direct_search():
    base = datetime(2026, 11, 1, tzinfo=UTC)
    for seed in range(300):
        rng = random.Random(seed)
        # Часы из короткого ряда: много событий с одинаковым временем и окна ровно на границе.
        evs = sorted(
            (
                Event(rng.choice(KINDS), ORG, base + timedelta(hours=rng.choice([0, 1, 24, 48, 71, 72, 73, 96, 150])))
                for _ in range(rng.randint(0, 12))
            ),
            key=lambda e: e.at,
        )
        window = timedelta(hours=rng.choice([1, 24, 72]))
        assert _takeover_window(evs, window) == _reference(evs, window), f"зерно {seed}"


def _within(seconds: float, call):
    started = time.perf_counter()
    result = call()
    elapsed = time.perf_counter() - started
    assert elapsed < seconds, f"{elapsed:.1f} с"
    return result


def test_many_events_of_one_company_are_checked_in_one_pass():
    base = datetime(2026, 11, 1, tzinfo=UTC)
    evs = [Event("NEW_POA", ORG, base + timedelta(minutes=i)) for i in range(30000)]
    assert _within(2.0, lambda: evaluate([], evs)) == []
    # Три сигнала в самом конце пачки всё равно находятся.
    tail = base + timedelta(days=40)
    evs += [Event(kind, ORG, tail + timedelta(minutes=i)) for i, kind in enumerate(sorted(HIJACK_PATTERN))]
    (alert,) = _within(2.0, lambda: evaluate([], evs))
    assert alert.kind == "possible_takeover" and len(alert.events) == 3


def test_revocations_find_their_rights_by_poa_number():
    numbers = [str(uuid.UUID(int=i + 1)) for i in range(20000)]
    payload = {
        "rights": [
            {
                "right_id": f"R-{i}",
                "org_inn": ORG,
                "representative_inn": "777588807322",
                "operation": "PAYMENT_SIGN",
                "poa_numbers": [n],
                "eio_inn": None,
            }
            for i, n in enumerate(numbers)
        ],
        "events": [
            {"kind": "POA_REVOKED", "org_inn": ORG, "at": "2026-11-23T10:00:00+03:00", "payload": {"number": n}}
            for n in numbers
        ],
    }
    rights, events = parse_events(payload)
    alerts = _within(2.0, lambda: evaluate(rights, events))
    assert len(alerts) == 20000 and alerts[123].affected_rights == ("R-123",)


def test_long_chain_with_deadlines_parses_in_linear_time():
    numbers = [str(uuid.UUID(int=i + 1)) for i in range(50000)]
    right = {
        "right_id": "R-1",
        "org_inn": ORG,
        "representative_inn": "777588807322",
        "operation": "PAYMENT_SIGN",
        "poa_numbers": numbers,
        "eio_inn": None,
        "poa_valid_until": {n: "2027-01-01" for n in reversed(numbers)},
    }
    (parsed,), _ = _within(3.0, lambda: parse_events({"rights": [right], "events": []}))
    # Сроки лежат в порядке цепочки, а не в порядке ключей в запросе.
    assert [n for n, _ in parsed.poa_valid_until[:2]] == numbers[:2]


def _revocations(rights: int, repeats: int) -> dict:
    number = str(uuid.UUID(int=7))
    return {
        "rights": [
            {
                "right_id": f"R-{i}",
                "org_inn": ORG,
                "representative_inn": "777588807322",
                "operation": "PAYMENT_SIGN",
                "poa_numbers": [number],
                "eio_inn": None,
            }
            for i in range(rights)
        ],
        "events": [
            {
                "kind": "POA_REVOKED",
                "org_inn": ORG,
                "at": f"2026-11-23T10:{i // 60 % 60:02d}:{i % 60:02d}+03:00",
                "payload": {"number": number},
            }
            for i in range(repeats)
        ],
    }


def test_redelivered_event_gives_one_alert():
    payload = _revocations(3, 1)
    payload["events"] *= 5
    rights, events = parse_events(payload)
    assert [a.affected_rights for a in evaluate(rights, events)] == [("R-0", "R-1", "R-2")]


def test_alert_fanout_is_bounded_and_reported(monkeypatch, root, tmp_path, capsys):
    import json

    from fastapi.testclient import TestClient

    from mandat.api.app import create_app
    from mandat.api.storage import MemoryStore
    from mandat.cli import main
    from mandat.services import monitoring

    monkeypatch.setattr(monitoring, "MAX_ALERT_RIGHTS", 50)
    payload = _revocations(10, 6)  # шесть разных отмен одной МЧД по десяти правам — 60 связей
    rights, events = parse_events(payload)
    with pytest.raises(ValueError, match="больше 50 связей «тревога — право»"):
        evaluate(rights, events)
    assert len(evaluate(rights, events[:5])) == 5  # 50 связей — ещё в пределе

    client = TestClient(create_app(token="t", rate_per_min=0, store=MemoryStore()))
    response = client.post("/v1/events", json=payload, headers={"Authorization": "Bearer t"})
    assert response.status_code == 422
    assert response.json()["detail"] == "пачка слишком велика: разбейте её по организациям"

    path = tmp_path / "batch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["monitor", str(path)]) == 2
    assert "разбейте её по организациям" in capsys.readouterr().err
