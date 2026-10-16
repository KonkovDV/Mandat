"""M-05: ФИО известных людей, дата рождения и адрес не уходят в модель; в модель только строки о полномочиях."""

from __future__ import annotations

import dataclasses

import pytest

from mandat.advisory import llm
from mandat.domain.model import TextDocument
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.privacy.names import DIRECTOR, REPRESENTATIVE, KnownPerson, known_people, mask_names
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


# --- ФИО известных по кейсу людей (заявка, МЧД, ЕГРЮЛ) ---


# В синтетическом кейсе: директор по ЕГРЮЛ «Петров Сергей Сергеевич», представитель «Лебедева Мария Ивановна».
DIRECTOR_FORMS = [
    "Петров Сергей Сергеевич",
    "Петрова Сергея Сергеевича",
    "Петрову Сергею Сергеевичу",
    "Петровым Сергеем Сергеевичем",
    "Петрове Сергее Сергеевиче",
    "Сергей Сергеевич Петров",
    "Сергея Сергеевича Петрова",
    "Петров С.С.",
    "Петрова С. С.",
    "С.С. Петров",
    "С. С. Петрову",
    "Петровым С.",
    "Сергей Сергеевич",
    "Сергеем Сергеевичем",
    "ПЕТРОВ С.С.",
    "Петров",
    "Петровым",
]
REPRESENTATIVE_FORMS = [
    "Лебедева Мария Ивановна",
    "Лебедевой Марии Ивановны",
    "Лебедеву Марию Ивановну",
    "Лебедевой Марией Ивановной",
    "Мария Ивановна Лебедева",
    "Лебедева М.И.",
    "Лебедевой М. И.",
    "М.И. Лебедева",
    "М.И. Лебедевой",
    "Марией Ивановной",
    "Лебедевой",
]


def _case_with_charter(text: str):
    case = load_case_inline(synth.build("ok", 1).inline_manifest())
    charter = TextDocument("charter", "0" * 64, "charter_text", text)
    return dataclasses.replace(case, charter=charter)


def test_known_people_come_from_case_data():
    people = known_people(_case_with_charter("x"))
    assert KnownPerson(DIRECTOR, "Петров Сергей Сергеевич") in people
    assert KnownPerson(REPRESENTATIVE, "Лебедева Мария Ивановна") in people


@pytest.mark.parametrize(
    ("form", "marker"), [(f, DIRECTOR) for f in DIRECTOR_FORMS] + [(f, REPRESENTATIVE) for f in REPRESENTATIVE_FORMS]
)
def test_model_payload_has_no_known_name_in_any_form(form, marker):
    text = f"Сделки свыше 1 000 000 руб. подписывает {form} по одобрению совета директоров."
    content = llm.case_charter_messages(_case_with_charter(text))[1]["content"]
    assert form.lower() not in content.lower()
    for stem in ("петров", "лебедев"):
        assert stem not in content.lower()
    assert marker in content and "1 000 000 руб." in content


@pytest.mark.parametrize(
    "full_name",
    ["Вишневский Павел Андреевич", "Вишневская Ольга Петровна", "Сорокина Анна Юрьевна", "Ким Юрий Ильич"],
)
@pytest.mark.parametrize("form", ["{s}", "{s} {i}", "{i} {s}"])
def test_other_surname_types(full_name, form):
    surname, first, patronymic = full_name.split()
    declined = {
        "Вишневский": "Вишневского",
        "Вишневская": "Вишневской",
        "Сорокина": "Сорокиной",
        "Ким": "Кима",
    }[surname]
    text = form.format(s=declined, i=f"{first[0]}.{patronymic[0]}.")
    assert mask_names(text, [KnownPerson(DIRECTOR, full_name)]) == DIRECTOR


@pytest.mark.parametrize(("name", "forms"), [("Волков Павел Львович", ["Павла Львовича", "Павлом Львовичем"])])
def test_fleeting_vowel_first_names(name, forms):
    for form in forms:
        assert mask_names(form, [KnownPerson(DIRECTOR, name)]) == DIRECTOR


def test_unknown_names_are_not_masked():
    # Имя, которого нет в данных кейса, остаётся: это остаточный риск (DATA_PROTECTION).
    assert mask_names("Сидоров Пётр Ильич", [KnownPerson(DIRECTOR, "Петров Сергей Сергеевич")]) == "Сидоров Пётр Ильич"


@pytest.mark.parametrize(
    "line",
    [
        "Место нахождения Общества: 101000, г. Москва, ул. Ленина, д. 1",
        "Юридический адрес: г. Москва, ул. Тверская, д. 7",
        "Адрес общества: 190000, г. Санкт-Петербург, Невский 1",
        "Адрес юридического лица: г. Казань, ул. Баумана, д. 3",
    ],
)
def test_legal_entity_address_is_kept(line):
    assert redact(line) == line
