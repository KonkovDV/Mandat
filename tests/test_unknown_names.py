"""ФИО, которых нет в данных кейса, маскируются словарём и правилами соседства."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandat.advisory.llm import MixedAlphabet, charter_messages
from mandat.eval.metrics import clopper_pearson
from mandat.privacy.names import DIRECTOR, KnownPerson, mask_names
from mandat.privacy.unknown_names import MARKER, WORD, default_detector, mask_unknown_names, mixed_words

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "names"


def _covered(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return sum(max(0, min(end, b) - max(start, a)) for a, b in spans) >= end - start


def test_dictionary_is_cc0_wikidata():
    data = json.loads((Path(__file__).resolve().parents[1] / "src/mandat/data/names_ru.json").read_text("utf-8"))
    assert data["license"] == "CC0-1.0" and "Wikidata" in data["source"]
    assert len(data["given"]) > 1000 and len(data["surnames"]) > 7000


def test_recall_on_synthetic_sample():
    """Синтетические сочетания имени и фамилии; падежи, инициалы, «Имя Отчество Фамилия»."""
    rows = json.loads((FIXTURES / "synthetic_names.json").read_text("utf-8"))["rows"]
    detector = default_detector()
    people = words = words_hit = 0
    for row in rows:
        spans = detector.spans(row["text"])
        hits = [_covered(a, b, spans) for a, b in row["names"]]
        words += len(hits)
        words_hit += sum(hits)
        people += all(hits)
    assert clopper_pearson(people, len(rows))[0] >= 0.9, (people, len(rows))
    assert words_hit / words >= 0.98


def test_few_false_masks_on_charter_terms():
    lines = [ln for ln in (FIXTURES / "charter_terms.txt").read_text("utf-8").splitlines() if not ln.startswith("#")]
    detector = default_detector()
    capitalised = masked = 0
    for line in lines:
        spans = detector.spans(line)
        for m in WORD.finditer(line):
            capitalised += 1
            masked += _covered(m.start(), m.end(), spans)
    assert capitalised > 200
    assert masked / capitalised <= 0.02 and clopper_pearson(masked, capitalised)[1] <= 0.05, (masked, capitalised)


@pytest.mark.parametrize(
    "text",
    [
        "Договор подписан Пахомовым Ростиславом Юрьевичем в присутствии представителя.",  # творительный
        "Договор подписан Ланской Ильёй Петровной.",
        "Представитель Зурабян Т.К. вправе получать выписки.",
        "Подписант: Т. К. Зурабян.",
        "Генеральный директор Кудашева Сауле Анатольевна действует на основании Устава.",  # имя не из словаря
        "Сауле Анатольевна Кудашева назначена на должность.",
        "Решение принято по заявлению Белоусова.",  # суффикс русской фамилии
    ],
)
def test_names_are_masked(text):
    out = mask_unknown_names(text)
    assert MARKER in out
    assert not any(w in out for w in ("Пахомов", "Ростислав", "Юрьевич", "Зурабян", "Кудашев", "Сауле", "Белоусов"))


@pytest.mark.parametrize(
    "text",
    [
        "Доля участника Общества может быть отчуждена.",
        "Сумма сделки превышает 25 процентов стоимости имущества.",
        "Совет директоров Общества избирается Общим собранием участников.",
        "Генеральный директор действует без доверенности от имени Общества.",
    ],
)
def test_charter_terms_stay(text):
    assert mask_unknown_names(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Решение принято по заявлению Шор.",
        "Гражданина Шор вызвали в банк.",
        "Подпись: г-н Шор.",
        "по заявлению Доля.",
    ],
)
def test_person_cue_masks_the_following_word(text):
    """Подсказка о человеке маскирует следующее слово, даже без словаря и русского суффикса."""
    assert MARKER in mask_unknown_names(text)
    assert "Шор" not in mask_unknown_names(text)
    assert "Доля" not in mask_unknown_names(text)


def test_known_gap_bare_foreign_surname_without_cue():
    """Без подсказки одиночная фамилия вне словаря и без русского суффикса не ловится (LIMITS)."""
    assert mask_unknown_names("В протоколе назван Шор.") == "В протоколе назван Шор."
    assert mask_unknown_names("В протоколе назван Дюбуа-Леруа.") == "В протоколе назван Дюбуа-Леруа."


def test_hyphenated_surname_is_masked_when_one_part_is_known():
    """Дефис не прячет известную часть: маскируется всё слово, названия организаций остаются."""
    out = mask_unknown_names("Договор подписал Петров-Шор.")
    assert out == f"Договор подписал {MARKER}."
    assert mask_unknown_names("Филиал в городе Санкт-Петербург.") == "Филиал в городе Санкт-Петербург."
    assert mask_unknown_names("Платёж через систему «Альфа-Банк».") == "Платёж через систему «Альфа-Банк»."


def test_adjacent_words_become_one_marker():
    assert mask_unknown_names("Подписал Пахомов Ростислав Юрьевич.") == f"Подписал {MARKER}."


def test_model_request_masks_unknown_names():
    messages = charter_messages("Генеральный директор вправе подписывать договоры. Подпись: Пахомов Р.Ю.")
    assert "Пахомов" not in messages[1]["content"]
    assert MARKER in messages[1]["content"]


# --- регистр, двойники латиницы и разрядка: имя не проходит в модель в другой записи -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Договор подписал ПЕТРОВ ИВАН ИВАНОВИЧ.",
        "Подпись: ИВАНОВА М. С., главный бухгалтер.",
        "Сделки подписывает генеральный директор Пeтров Иван.",  # латинская e
        "Сделки подписывает Ивaнов Пётр Сергеевич.",  # латинская a
        "Сделки подписывает П е т р о в  И в а н.",
        "Сделки подписывает П е т р о в И в а н И в а н о в и ч.",
        "Подпись директора ПеТРОВ ИВАН.",
    ],
)
def test_names_in_capitals_lookalikes_and_spacing_are_masked(text):
    content = charter_messages(text)[1]["content"]
    assert MARKER in content
    for part in ("ПЕТРОВ", "Пeтров", "Ивaнов", "П е т", "ПеТРОВ", "ИВАН", "Иван", "Петров", "ИВАНОВА"):
        assert part not in content, (text, content)


def test_charter_terms_in_capitals_stay():
    text = (
        "СОВЕТ ДИРЕКТОРОВ ОДОБРЯЕТ СДЕЛКИ СВЫШЕ 1 000 000 РУБЛЕЙ. ГЕНЕРАЛЬНЫЙ ДИРЕКТОР ПЕТРОВ И.И. ВПРАВЕ ПОДПИСЫВАТЬ"
    )
    content = charter_messages(text)[1]["content"]
    assert "СОВЕТ ДИРЕКТОРОВ ОДОБРЯЕТ СДЕЛКИ" in content and "ВПРАВЕ ПОДПИСЫВАТЬ" in content
    assert "ПЕТРОВ" not in content


def test_known_person_with_a_lookalike_letter_is_masked():
    people = [KnownPerson(DIRECTOR, "Пeтров Пётр Петрович")]  # в данных кейса латинская e
    assert mask_names("Сделку подписал ПЕТРОВ П. П.", people) == f"Сделку подписал {DIRECTOR}"
    assert mask_names("Сделку подписал Петров", [KnownPerson(DIRECTOR, "Петров Пётр")]) == f"Сделку подписал {DIRECTOR}"


def test_text_with_a_mixed_alphabet_word_is_not_sent():
    with pytest.raises(MixedAlphabet):
        charter_messages("Сделки подписывает директор Пqтров.")  # q двойника в кириллице нет
    assert mixed_words("Пqтров и Mandat-Сервис, SYN-BANK-PAY-SIGN") == ["Пqтров"]
