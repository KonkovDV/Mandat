"""ФИО, которых нет в данных кейса: словарь имён и фамилий и правила соседства (M-05, #65).

Словарь — `data/names_ru.json`: подписи элементов Wikidata «мужское имя», «женское имя» и «фамилия» с
кириллической записью (CC0 1.0). Формы слов строятся по правилам склонения, без морфологической библиотеки.

Правила fail-closed: при сомнении слово маскируется.

- отчество (по суффиксу) — всегда;
- фамилия из словаря в любой форме — всегда, кроме слов устава вроде «Доля» и «Сумма» без соседства с ФИО;
- слово с заглавной рядом с инициалами («Иванов И.И.», «И. И. Иванов») — всегда;
- слово перед отчеством или за два слова до него («Фамилия Имя Отчество») и слово после отчества
  («Имя Отчество Фамилия») — даже если его нет в словаре;
- имя из словаря рядом с другим словом ФИО;
- слово с суффиксом русской фамилии (-ов, -ин, -енко, …) рядом со словом ФИО или не в начале предложения;
- слово с заглавной сразу после подсказки о человеке («заявлению», «гражданина», «фамилии», «господина», «г-н»),
  даже если его нет в словаре и у него нет русского суффикса;
- двойная фамилия через дефис целиком, если хотя бы одна часть есть в словаре или с русским суффиксом
  («Петров-Шор»). «Альфа-Банк» и «Санкт-Петербург» частями фамилий не являются.

Не ловится: то же слово без подсказки, имени, отчества и инициалов («В протоколе назван Шор») и дефис,
у которого ни одна часть не известна («Дюбуа-Леруа»). Это остаток в LIMITS.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

MARKER = "[ФИО]"
WORD = re.compile(r"(?<![А-ЯЁа-яё])[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?")
INITIALS = re.compile(r"(?<![А-ЯЁа-яё])[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?")
PATRONYMIC = re.compile(
    r"^[А-ЯЁ][а-яё]+(?:(?:ович|евич|ьич|ич)(?:а|у|ем|ом|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|у|ой|ою))$"
)
SURNAME_LIKE = re.compile(
    r"^[А-ЯЁ][а-яё]{2,}(?:ов|ев|ёв|ин|ын)(?:а|у|ым|ом|е|ой|ых|ыми|ы)?$"
    r"|^[А-ЯЁ][а-яё]{2,}(?:енко|ук|юк|чук|дзе|швили)$"
)
SENTENCE_BREAK = '.!?:;«"(\n'
# Подсказка о человеке непосредственно перед словом. Приём контекстных слов у распознавателей ПДн:
# сомнение маскируется. «директор Общества» сюда не входит: иначе уставные термины станут [ФИО].
PERSON_CUE = re.compile(
    r"(?<![А-ЯЁа-яё])(?:"
    r"заявлени(?:ем|ю|я|е|и)|заявител(?:ем|я|ю|е|ь)|"
    r"гражданин(?:а|у|ом|е|кой)?|гражданк(?:ой|а|и|е|у)|"
    r"фамили(?:ей|я|и|ю)|господин(?:а|у|ом|е)?|госпож(?:ой|а|и|е|у)|"
    r"г-н|г-жа"
    r")(?:[:\-,])?\s+$",
    re.IGNORECASE,
)
# Слова устава, совпадающие с фамилиями из словаря («Доля», «Сумма»): без соседства с ФИО не маскируются.
# Найдены на главе III 14-ФЗ, которая в оценке ложных масок не участвует.
CHARTER_WORDS = frozenset(
    base + ending for base in ("дол", "сумм") for ending in ("я", "а", "и", "ы", "ю", "у", "ей", "ой", "е")
)


def _with(base: str, endings: tuple[str, ...]) -> set[str]:
    return {base + e for e in endings}


def surname_forms(surname: str) -> set[str]:
    """Падежные формы фамилии в нижнем регистре, мужские и женские."""
    low = surname.lower()
    if re.search(r"(?:ов|ев|ёв|ин|ын)$", low):
        return _with(low, ("", "а", "у", "ым", "е", "ой", "ы", "ых", "ыми"))
    for masc in ("ский", "цкий", "ской", "цкой"):
        if low.endswith(masc):
            return _with(low[:-2], ("ий", "ой", "ого", "ому", "им", "ом", "ая", "ую", "ие", "их", "ими"))
    if low.endswith(("ой", "ый", "ий")):
        return _with(low[:-2], ("ой", "ый", "ий", "ого", "ому", "ым", "им", "ом", "ая", "ую"))
    if low.endswith("а"):
        return {low} | _with(low[:-1], ("ы", "и", "е", "у", "ой", "ою"))
    if low.endswith("я"):
        return {low} | _with(low[:-1], ("и", "е", "ю", "ей"))
    if low.endswith(("о", "у", "ю", "и", "е", "ых", "их")):
        return {low}
    if low.endswith("ь"):
        return {low} | _with(low[:-1], ("я", "ю", "ем", "е"))
    return _with(low, ("", "а", "у", "ом", "ем", "е"))


def given_forms(name: str) -> set[str]:
    """Падежные формы имени в нижнем регистре; беглая гласная («Павел — Павла»)."""
    low = name.lower()
    if low.endswith("ий"):
        return {low} | _with(low[:-2], ("ия", "ию", "ием", "ии"))
    if low.endswith("й"):
        return {low} | _with(low[:-1], ("я", "ю", "ем", "е"))
    if low.endswith("ь"):
        return {low} | _with(low[:-1], ("я", "ю", "ем", "е", "и", "ью"))
    if low.endswith("ия"):
        return {low} | _with(low[:-1], ("и", "ю", "ей", "ею"))
    if low.endswith(("а", "я")):
        return {low} | _with(low[:-1], ("ы", "и", "е", "у", "ю", "ой", "ей"))
    if low.endswith(("о", "е", "и", "у")):
        return {low}
    forms = _with(low, ("", "а", "у", "ом", "е", "ем"))
    fleeting = re.search(r"([^аеёиоуыэюя])е([лр])$", low)
    if fleeting:
        forms |= _with(low[: fleeting.start()] + fleeting.group(1) + fleeting.group(2), ("а", "у", "ом", "е"))
    return forms


def _fold(word: str) -> str:
    return word.lower().replace("ё", "е")


class NameDetector:
    """Поиск ФИО по словарю имён и фамилий и правилам соседства."""

    def __init__(self, given: list[str], surnames: list[str]) -> None:
        self.given = {_fold(f) for g in given for f in given_forms(g)}
        self.surnames = {_fold(f) for s in surnames for f in surname_forms(s)}

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Участки текста (начало, конец), которые нужно замаскировать."""
        tokens = [(m.start(), m.end(), m.group(0)) for m in WORD.finditer(text)]
        initials = [(m.start(), m.end()) for m in INITIALS.finditer(text)]
        kinds = [self._kind(word) for _, _, word in tokens]
        n = len(tokens)

        def touching(left_end: int, right_start: int) -> bool:
            return 0 <= right_start - left_end <= 2 and not text[left_end:right_start].strip(" \u00a0")

        adjacent = [i + 1 < n and touching(tokens[i][1], tokens[i + 1][0]) for i in range(n)]

        def kind(j: int) -> set[str]:
            return kinds[j] if 0 <= j < n else set()

        out = list(initials)
        for i, (start, end, _word) in enumerate(tokens):
            own = kinds[i]
            prev = kind(i - 1) if i > 0 and adjacent[i - 1] else set()
            nxt = kind(i + 1) if adjacent[i] else set()
            nxt2 = kind(i + 2) if adjacent[i] and i + 1 < n and adjacent[i + 1] else set()
            near_initials = any(touching(b, start) or touching(end, a) for a, b in initials)
            if (
                "P" in own
                or "S" in own
                or near_initials
                or "P" in prev
                or "P" in nxt
                or "P" in nxt2
                or ("G" in own and bool((prev | nxt) & {"P", "S", "s", "G"}))
                or ("s" in own and bool((prev | nxt) & {"G", "P"}))
                or ("s" in own and not self._sentence_start(text, start))
                or self._person_cue(text, start)
            ):
                out.append((start, end))
        return out

    def mask(self, text: str, marker: str = MARKER) -> str:
        """Заменить найденные ФИО меткой; соседние слова одного ФИО — одной меткой."""
        spans = sorted(self.spans(text))
        merged: list[list[int]] = []
        for start, end in spans:
            if merged and not text[merged[-1][1] : start].strip(" \u00a0"):
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        out, pos = [], 0
        for start, end in merged:
            out += [text[pos:start], marker]
            pos = end
        return "".join(out) + text[pos:]

    def _kind(self, word: str) -> set[str]:
        kinds = self._kind_one(word)
        if "-" in word:
            for part in word.split("-"):
                if part:
                    kinds |= self._kind_one(part)
        return kinds

    def _kind_one(self, word: str) -> set[str]:
        low = _fold(word)
        kinds = set()
        if PATRONYMIC.match(word):
            kinds.add("P")
        if low in self.given:
            kinds.add("G")
        if low in self.surnames and low not in CHARTER_WORDS:
            kinds.add("S")
        if SURNAME_LIKE.match(word):
            kinds.add("s")
        return kinds

    @staticmethod
    def _person_cue(text: str, start: int) -> bool:
        return PERSON_CUE.search(text[max(0, start - 64) : start]) is not None

    @staticmethod
    def _sentence_start(text: str, start: int) -> bool:
        before = text[:start].rstrip()
        return not before or before[-1] in SENTENCE_BREAK


@lru_cache(maxsize=1)
def default_detector() -> NameDetector:
    data = json.loads(resources.files("mandat.data").joinpath("names_ru.json").read_text(encoding="utf-8"))
    return NameDetector(data["given"], data["surnames"])


def mask_unknown_names(text: str) -> str:
    """Замаскировать ФИО, которых нет в данных кейса (после ``names.mask_names``)."""
    return default_detector().mask(text)
