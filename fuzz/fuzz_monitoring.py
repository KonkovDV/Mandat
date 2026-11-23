"""Фаззинг мониторинга: любая пачка JSON даёт тревоги или ошибку ввода, которую API превращает в 422.

Пачка приходит в ``POST /v1/events`` от учётной записи банка: права со сроками МЧД, события ЕГРЮЛ и реестра,
момент проверки. Любое другое исключение, зависание или квадратичный разбор — находка.
"""

from __future__ import annotations

import json

from _harness import instrumented, run

with instrumented():
    from mandat.services.monitoring import evaluate, parse_as_of, parse_events


def test_one_input(data: bytes) -> None:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return
    if not isinstance(payload, dict):
        return
    # Те же исключения, что POST /v1/events отвечает кодом 422.
    try:
        warnings: list[str] = []
        rights, events = parse_events(payload, warnings)
        as_of = parse_as_of(payload, warnings)
        evaluate(rights, events, as_of=as_of, notice_days=len(data) % 60)
    except (KeyError, ValueError, TypeError):
        pass


if __name__ == "__main__":
    run(test_one_input)
