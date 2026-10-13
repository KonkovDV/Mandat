"""Поиск ПДн в тексте и псевдонимизация HMAC перед логами и запросами к модели.

Паттерн перенесён из AeroBIM (``privacy_guard``/``sensitive_entities``) и дополнен
тем, чего там не было: ИНН и СНИЛС с контрольными числами, паспорт РФ, телефон
и почта. Токен детерминирован для одного ключа, поэтому сотрудник видит, что в
двух местах упомянут один и тот же человек, но не видит самого номера.
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


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern[str]
    check: Callable[[str], bool] | None = None


DETECTORS = (
    Detector("СНИЛС", re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b"), snils_valid),
    Detector("ИНН", re.compile(r"(?<!\d)(\d{12}|\d{10})(?!\d)"), inn_valid),
    Detector("ПАСПОРТ", re.compile(r"\b\d{2}\s?\d{2}\s?№?\s?\d{6}\b")),
    Detector("ТЕЛЕФОН", re.compile(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b")),
    Detector("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
)


class PiiKeyMissing(RuntimeError):
    """Режим требует собственный ключ псевдонимизации, а ``MANDAT_PII_KEY`` не задан."""


def _key() -> bytes:
    value = os.environ.get("MANDAT_PII_KEY", "").strip()
    if value:
        return value.encode("utf-8")
    from mandat.services.env_policy import protected

    if protected():
        # Ключ разработки лежит в репозитории: токен ИНН по нему восстанавливается перебором.
        raise PiiKeyMissing("MANDAT_PII_KEY не задан: на открытом ключе разработки ПДн не псевдонимизируются")
    return DEV_KEY.encode("utf-8")


def token(kind: str, value: str) -> str:
    digest = hmac.new(_key(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:10]
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
