"""Консоль стенда: веб-страница сотрудника банка поверх HTTP API «Мандата».

Включается только флагом ``MANDAT_STAND=1`` и только вне pilot и prod: защищённый режим со стендом не стартует.
Консоль работает от демо-учётных записей сессии браузера (operator, reviewer, dbo), их токены живут на сервере и
в браузер не уходят. Пять вкладок: проверка кейса, консультант, разбор юриста, допуск операции в ДБО, прогон
корпуса. Данные — вымышленные демо-кейсы или документы, которые сотрудник загрузил сам.
"""

from __future__ import annotations

import os

STAND_ENV = "MANDAT_STAND"
# Secure у cookie сессии: стенд за HTTPS (nginx с сертификатом). Локально по http cookie без Secure.
SECURE_COOKIE_ENV = "MANDAT_STAND_SECURE_COOKIE"
# Каталог рабочих файлов стенда: списки статусов демо-издателя. Пусто — временный каталог системы.
WORKDIR_ENV = "MANDAT_STAND_DIR"


class StandRefused(RuntimeError):
    """Стенд запрошен там, где он запрещён: pilot, prod и любой защищённый режим."""


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def requested() -> bool:
    """Флаг ``MANDAT_STAND`` включён."""
    return _flag(STAND_ENV)


def refusal() -> str | None:
    """Почему консоль стенда нельзя включить в текущем режиме, или ``None``."""
    from mandat.services.env_policy import protected

    if protected():
        from mandat.audit.journal import mode

        return f"MANDAT_ENV={mode()}: консоль стенда (MANDAT_STAND=1) работает только в demo, dev и test"
    return None


def problem() -> str | None:
    """Почему стенд нельзя запустить в этом режиме. ``None`` — можно или стенд не запрошен."""
    return refusal() if requested() else None


def enabled() -> bool:
    """Стенд включён. Запрос стенда в защищённом режиме — ``StandRefused``, а не тихое выключение."""
    reason = problem()
    if reason is not None:
        raise StandRefused(reason)
    return requested()


def secure_cookie() -> bool:
    return _flag(SECURE_COOKIE_ENV)
