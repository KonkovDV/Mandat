"""Поиск ПДн в тексте и псевдонимизация HMAC перед логами и запросами к модели.

Паттерн перенесён из AeroBIM (``privacy_guard``/``sensitive_entities``) и дополнен
тем, чего там не было: ИНН и СНИЛС с контрольными числами, паспорт РФ, телефон
и почта. Токен детерминирован для одного ключа, поэтому сотрудник видит, что в
двух местах упомянут один и тот же человек, но не видит самого номера.

Псевдонимизация — не обезличивание (152-ФЗ, ст. 3, п. 9): по ключу токен сопоставим с человеком, а имена,
должности и прочий текст остаются. Дата рождения и адрес связывать между местами незачем, поэтому они не
заменяются токеном, а вырезаются (``redact``). Распознаётся только то, что описано ниже; полный список того,
что не распознаётся, — в docs/DATA_PROTECTION.md.
Ключ — ``MANDAT_PII_KEY``. Без ключа токены строятся на открытом ключе разработки, поэтому
так можно только в режимах demo, dev и test; в pilot, prod и любом другом режиме псевдонимизация
без ключа отказывает (см. docs/DATA_PROTECTION.md).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from mandat.domain.identifiers import inn_valid, snils_valid

DEV_KEY = "mandat-dev-key-not-for-production"
# 16 hex = 64 бита: столкновение токенов двух разных людей практически исключено и на миллионах записей.
TOKEN_HEX = 16


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern[str]
    check: Callable[[str], bool] | None = None


DETECTORS = (
    # Номер счёта (20 цифр) — до ИНН и СНИЛС, чтобы его части не принимались за них.
    Detector("СЧЁТ", re.compile(r"(?<!\d)\d{20}(?!\d)")),
    # СНИЛС с дефисами и без них: «123-456-789 01» и «12345678901».
    Detector("СНИЛС", re.compile(r"(?<!\d)\d{3}-?\d{3}-?\d{3}[ -]?\d{2}(?!\d)"), snils_valid),
    Detector("ИНН", re.compile(r"(?<!\d)(\d{12}|\d{10})(?!\d)"), inn_valid),
    # 12 цифр с неверной контрольной суммой — скорее всего ИНН физлица с опечаткой, а это всё равно ПДн.
    # Буквы a–f и двоеточие перед числом — это уже выданный токен, его не трогаем.
    Detector("НОМЕР", re.compile(r"(?<![\d:a-f])\d{12}(?![\da-f\]])")),
    Detector("ПАСПОРТ", re.compile(r"\b\d{2}\s?\d{2}\s?№?\s?\d{6}\b")),
    Detector("ТЕЛЕФОН", re.compile(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b")),
    Detector("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
)


# ГГГГ-ММ-ДД — первой: иначе «1980-02-01» режется как «80-02-01», и в тексте остаётся «19».
_DATE = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})"
    r"|\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"\s+\d{4}(?:\s*г(?:ода|\.)?)?)"
)
# Дата рождения: по подписи до даты («дата рождения:», «д.р.», «родился») или после («1980 г.р.»).
# Форма ГГГГ-ММ-ДД — та же, что xs:date в МЧД. Без подписи дата остаётся: иначе из устава пропадут сроки.
BIRTH_BEFORE = re.compile(
    r"(?P<label>(?:дат[аеуы]\s+рождения|д\.\s?р\.|родил(?:ся|ась)|рожд[её]нн?(?:ый|ая|ого|ой)?)[\s:,-]*)" + _DATE,
    re.IGNORECASE,
)
BIRTH_AFTER = re.compile(r"(?:" + _DATE + r"|(?<!\d)\d{4})(?P<label>\s*(?:г\.\s?р\.|года\s+рождения))", re.IGNORECASE)
# Адрес: по подписи («адрес:», «проживающий по адресу», «место жительства») до конца строки, «;» или «)».
ADDRESS_LABELED = re.compile(
    r"(?P<label>(?:(?:почтовый\s+|фактический\s+)?адрес(?:у|а|ом)?|"
    r"проживающ(?:ий|ая|его|ей)(?:\s+по\s+адресу)?|зарегистрирован(?:ный|ная|а)?\s+по\s+адресу|"
    r"место\s+жительства)[\s:,-]+)(?!электронн)[^\n;)]+",
    re.IGNORECASE,
)
# Адрес без подписи: индекс и город или улица с номером дома.
ADDRESS_BARE = re.compile(
    r"(?<!\d)\d{6},\s*(?:г\.|город)\s*[^\n;)]+"
    r"|(?:(?:г\.|город)\s*[А-ЯЁ][\w-]*,\s*)?(?:ул\.|улица|пр-т|проспект|пер\.|переулок|ш\.|шоссе|наб\.|набережная|бульвар|б-р)\s*[А-ЯЁ0-9][^\n;)]*?"
    r"(?:д\.|дом)\s*\d+[^\n;)]*",
)
BIRTH_MARK = "[ДАТА_РОЖДЕНИЯ]"
ADDRESS_MARK = "[АДРЕС]"


# Адрес юридического лица открыт (ЕГРЮЛ, ст. 6 129-ФЗ) и не ПДн: строка с такой подписью адрес не теряет.
ORG_ADDRESS = re.compile(
    r"мест[оае]\s+нахождения|юридическ\w*\s+адрес|адрес\w*\s+(?:места\s+нахождения\s+)?"
    r"(?:общества|организации|юридического\s+лица|компании)",
    re.IGNORECASE,
)


def _redact_line(line: str) -> str:
    out = BIRTH_BEFORE.sub(lambda m: m.group("label") + BIRTH_MARK, line)
    out = BIRTH_AFTER.sub(lambda m: BIRTH_MARK + m.group("label"), out)
    if ORG_ADDRESS.search(out):
        return out
    out = ADDRESS_LABELED.sub(lambda m: m.group("label") + ADDRESS_MARK, out)
    return ADDRESS_BARE.sub(ADDRESS_MARK, out)


def redact(text: str) -> str:
    """Вырезать дату рождения и адрес. Подпись («адрес:») остаётся, значение заменяется меткой без токена.

    Строка с адресом юридического лица («место нахождения», «юридический адрес», «адрес общества») адрес
    сохраняет: он публичный. Если в такой строке есть и адрес человека, он тоже останется.
    """
    return "\n".join(_redact_line(line) for line in text.split("\n"))


MIN_KEY_BYTES = 32


class PiiKeyMissing(RuntimeError):
    """Режим требует собственный ключ псевдонимизации, а ``MANDAT_PII_KEY`` не задан."""


def _key() -> bytes:
    from mandat.services.env_policy import protected

    value = os.environ.get("MANDAT_PII_KEY", "").strip()
    if value:
        if protected() and len(value.encode("utf-8")) < MIN_KEY_BYTES:
            # Короткий ключ HMAC (RFC 2104, разд. 3) делает токен ИНН подбираемым вместе с ключом.
            raise PiiKeyMissing(f"MANDAT_PII_KEY короче {MIN_KEY_BYTES} байт")
        return value.encode("utf-8")

    if protected():
        # Ключ разработки лежит в репозитории: токен ИНН по нему восстанавливается перебором.
        raise PiiKeyMissing("MANDAT_PII_KEY не задан: на открытом ключе разработки ПДн не псевдонимизируются")
    return DEV_KEY.encode("utf-8")


def token(kind: str, value: str) -> str:
    digest = hmac.new(_key(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:TOKEN_HEX]
    return f"[{kind}:{digest}]"


def find(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for det in DETECTORS:
        for match in det.pattern.finditer(text):
            value = match.group(0)
            if det.check is None or det.check(re.sub(r"\D", "", value) if det.kind == "ИНН" else value):
                found.append((det.kind, value))
    return found


def pseudonymize(text: str) -> str:
    out = text
    for det in DETECTORS:

        def repl(match: re.Match[str], det: Detector = det) -> str:
            value = match.group(0)
            probe = re.sub(r"\D", "", value) if det.kind == "ИНН" else value
            if det.check is not None and not det.check(probe):
                return value
            return token(det.kind, value)

        out = det.pattern.sub(repl, out)
    return out


def for_model(text: str) -> str:
    """Текст для модели: дата рождения и адрес вырезаны, реквизиты заменены токенами."""
    return pseudonymize(redact(text))
