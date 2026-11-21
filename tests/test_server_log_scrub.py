"""Журнал сервера не хранит номера людей, записанные по группам.

Маска ловила только сплошные цифры: номер карты «4111 1111 1111 1111», паспорт «45 06 123456» и телефон
«+7 (903) 123-45-67» уходили в журнал как есть. По PCI DSS 4.0 (требование 3.5.1) номер карты нечитаем
везде, где хранится, в том числе в журналах; по 152-ФЗ журнал с номерами — та же база персональных данных,
что и хранилище кейсов.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.app import scrub
from mandat.api.storage import MemoryStore

TOKEN = "t" * 40


@pytest.mark.parametrize(
    "text",
    [
        "карта 4111 1111 1111 1111 отклонена",
        "карта 4111-1111-1111-1111",
        "паспорт 45 06 123456",
        "серия 45-06 номер 123456",
        "телефон +7 (903) 123-45-67",
        "телефон 8.903.123.45.67",
        "СНИЛС 112-233-445 95",
        "ИНН 7707 0838 93",
        "счёт 40702 810 9 3800 0012345",
        "дата 1980/02/01",
    ],
)
def test_grouped_numbers_are_masked(text):
    masked = scrub(text)
    assert "<num>" in masked
    assert not any(ch.isdigit() for ch in masked.split("<num>")[-1]), masked
    for part in ("1111", "123456", "123-45-67", "445", "0838", "0012345", "1980"):
        assert part not in masked, (text, masked)


@pytest.mark.parametrize("text", ["app.py:239", "HTTP 500", "ValueError: поле 12 из 40", "версия 3 устарела"])
def test_short_numbers_and_code_places_stay(text):
    assert scrub(text) == text


def test_grouped_card_number_from_an_unhandled_error_never_reaches_the_log(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("карта 4111 1111 1111 1111, телефон +7 (903) 123-45-67")

    monkeypatch.setattr(api, "evaluate", boom)
    monkeypatch.setattr(api, "parse_events", lambda payload, warnings: ([], []))
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore()), raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING, "mandat.api"):
        answer = client.post("/v1/events", json={"events": []}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert answer.status_code == 500
    assert "RuntimeError" in caplog.text
    for part in ("4111 1111", "1111 1111", "123-45-67", "(903)"):
        assert part not in caplog.text, part
