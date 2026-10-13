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
# Справки, которые сотрудник должен увидеть сразу: они не меняют вердикт, но требуют внимания.
SIGNAL_RULES = frozenset({"R-EIO-002", "R-SUB-008"})


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


def strongest_objection(verdict: Verdict, findings: list[Finding], warnings: list[str]) -> str:
    """Сильнейшее возражение рецензента: почему вердикт ещё нельзя считать окончательным.

    Роль рецензента: перед публикацией назвать самое сильное возражение,
    а не только итог. На подтверждении первым идёт сигнал (давняя смена руководителя,
    длинная цепочка), потом снятая проверка или граница профиля.
    """
    if verdict is Verdict.CONFIRMED:
        signals = sorted(
            (f for f in findings if f.effect is LegalEffect.INFO and f.rule_id in SIGNAL_RULES), key=lambda f: f.rule_id
        )
        if signals:
            return f"{signals[0].rule_id}: {signals[0].message}"
    ranked = [f for f in findings if f.origin == "deterministic" and f.effect in _ORDER]
    if ranked:
        finding = min(ranked, key=lambda f: (_ORDER[f.effect], f.rule_id))
        return f"{finding.rule_id}: {finding.message}"
    if verdict is Verdict.CONFIRMED:
        waived = [w for w in warnings if "не выполнялась" in w]
        if waived:
            return waived[0]
        return "Подтверждение относится к правилам этой версии и этому профилю."
    return "Автоматического вердикта нет: данных или проверки недостаточно."


def route(verdict: Verdict, findings: list[Finding], *, high_risk: bool) -> dict[str, Any]:
    if verdict is Verdict.CONFIRMED:
        return {"queue": "auto_recommendation", "priority": None, "sla_hours": None}
    risky = high_risk or any(f.rule_id in {"R-EIO-002", "R-EIO-003", "R-SUB-001"} for f in findings)
    priority = "P1" if risky else ("P2" if verdict is Verdict.NEEDS_REVIEW else "P3")
    units = sorted({f.unit for f in findings if f.unit and f.effect in _ORDER})
    return {"queue": "exceptions", "priority": priority, "sla_hours": SLA_HOURS[priority], "units": units}
