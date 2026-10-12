"""Вторая свёртка вердикта, написанная отдельно от ``verdict.fold``.

Паттерн SynAPS-ProgramPlan: план, который посчитал решатель, проходит проверку,
не использующую код решателя. Здесь оракул заново собирает статус групп и
принимает решение таблицей рангов. Расхождение с ``fold`` — ошибка теста,
а не повод молча выбрать один из двух ответов.
"""

from __future__ import annotations

from collections.abc import Iterable

from mandat.domain.model import Finding
from mandat.domain.verdict import LegalEffect, Profile, Verdict, effective_effect

_RANK = {
    LegalEffect.REFUTES: "refuted",
    LegalEffect.NOT_VERIFIED: "not_verified",
    LegalEffect.NEEDS_REVIEW: "review",
    LegalEffect.SUPPORTS: "ok",
}
_RANK_ORDER = {"refuted": 0, "not_verified": 1, "review": 2, "ok": 3}


def _group(effects: list[LegalEffect]) -> str:
    labels = [_RANK[effect] for effect in effects if effect in _RANK]
    if not effects:
        return "missing"
    if not labels:
        return "info_only"
    return min(labels, key=_RANK_ORDER.__getitem__)


def oracle_verdict(findings: Iterable[Finding], profile: Profile, applicable: Iterable[str]) -> Verdict:
    applicable_set = set(applicable)
    by_check: dict[str, list[LegalEffect]] = {}
    effects: list[LegalEffect] = []
    for finding in findings:
        effect = effective_effect(finding, profile)
        effects.append(effect)
        by_check.setdefault(finding.check, []).append(effect)

    checks: dict[str, str] = {}
    for check in set(by_check) | applicable_set | set(profile.mandatory):
        if check not in applicable_set and check not in by_check:
            checks[check] = "not_applicable"
            continue
        status = _group(by_check.get(check, []))
        if check in profile.waived and status in {"missing", "not_verified"}:
            status = "waived"
        checks[check] = status

    live = [check for check in profile.mandatory if checks.get(check) not in {"not_applicable", "waived"}]
    if LegalEffect.REFUTES in effects:
        return Verdict.REFUTED
    if any(checks.get(check) in {"missing", "not_verified", "info_only"} for check in live):
        return Verdict.INSUFFICIENT_DATA
    if LegalEffect.NOT_VERIFIED in effects and any(
        status == "not_verified" for name, status in checks.items() if name not in profile.waived
    ):
        return Verdict.NEEDS_REVIEW
    if LegalEffect.NEEDS_REVIEW in effects:
        return Verdict.NEEDS_REVIEW
    if live and all(checks.get(check) == "ok" for check in live):
        return Verdict.CONFIRMED
    return Verdict.INSUFFICIENT_DATA
