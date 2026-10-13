"""Серверная политика профиля проверки по ``MANDAT_ENV``.

Режим берётся из той же функции, что и для журнала (``mandat.audit.journal.mode``).
В режимах без ключа журнала (demo, dev, test) профиль ``demo`` разрешён и выбирается по умолчанию.
В остальных режимах, включая pilot, prod и любой неизвестный, допускается только ``strict``:
клиент не может выбрать более слабый профиль, а отсутствие выбора не превращается в demo.
"""

from __future__ import annotations

from mandat.audit.journal import KEYLESS_MODES, mode

PROFILES = frozenset({"demo", "strict"})


class ProfileNotAllowed(ValueError):
    """Запрошенный профиль запрещён в текущем режиме."""


def protected() -> bool:
    return mode() not in KEYLESS_MODES


def resolve_profile(requested: str | None) -> str:
    if requested is not None and requested not in PROFILES:
        raise ProfileNotAllowed(f"profile: {' или '.join(sorted(PROFILES))}")
    if not protected():
        return requested or "demo"
    if requested in (None, "strict"):
        return "strict"
    raise ProfileNotAllowed(f"MANDAT_ENV={mode()}: профиль {requested} запрещён, допускается только strict")
