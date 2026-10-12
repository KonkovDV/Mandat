"""Сущности предметной области: организация, лицо, доверенность, ЕГРЮЛ, операция, факт, находка.

Каждый факт и каждая находка несут ссылку на источник (документ, его sha256 и
локатор: XPath, путь в JSON, диапазон символов или ключ реестра). Без ссылки
находка не попадает в отчёт: см. ``Finding.__post_init__``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from mandat.domain.verdict import LegalEffect


class ExtractionMethod(StrEnum):
    DETERMINISTIC_PARSER = "deterministic_parser"
    REGISTRY_LOOKUP = "registry_lookup"
    HEURISTIC = "heuristic"
    LLM_ADVISORY = "llm_advisory"
    MANUAL = "manual"


class Subdelegation(StrEnum):
    """Признак передоверия ``ПрПередов``: 1 — без права, 2 — однократно, 3 — с последующим передоверием.

    Значения подтверждены описанием схемы и методическим материалом (docs/FORMAT_NOTES.md).
    """

    NONE = "none"  # без права передоверия
    ONCE = "once"  # однократное передоверие
    CHAIN = "chain"  # с правом последующего передоверия
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceRef:
    doc_id: str
    doc_sha256: str
    locator: str
    quote: str | None = None

    def short(self) -> str:
        return f"{self.doc_id}@{self.doc_sha256[:12]}#{self.locator}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_sha256": self.doc_sha256,
            "locator": self.locator,
            "quote": self.quote,
        }


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    ref: EvidenceRef
    method: ExtractionMethod = ExtractionMethod.DETERMINISTIC_PARSER
    confidence: float = 1.0


@dataclass(frozen=True)
class Person:
    full_name: str | None = None
    inn: str | None = None
    snils: str | None = None
    position: str | None = None

    def same_as(self, other: Person) -> bool | None:
        """True/False по ИНН или СНИЛС; None, если сравнить нечем (ФИО не считаем доказательством)."""
        from mandat.domain.identifiers import normalize_snils

        if self.inn and other.inn:
            return self.inn == other.inn
        if self.snils and other.snils:
            return normalize_snils(self.snils) == normalize_snils(other.snils)
        return None

    def label(self) -> str:
        return self.full_name or self.inn or "лицо без ФИО"


@dataclass(frozen=True)
class Organization:
    inn: str | None = None
    ogrn: str | None = None
    kpp: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Authority:
    kind: str  # "machine" | "text"
    code: str | None = None
    mnemonic: str | None = None
    name: str | None = None
    text: str | None = None
    ref: EvidenceRef | None = None


@dataclass(frozen=True)
class PowerOfAttorney:
    doc_id: str
    sha256: str
    format_version: str | None
    number: str | None
    internal_number: str | None
    issue_date: date | None
    valid_until: date | None
    subdelegation: Subdelegation
    principal: Organization
    signer: Person
    representatives: tuple[Person, ...]
    authorities: tuple[Authority, ...]
    parent_number: str | None
    refs: dict[str, EvidenceRef] = field(default_factory=dict)
    loses_authority: bool | None = None  # ПрУтрПолн: передоверитель утрачивает полномочия (2) или нет (1)
    joint: bool | None = None  # ПрСовмПолн: полномочия осуществляются совместно (2) или индивидуально (1)
    original_number: str | None = None  # номер первоначальной доверенности цепочки
    irrevocable_subdelegation: str | None = None  # Безотзыв/@ПрПерБезДов
    kind_code: str | None = None  # ВидДовер

    def ref(self, key: str) -> EvidenceRef:
        return self.refs.get(key) or EvidenceRef(self.doc_id, self.sha256, "document")

    def effective_until(self) -> date | None:
        """Срок действия: указанный в МЧД, иначе год со дня совершения (п. 1 ст. 186 ГК РФ)."""
        if self.valid_until is not None:
            return self.valid_until
        if self.issue_date is None:
            return None
        try:
            return self.issue_date.replace(year=self.issue_date.year + 1)
        except ValueError:  # 29 февраля
            return self.issue_date.replace(year=self.issue_date.year + 1, day=28)


@dataclass(frozen=True)
class ExecutiveRecord:
    person: Person
    grn: str | None
    record_date: date | None
    valid_from: date | None
    valid_to: date | None
    unreliable: bool = False
    ref: EvidenceRef | None = None

    def active_on(self, day: date) -> bool:
        if self.valid_from and day < self.valid_from:
            return False
        return not (self.valid_to and day > self.valid_to)


@dataclass(frozen=True)
class EgrulSnapshot:
    doc_id: str
    sha256: str
    organization: Organization
    status: str  # active | in_liquidation | liquidated
    executives: tuple[ExecutiveRecord, ...]
    extracted_at: date | None
    full_name: str | None = None

    def executives_on(self, day: date) -> list[ExecutiveRecord]:
        return [rec for rec in self.executives if rec.active_on(day)]


@dataclass(frozen=True)
class TextDocument:
    doc_id: str
    sha256: str
    kind: str  # charter_text | paper_poa_text
    text: str


@dataclass(frozen=True)
class Operation:
    code: str
    on_date: date
    amount_rub: int | None = None
    counterparty_inn: str | None = None


@dataclass(frozen=True)
class IntakeError:
    doc_id: str
    kind: str
    error: str


@dataclass(frozen=True)
class Case:
    case_id: str
    organization: Organization
    requester: Person
    operation: Operation
    poas: tuple[PowerOfAttorney, ...] = ()
    egrul: EgrulSnapshot | None = None
    charter: TextDocument | None = None
    paper_poas: tuple[TextDocument, ...] = ()
    registry: dict[str, str] = field(default_factory=dict)
    intake_errors: tuple[IntakeError, ...] = ()
    inputs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class Finding:
    rule_id: str
    check: str
    effect: LegalEffect
    message: str
    norm_ref: str
    refs: tuple[EvidenceRef, ...]
    rule_status: str = "draft"
    next_step: str | None = None
    unit: str | None = None
    origin: str = "deterministic"

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("finding without rule_id cannot be published")
        if not self.refs:
            raise ValueError(f"{self.rule_id}: finding without evidence refs cannot be published")

    @property
    def finding_id(self) -> str:
        parts = [self.rule_id, self.effect.value, self.message, *(ref.short() for ref in self.refs)]
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "check": self.check,
            "effect": self.effect.value,
            "message": self.message,
            "norm_ref": self.norm_ref,
            "rule_status": self.rule_status,
            "next_step": self.next_step,
            "unit": self.unit,
            "origin": self.origin,
            "refs": [ref.as_dict() for ref in self.refs],
        }
