"""Свёртка находок в вердикт по принципу «при сомнении — не «да»» (ADR-001).

Порядок старшинства: опровержение > недостаточно данных > разбор > подтверждение.
«Не проверено» никогда не превращается в «подтверждено», тишина — тоже:
обязательная группа проверок считается выполненной, только если в ней есть
подтверждающая находка и нет непроверенных.

Паттерн перенесён из AeroBIM (``compute_package_outcome`` и ``SignOffCapabilityPolicy``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mandat.domain.model import Finding


class LegalEffect(StrEnum):
    REFUTES = "refutes"
    NEEDS_REVIEW = "needs_review"
    NOT_VERIFIED = "not_verified"
    SUPPORTS = "supports"
    INFO = "info"


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    NEEDS_REVIEW = "needs_review"
    INSUFFICIENT_DATA = "insufficient_data"


VERDICT_LABEL_RU = {
    Verdict.CONFIRMED: "ПОДТВЕРЖДЕНО",
    Verdict.REFUTED: "ОПРОВЕРГНУТО",
    Verdict.NEEDS_REVIEW: "НА РАЗБОР",
    Verdict.INSUFFICIENT_DATA: "НЕДОСТАТОЧНО ДАННЫХ",
}

CHECK_LABEL_RU = {
    "format": "Формат документа",
    "identity": "Реквизиты и стороны",
    "registry": "Реестр МЧД",
    "term": "Срок действия",
    "eio": "Руководитель по ЕГРЮЛ",
    "coverage": "Покрытие операции",
    "subdelegation": "Передоверие",
    "charter": "Ограничения устава",
    "text": "Текстовые условия",
    "signature": "Электронная подпись",
}


@dataclass(frozen=True)
class Profile:
    """Профиль строгости. ``demo`` честно помечает то, что не проверялось."""

    name: str
    mandatory: frozenset[str]
    waived: frozenset[str]
    trust_draft_rules: bool

    def describe(self) -> str:
        return self.name


PROFILES: dict[str, Profile] = {
    "demo": Profile(
        name="demo",
        mandatory=frozenset({"format", "identity", "registry", "term", "eio", "coverage", "subdelegation"}),
        waived=frozenset({"signature"}),
        trust_draft_rules=True,
    ),
    "strict": Profile(
        name="strict",
        mandatory=frozenset(
            {"format", "identity", "registry", "term", "eio", "coverage", "subdelegation", "signature"}
        ),
        waived=frozenset(),
        trust_draft_rules=False,
    ),
}


def get_profile(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown profile {name!r}; known: {sorted(PROFILES)}") from exc


@dataclass(frozen=True)
class FoldResult:
    verdict: Verdict
    checks: dict[str, str]
    warnings: tuple[str, ...]
    effective: tuple[tuple[str, LegalEffect], ...]


def effective_effect(finding: Finding, profile: Profile) -> LegalEffect:
    """В строгом профиле черновик не закрывает проверку и не считается юридическим опровержением.

    Положительный эффект неутверждённого правила становится «не проверено»:
    обязательная группа так не получает «выполнено». Отрицательный эффект
    становится разбором: повод для человека есть, утверждённого отказа нет.
    """
    if finding.origin != "deterministic":
        return LegalEffect.INFO
    draft = finding.rule_status != "approved" and not profile.trust_draft_rules
    if draft and finding.effect is LegalEffect.REFUTES:
        return LegalEffect.NEEDS_REVIEW
    if draft and finding.effect is LegalEffect.SUPPORTS:
        return LegalEffect.NOT_VERIFIED
    return finding.effect


def profile_gap(finding: Finding, effective: LegalEffect) -> bool:
    """«Не проверено», которое создал сам профиль: черновик не закрыл группу или источник не доверенный.

    Это не пробел в документах кейса. Если рядом есть содержательный повод для разбора, кейс
    уходит человеку на разбор, а не за документами.
    """
    if effective is not LegalEffect.NOT_VERIFIED:
        return False
    if finding.effect is LegalEffect.SUPPORTS:
        return True
    return any(ref.doc_id == "source-trust" for ref in finding.refs)


def _group_status(effects: list[LegalEffect]) -> str:
    if not effects:
        return "missing"
    if LegalEffect.REFUTES in effects:
        return "refuted"
    if LegalEffect.NOT_VERIFIED in effects:
        return "not_verified"
    if LegalEffect.NEEDS_REVIEW in effects:
        return "review"
    if LegalEffect.SUPPORTS in effects:
        return "ok"
    return "info_only"


def fold(findings: Iterable[Finding], profile: Profile, applicable: Iterable[str]) -> FoldResult:
    applicable_set = set(applicable)
    by_check: dict[str, list[LegalEffect]] = {}
    data_gap: set[str] = set()
    reasons = 0  # содержательные поводы для разбора (не пониженные опровержения)
    effective: list[tuple[str, LegalEffect]] = []
    warnings: list[str] = []
    for finding in findings:
        eff = effective_effect(finding, profile)
        effective.append((finding.finding_id, eff))
        by_check.setdefault(finding.check, []).append(eff)
        if eff is LegalEffect.NOT_VERIFIED and not profile_gap(finding, eff):
            data_gap.add(finding.check)
        if eff is LegalEffect.NEEDS_REVIEW and finding.effect is LegalEffect.NEEDS_REVIEW:
            reasons += 1
        if eff is not finding.effect and finding.effect is LegalEffect.REFUTES:
            warnings.append(f"{finding.rule_id}: правило не утверждено юристом, опровержение понижено до разбора")
        if eff is not finding.effect and finding.effect is LegalEffect.SUPPORTS:
            warnings.append(f"{finding.rule_id}: правило не утверждено юристом, подтверждение не закрывает проверку")
        if finding.rule_status != "approved" and profile.trust_draft_rules and finding.effect is LegalEffect.REFUTES:
            warnings.append(f"{finding.rule_id}: опровержение по правилу, не утверждённому юристом (профиль demo)")

    checks: dict[str, str] = {}
    for check in sorted(set(by_check) | applicable_set | set(profile.mandatory)):
        if check not in applicable_set and check not in by_check:
            checks[check] = "not_applicable"
            continue
        status = _group_status(by_check.get(check, []))
        if check in profile.waived and status in {"missing", "not_verified"}:
            status = "waived"
            warnings.append(f"Проверка «{CHECK_LABEL_RU.get(check, check)}» не выполнялась (профиль {profile.name})")
        checks[check] = status

    all_effects = [eff for _, eff in effective]
    mandatory_live = [c for c in profile.mandatory if checks.get(c) not in {"not_applicable", "waived"}]

    gaps = [c for c in mandatory_live if checks.get(c) in {"missing", "not_verified", "info_only"}]
    only_profile = all(checks.get(c) == "not_verified" and c not in data_gap for c in gaps)

    if LegalEffect.REFUTES in all_effects:
        verdict = Verdict.REFUTED
    elif gaps and only_profile and reasons:
        verdict = Verdict.NEEDS_REVIEW
        warnings.append(
            f"Профиль {profile.name} не закрыл проверки ({', '.join(sorted(gaps))}), "
            "но в кейсе есть содержательный повод для разбора: кейс передан человеку"
        )
    elif gaps:
        verdict = Verdict.INSUFFICIENT_DATA
    elif LegalEffect.NOT_VERIFIED in all_effects and any(
        checks.get(c) == "not_verified" for c in checks if c not in profile.waived
    ):
        verdict = Verdict.NEEDS_REVIEW
    elif LegalEffect.NEEDS_REVIEW in all_effects:
        verdict = Verdict.NEEDS_REVIEW
    elif all(checks.get(c) == "ok" for c in mandatory_live) and mandatory_live:
        verdict = Verdict.CONFIRMED
    else:
        verdict = Verdict.INSUFFICIENT_DATA
    return FoldResult(verdict, checks, tuple(dict.fromkeys(warnings)), tuple(effective))
