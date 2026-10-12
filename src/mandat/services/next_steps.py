"""Что делать дальше: список недостающих доказательств и маршрут кейса.

Клиент и сотрудник видят не «ошибка», а конкретный документ, который нужен,
и подразделение, которое ведёт кейс. Сроки SLA — заглушки до калибровки с банком;
оптимизацию очереди позже можно взять из SynAPS (диспетчер ATCS).
"""

from __future__ import annotations

from typing import Any

from mandat.domain.model import Finding
from mandat.domain.verdict import LegalEffect, Verdict

_ORDER = {LegalEffect.REFUTES: 0, LegalEffect.NOT_VERIFIED: 1, LegalEffect.NEEDS_REVIEW: 2}
SLA_HOURS = {"P1": 4, "P2": 24, "P3": 72}


def build_next_steps(findings: list[Finding], verdict: Verdict) -> list[dict[str, Any]]:
    if verdict is Verdict.CONFIRMED:
        return []
    steps: dict[tuple[str, str | None], dict[str, Any]] = {}
    for finding in sorted(findings, key=lambda f: _ORDER.get(f.effect, 9)):
        if finding.effect not in _ORDER or not finding.next_step:
            continue
        key = (finding.next_step, finding.unit)
        row = steps.setdefault(
            key,
            {
                "action": finding.next_step,
                "unit": finding.unit,
                "because": [],
                "effect": finding.effect.value,
            },
        )
        row["because"].append(
            {"rule_id": finding.rule_id, "finding_id": finding.finding_id, "message": finding.message}
        )
    return list(steps.values())


def route(verdict: Verdict, findings: list[Finding], *, high_risk: bool) -> dict[str, Any]:
    if verdict is Verdict.CONFIRMED:
        return {"queue": "auto_recommendation", "priority": None, "sla_hours": None}
    risky = high_risk or any(f.rule_id in {"R-EIO-002", "R-EIO-003", "R-SUB-001"} for f in findings)
    priority = "P1" if risky else ("P2" if verdict is Verdict.NEEDS_REVIEW else "P3")
    units = sorted({f.unit for f in findings if f.unit and f.effect in _ORDER})
    return {"queue": "exceptions", "priority": priority, "sla_hours": SLA_HOURS[priority], "units": units}
