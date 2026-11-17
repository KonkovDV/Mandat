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


def key_problem() -> str | None:
    """Почему сервис нельзя запускать в текущем режиме без ключей. ``None``, если ключи на месте.

    В защищённых режимах (pilot, prod и любой неизвестный) ключ журнала и ключ псевдонимизации ПДн
    проверяются при старте, а не при первой записи: иначе сервис поднимается и падает на первом кейсе.
    """
    if not protected():
        return None
    from mandat.audit.journal import JournalKeyMissing
    from mandat.audit.journal import _key as journal_key
    from mandat.privacy.pii import PiiKeyMissing
    from mandat.privacy.pii import _key as pii_key

    problems: list[str] = []
    try:
        journal_key(None)
    except JournalKeyMissing as exc:
        problems.append(str(exc))
    try:
        pii_key()
    except PiiKeyMissing as exc:
        problems.append(f"MANDAT_ENV={mode()}: {exc}")
    return "; ".join(problems) or None


def require_attestation_status() -> bool:
    """Нужна ли обязательная проверка статуса удостоверения.

    В pilot/prod (и любом защищённом режиме) — да, и выключить её нельзя: значение ``0`` там — ошибка
    конфигурации ``ValueError``, как и ослабление ``MANDAT_STATUS_MAX_AGE_BY_RISK`` (#112). Переменная
    ``MANDAT_REQUIRE_ATTESTATION_STATUS=1/0`` включает и выключает проверку только на стенде.
    """
    raw = __import__("os").environ.get("MANDAT_REQUIRE_ATTESTATION_STATUS", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        if protected():
            raise ValueError(
                f"MANDAT_ENV={mode()}: MANDAT_REQUIRE_ATTESTATION_STATUS={raw} не применяется: "
                "в защищённом режиме проверку статуса удостоверения выключить нельзя"
            )
        return False
    return protected()
