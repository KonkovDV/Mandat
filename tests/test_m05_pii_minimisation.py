"""M-05: дата рождения и адрес вырезаются, в модель уходят только строки о полномочиях."""

from __future__ import annotations

import pytest

from mandat.advisory import llm
from mandat.privacy.pii import ADDRESS_MARK, BIRTH_MARK, for_model, redact


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    monkeypatch.delenv("MANDAT_PII_KEY", raising=False)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("дата рождения: 01.02.1980", "01.02.1980"),
        ("Дата рождения 1.2.80", "1.2.80"),
        ("д.р. 15-07-1990", "15-07-1990"),
        ("родился 5 марта 1975 г. в Москве", "5 марта 1975"),
        ("рождённая 12 декабря 1966 года", "12 декабря 1966"),
        ("Представитель, 12.03.1961 года рождения", "12.03.1961"),
        ("Поверенный, 1980 г.р.", "1980"),
    ],
)
def test_birth_date_is_cut(text, secret):
    out = redact(text)
    assert secret not in out and BIRTH_MARK in out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("проживающий по адресу: 101000, г. Москва, ул. Ленина, д. 1, кв. 2; далее", "Ленина"),
        ("Адрес: Казань, Кремлёвская 18", "Кремлёвская"),
        ("зарегистрирована по адресу г. Тверь, пр-т Победы, д. 3", "Победы"),
        ("место жительства — Самара, Молодогвардейская 10", "Молодогвардейская"),
        ("г. Москва, ул. Тверская, д. 7, кв. 12", "Тверская"),
        ("190000, г. Санкт-Петербург, Невский 1", "Невский"),
        ("пер. Чистый, дом 5", "Чистый"),
    ],
)
def test_address_is_cut(text, secret):
    out = redact(text)
    assert secret not in out and ADDRESS_MARK in out


def test_cut_values_are_not_tokens():
    # Метка без хеша: дату рождения и адрес нельзя сопоставить между документами даже по ключу.
    out = for_model("дата рождения: 01.02.1980; адрес: г. Москва, ул. Ленина, д. 1")
    assert out == f"дата рождения: {BIRTH_MARK}; адрес: {ADDRESS_MARK}"


@pytest.mark.parametrize(
    "text",
    [
        "Устав утверждён 01.02.2020",
        "Сделки свыше 1 000 000 руб. одобряет совет директоров",
        "адрес электронной почты: info@example.ru",
        "ОГРН 1027700132195, 2015 год",
    ],
)
def test_other_dates_and_text_are_kept(text):
    assert redact(text) == text


def test_keyed_details_still_pseudonymized():
    out = for_model("Директор, ИНН 7707083893, родился 01.02.1980")
    assert "7707083893" not in out and "[ИНН:" in out and BIRTH_MARK in out


def test_minimize_keeps_only_authority_lines():
    charter = "\n".join(
        [
            "1. Общество с ограниченной ответственностью «Ромашка».",
            "2. Генеральный директор, дата рождения 01.02.1980.",
            "3. Адрес директора: г. Москва, ул. Ленина, д. 1, кв. 2.",
            "4. Сделки свыше 5 000 000 руб. требуют одобрения общего собрания.",
            "5. Цвет логотипа — зелёный. Телефон офиса +7 495 123-45-67.",
        ]
    )
    content = llm.charter_messages(charter)[1]["content"]
    assert "Ромашка" not in content and "логотип" not in content and "123-45-67" not in content
    assert "5 000 000 руб." in content and "общего собрания" in content
    assert "01.02.1980" not in content and "Ленина" not in content


def test_minimize_without_authority_lines_sends_no_document():
    content = llm.charter_messages("Цвет логотипа зелёный.\nТелефон +7 495 123-45-67")[1]["content"]
    assert content == f"{llm.DATA_BEGIN}\n\n{llm.DATA_END}"
