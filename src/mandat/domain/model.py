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

    def _pairs(self, other: Person) -> list[bool]:
        """Сравнимые пары сильных идентификаторов.

        Пара сравнима, только если обе стороны дают правильно сформированное значение:
        ИНН из 10 или 12 цифр ASCII, СНИЛС из 11 цифр ASCII после удаления пробелов и дефисов.
        Неправильно сформированное значение не сравнивается (в том числе не «совпадает» с другим
        неправильным значением, которое после очистки стало пустой строкой).
        """
        from mandat.domain.identifiers import comparable_inn, comparable_snils

        out = []
        a, b = comparable_inn(self.inn), comparable_inn(other.inn)
        if a and b:
            out.append(a == b)
        a, b = comparable_snils(self.snils), comparable_snils(other.snils)
        if a and b:
            out.append(a == b)
        return out

    def match_status(self, other: Person) -> str:
        """``match``, ``mismatch``, ``conflict`` или ``insufficient``. ФИО в сравнении не участвует."""
        pairs = self._pairs(other)
        if not pairs:
            return "insufficient"
        if all(pairs):
            return "match"
        if not any(pairs):
            return "mismatch"
        return "conflict"

    def same_as(self, other: Person) -> bool | None:
        """Одно лицо, только если совпали все сравнимые идентификаторы, указанные с обеих сторон.

        True — все совпали; False — ни один не совпал; None — сравнить нечем (ФИО не доказательство)
        или идентификаторы противоречат друг другу (ИНН одного человека, СНИЛС другого).
        """
        status = self.match_status(other)
        return {"match": True, "mismatch": False}.get(status)

    def conflicts_with(self, other: Person) -> bool:
        """ИНН и СНИЛС указывают на разных людей: один идентификатор совпал, другой нет."""
        return self.match_status(other) == "conflict"

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
    format_gaps: tuple[str, ...] = ()  # обязательные по формату 003 поля, которых нет
    format_questions: tuple[str, ...] = ()  # поля, обязательность которых не установлена: решает юрист
    representative_kinds: tuple[str, ...] = ()  # вид каждого представителя по элементу внутри Пред
    notarized: bool = False  # в МЧД есть сведения о нотариальном удостоверении (СвНотУд)
    policy_gaps: tuple[str, ...] = ()  # по схеме поле необязательно, но без него проверку не провести
    subdelegator_kind: str | None = None  # вид передоверителя по самой МЧД (ветвь Передов), иначе None

    def ref(self, key: str) -> EvidenceRef:
        return self.refs.get(key) or EvidenceRef(self.doc_id, self.sha256, "document")

    def effective_until(self) -> date | None:
        """Срок действия: указанный в МЧД, иначе год со дня совершения (п. 1 ст. 186 ГК РФ)."""
        if self.valid_until is not None:
            return self.valid_until
        if self.issue_date is None:
            return None
        if self.issue_date.year >= 9999:
            return None  # срок за пределами календаря: неизвестен, а не «бессрочно»
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
        """Известный интервал. Нет даты начала — история не доказана, запись не действует.

        Открытый конец (есть valid_from и нет valid_to) — известный действующий период.
        Это не срок годности выписки и не догадка о прошлом.
        """
        if self.valid_from is None:
            return False
        if day < self.valid_from:
            return False
        return not (self.valid_to is not None and day > self.valid_to)


@dataclass(frozen=True)
class EgrulSnapshot:
    doc_id: str
    sha256: str
    organization: Organization
    status: str  # active | in_liquidation | liquidated | unknown
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
    registry: dict[str, str | dict[str, str]] = field(default_factory=dict)  # номер -> статус или снимок
    bankruptcy: dict[str, str] = field(default_factory=dict)
    intake_errors: tuple[IntakeError, ...] = ()
    inputs: tuple[dict[str, str], ...] = ()
    charter_disposition: str | None = None
    # Форма передоверия (п. 3 ст. 187 ГК РФ) со слов клиента: номер дочерней МЧД -> форма
    subdelegation_forms: dict[str, str] = field(default_factory=dict)
    duplicates: tuple[tuple[str, str], ...] = ()  # (doc_id, kind) второго документа вида, который должен быть один


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
