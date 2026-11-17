"""Замена ФИО известных по кейсу людей меткой роли перед отправкой в модель (M-05).

Имена берутся из структурированных данных кейса: заявитель, представители и подписант МЧД, руководители
из ЕГРЮЛ. В тексте ищется фамилия во всех падежах и в женской форме, полное ФИО в порядках «Фамилия Имя
Отчество» и «Имя Отчество Фамилия», фамилия с инициалами («Иванов И.И.», «И.И. Иванов») и «Имя Отчество».
Склонение по основе: от фамилии, имени и отчества отрезается окончание, к основе допускается до трёх букв.
Это правило, а не морфологический словарь: лишнее слово с той же основой тоже заменится, а имя, которого нет
в данных кейса, не заменяется вовсе (docs/DATA_PROTECTION.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from mandat.domain.model import Case

DIRECTOR = "[ДИРЕКТОР]"
REPRESENTATIVE = "[ПРЕДСТАВИТЕЛЬ]"
SIGNER = "[ПОДПИСАНТ]"
_TAIL = r"[а-яё]{0,3}"
_PATRONYMIC = re.compile(r"(?:вич|вна|ична|чна|ич)$", re.IGNORECASE)


@dataclass(frozen=True)
class KnownPerson:
    marker: str
    full_name: str


def _letters(stem: str) -> str:
    return "".join("[её]" if ch in "её" else re.escape(ch) for ch in stem.lower())


def _surname_stem(word: str) -> str:
    low = word.lower()
    for ending in ("ский", "цкий", "ской", "цкой"):
        if low.endswith(ending):
            return word[:-2]
    for ending in ("ская", "цкая"):
        if low.endswith(ending):
            return word[:-2]
    if re.search(r"(?:ов|ев|ёв|ин|ын)а$", low):
        return word[:-1]
    if low[-1:] in "аяоеыиьй" and len(word) > 3:
        return word[:-1]
    return word


def _first_stems(word: str) -> list[str]:
    low = word.lower()
    stem = word[:-1] if low[-1:] in "аяйьое" and len(word) > 3 else word
    stems = [stem]
    # Беглая гласная: Павел — Павла, Лев — Льва.
    if re.search(r"[^аеёиоуыэюя]е[лр]$", low) or low == "лев":
        stems.append(word[:-2] + word[-1] if low != "лев" else "Льв")
    return stems


def _parts(full_name: str) -> tuple[str | None, str | None, str | None]:
    """(фамилия, имя, отчество) из ФИО в порядке «Фамилия Имя Отчество» или «Имя Отчество Фамилия»."""
    tokens = [t for t in re.split(r"\s+", full_name.strip()) if t]
    if len(tokens) >= 3 and _PATRONYMIC.search(tokens[1]) and not _PATRONYMIC.search(tokens[2]):
        return tokens[2], tokens[0], tokens[1]
    if len(tokens) >= 3:
        return tokens[0], tokens[1], tokens[2]
    if len(tokens) == 2:
        return tokens[0], tokens[1], None
    return (tokens[0], None, None) if tokens else (None, None, None)


def person_pattern(full_name: str) -> re.Pattern[str] | None:
    surname, first, patronymic = _parts(full_name)
    if not surname or len(_surname_stem(surname)) < 3:
        return None
    sur = _letters(_surname_stem(surname)) + r"(?!ич|н[аеоуы])" + _TAIL
    alts = []
    fir = pat = None
    if first:
        fir = "(?:" + "|".join(_letters(s) for s in _first_stems(first)) + ")" + _TAIL
    if patronymic:
        pat = _letters(patronymic[:-1] if patronymic.lower().endswith("а") else patronymic) + _TAIL
    initials = ""
    if first:
        initials = _letters(first[0]) + r"\.\s?"
        if patronymic:
            initials += "(?:" + _letters(patronymic[0]) + r"\.)?"
    if fir:
        alts.append(sur + r"\s+" + fir + (r"(?:\s+" + pat + ")?" if pat else ""))
        alts.append(fir + (r"\s+" + pat if pat else "") + r"\s+" + sur)
        alts.append(sur + r"\s+" + initials)
        alts.append(initials + r"\s?" + sur)
        if pat:
            alts.append(fir + r"\s+" + pat)
    alts.append(sur)
    return re.compile(r"(?<![\w.])(?:" + "|".join(alts) + r")(?!\w)", re.IGNORECASE)


def known_people(case: Case) -> list[KnownPerson]:
    """Люди кейса с меткой роли: руководители из ЕГРЮЛ, представители, подписанты МЧД, заявитель."""
    out: list[KnownPerson] = []
    directors = {rec.person.full_name for rec in (case.egrul.executives if case.egrul else ()) if rec.person.full_name}
    out += [KnownPerson(DIRECTOR, name) for name in sorted(directors)]
    for poa in case.poas:
        out += [KnownPerson(REPRESENTATIVE, p.full_name) for p in poa.representatives if p.full_name]
        if poa.signer.full_name and poa.signer.full_name not in directors:
            out.append(KnownPerson(SIGNER, poa.signer.full_name))
    if case.requester.full_name:
        out.append(KnownPerson(REPRESENTATIVE, case.requester.full_name))
    seen: set[str] = set()
    unique = []
    for person in out:
        key = " ".join(person.full_name.lower().split())
        if key not in seen:
            seen.add(key)
            unique.append(person)
    return unique


def mask_names(text: str, people: Iterable[KnownPerson]) -> str:
    """Заменить упоминания известных людей меткой роли. Более длинные ФИО — первыми."""
    out = text
    for person in sorted(people, key=lambda p: -len(p.full_name)):
        pattern = person_pattern(person.full_name)
        if pattern is not None:
            out = pattern.sub(person.marker, out)
    return out
