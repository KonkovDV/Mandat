"""Порты к внешним системам банка и их заглушки для MVP.

В пилоте заглушки меняются на адаптеры банка (реестр МЧД через узел распределённого
реестра или оператора, ЕГРЮЛ через СМЭВ, проверка КЭП через сертифицированные СКЗИ),
ядро при этом не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from mandat.domain.model import PowerOfAttorney


class RegistryStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class SignatureStatus(StrEnum):
    VERIFIED = "verified"
    INVALID = "invalid"
    NOT_VERIFIED = "not_verified"


@dataclass(frozen=True)
class SignatureResult:
    status: SignatureStatus
    detail: str


class PoaRegistryPort(Protocol):
    name: str

    def status(self, number: str) -> RegistryStatus: ...


class SignaturePort(Protocol):
    name: str

    def verify(self, poa: PowerOfAttorney) -> SignatureResult: ...


class FixtureRegistry:
    """Статусы из манифеста кейса. Нет записи — реестр «недоступен», а не «действует»."""

    name = "fixture-registry"
    is_stub = True

    def __init__(self, statuses: dict[str, str]):
        self._statuses = dict(statuses)

    def status(self, number: str) -> RegistryStatus:
        raw = self._statuses.get(number)
        if raw is None:
            return RegistryStatus.UNAVAILABLE
        try:
            return RegistryStatus(raw)
        except ValueError:
            return RegistryStatus.UNAVAILABLE


class NotConnectedSignature:
    """ГОСТ-проверка КЭП в MVP не подключена: честный статус «не проверено»."""

    name = "signature-not-connected"
    is_stub = True

    def verify(self, poa: PowerOfAttorney) -> SignatureResult:
        return SignatureResult(
            SignatureStatus.NOT_VERIFIED,
            "проверка КЭП не подключена: в пилоте — через сертифицированные СКЗИ банка",
        )


class BankruptcyStatus(StrEnum):
    NONE = "none"
    # Наблюдение: доверенности не прекращаются, но действия органов управления ограничены.
    OBSERVATION = "observation"
    # Процедура, при которой лицо утрачивает право само выдавать доверенности
    # (конкурсное производство, внешнее управление, реализация имущества гражданина).
    TERMINATING = "terminating"
    NOT_CHECKED = "not_checked"


class BankruptcyPort(Protocol):
    """Сведения ЕФРСБ о банкротстве доверителя или представителя (подп. 5 п. 1 ст. 188 ГК РФ)."""

    name: str

    def status(self, inn: str) -> BankruptcyStatus: ...


class NotConnectedBankruptcy:
    """ЕФРСБ не подключён: на любой ИНН ответ «не проверено»."""

    name = "efrsb-not-connected"
    is_stub = True

    def status(self, inn: str) -> BankruptcyStatus:
        return BankruptcyStatus.NOT_CHECKED


class FixtureBankruptcy:
    """Сведения о банкротстве из манифеста кейса (поле ``bankruptcy``). Живой ЕФРСБ не опрашивается.

    Нет записи по ИНН — «не проверено», а не «банкротства нет».
    """

    name = "fixture-efrsb"
    is_stub = True

    def __init__(self, statuses: dict[str, str]):
        self._statuses = dict(statuses)

    def status(self, inn: str) -> BankruptcyStatus:
        raw = self._statuses.get(inn)
        if raw is None:
            return BankruptcyStatus.NOT_CHECKED
        try:
            return BankruptcyStatus(raw)
        except ValueError:
            return BankruptcyStatus.NOT_CHECKED


@dataclass(frozen=True)
class Ports:
    registry: PoaRegistryPort
    signature: SignaturePort
    bankruptcy: BankruptcyPort = field(default_factory=NotConnectedBankruptcy)
