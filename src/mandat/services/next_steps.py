"""Что делать дальше: список недостающих доказательств и маршрут кейса.

Клиент и сотрудник видят не «ошибка», а конкретный документ, который нужен,
и подразделение, которое ведёт кейс. Сроки SLA — заглушки до калибровки с банком;
оптимизацию очереди позже можно взять из SynAPS (диспетчер ATCS).
"""

from __future__ import annotations

import json
import os
from typing import Any

from mandat.domain.model import Finding
from mandat.domain.verdict import LegalEffect, Verdict

_ORDER = {LegalEffect.REFUTES: 0, LegalEffect.NOT_VERIFIED: 1, LegalEffect.NEEDS_REVIEW: 2}
_DRIVER = {
    Verdict.REFUTED: LegalEffect.REFUTES,
    Verdict.NEEDS_REVIEW: LegalEffect.NEEDS_REVIEW,
    Verdict.INSUFFICIENT_DATA: LegalEffect.NOT_VERIFIED,
}
_GENERIC = frozenset({"R-CHR-002"})
# Сроки разбора по умолчанию. Не из норм: реальные значения задаёт банк в MANDAT_REVIEW_SLA_HOURS
# (JSON, например {"P1": 2, "P2": 8}); не заданные приоритеты берут значения отсюда. docs/REVIEW.md.
SLA_HOURS: dict[str, float] = {"P1": 4, "P2": 24, "P3": 72}
SLA_ENV = "MANDAT_REVIEW_SLA_HOURS"


def sla_hours(priority: str) -> float:
    """Срок разбора в часах для приоритета: значение из MANDAT_REVIEW_SLA_HOURS или по умолчанию."""
    raw = os.environ.get(SLA_ENV, "").strip()
    table = dict(SLA_HOURS)
    if raw:
        try:
            override = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{SLA_ENV}: не JSON") from exc
        if not isinstance(override, dict) or not set(override) <= set(SLA_HOURS):
            raise ValueError(f"{SLA_ENV}: ожидался объект с ключами из {sorted(SLA_HOURS)}")
        for key, value in override.items():
            if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 24 * 365:
                raise ValueError(f"{SLA_ENV}: {key} — число часов больше 0 и не больше года")
            table[key] = value
    return table[priority]


def check_sla() -> None:
    """Проверить MANDAT_REVIEW_SLA_HOURS при старте: неверное значение — ValueError, а не 500 на запросах."""
    for priority in SLA_HOURS:
        sla_hours(priority)


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
        # Сначала находка того же вида, что и вердикт: она и есть настоящая причина.
        # Неразмеченный устав (R-CHR-002) есть почти в каждом кейсе, поэтому он идёт последним из равных.
        # Снятая в профиле проверка (например, подпись в demo) не должна заслонять её.
        driver = _DRIVER.get(verdict)
        finding = min(
            ranked, key=lambda f: (f.effect is not driver, _ORDER[f.effect], f.rule_id in _GENERIC, f.rule_id)
        )
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
    return {"queue": "exceptions", "priority": priority, "sla_hours": sla_hours(priority), "units": units}
