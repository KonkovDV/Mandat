"""Порты к внешним системам банка и их заглушки для MVP.

В пилоте заглушки меняются на адаптеры банка (реестр МЧД через узел распределённого
реестра или оператора, ЕГРЮЛ через СМЭВ, проверка КЭП через сертифицированные СКЗИ),
ядро при этом не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from mandat.domain.identifiers import normalize_poa_number
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


@dataclass(frozen=True)
class RegistryObservation:
    """Один ответ реестра МЧД по номеру: статус, дата события (отмены, истечения) и момент запроса.

    Дату события и момент запроса реестр может не сообщить: тогда они ``None`` и не выдумываются.
    """

    number: str
    status: RegistryStatus
    event_date: date | None = None
    observed_at: datetime | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "number": self.number,
            "status": self.status.value,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
        }


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

    def __init__(self, statuses: dict[str, str | dict[str, str]]):
        self._statuses = {normalize_poa_number(k) or "": v for k, v in statuses.items()}

    def observe(self, number: str) -> RegistryObservation:
        raw = self._statuses.get(normalize_poa_number(number) or "")
        entry = raw if isinstance(raw, dict) else {"status": raw} if raw is not None else {}
        try:
            status = RegistryStatus(entry.get("status") or "")
        except ValueError:
            return RegistryObservation(number, RegistryStatus.UNAVAILABLE)
        event = entry.get("event_date")
        observed = entry.get("observed_at")
        return RegistryObservation(
            number,
            status,
            date.fromisoformat(event) if event else None,
            datetime.fromisoformat(observed) if observed else None,
        )

    def status(self, number: str) -> RegistryStatus:
        return self.observe(number).status


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
    """Процедура по 127-ФЗ. Что каждая значит для доверенности, см. ``BANKRUPTCY_RULES`` в rules/checks.py."""

    NONE = "none"
    # Юрлицо.
    OBSERVATION = "observation"  # наблюдение, ст. 64
    FINANCIAL_RECOVERY = "financial_recovery"  # финансовое оздоровление, ст. 82
    EXTERNAL_MANAGEMENT = "external_management"  # внешнее управление, ст. 94
    BANKRUPTCY_PROCEEDINGS = "bankruptcy_proceedings"  # конкурсное производство, ст. 126
    SETTLEMENT = "settlement"  # мировое соглашение, ст. 159
    # Гражданин.
    DEBT_RESTRUCTURING = "debt_restructuring"  # реструктуризация долгов, ст. 213.11
    PROPERTY_SALE = "property_sale"  # реализация имущества, ст. 213.25
    NOT_CHECKED = "not_checked"
    # Значение источника вне словаря: это не «не проверено» и не «банкротства нет», решает человек.
    UNRECOGNIZED = "unrecognized"


class BankruptcyPort(Protocol):
    """Сведения ЕФРСБ о банкротстве доверителя или представителя (подп. 7 п. 1 ст. 188 ГК РФ)."""

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
            status = BankruptcyStatus(raw)
        except ValueError:
            return BankruptcyStatus.UNRECOGNIZED
        # Служебные значения порта не принимаются как сведения из источника.
        if status in (BankruptcyStatus.NOT_CHECKED, BankruptcyStatus.UNRECOGNIZED):
            return BankruptcyStatus.UNRECOGNIZED
        return status


@dataclass(frozen=True)
class Ports:
    registry: PoaRegistryPort
    signature: SignaturePort
    bankruptcy: BankruptcyPort = field(default_factory=NotConnectedBankruptcy)
