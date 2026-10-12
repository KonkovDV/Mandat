"""Поиск ПДн в тексте и псевдонимизация HMAC перед логами и запросами к модели.

Паттерн перенесён из AeroBIM (``privacy_guard``/``sensitive_entities``) и дополнен
тем, чего там не было: ИНН и СНИЛС с контрольными числами, паспорт РФ, телефон
и почта. Токен детерминирован для одного ключа, поэтому сотрудник видит, что в
двух местах упомянут один и тот же человек, но не видит самого номера.
Ключ — ``MANDAT_PII_KEY``; без ключа токены строятся на ключе разработки и
в отчёт добавляется предупреждение (см. docs/DATA_PROTECTION.md).
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


def _key() -> bytes:
    return (os.environ.get("MANDAT_PII_KEY") or DEV_KEY).encode("utf-8")


def using_dev_key() -> bool:
    return not os.environ.get("MANDAT_PII_KEY")


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
