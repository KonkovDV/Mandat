"""Прогон движка по синтетическому корпусу и метрики.

Главная защитная метрика — доля ложных «подтверждено» среди кейсов, где
подтверждения быть не должно, с верхней границей интервала Вилсона 95%.
Для системы с правом воздержаться («на разбор», «недостаточно данных») считаем
пару «риск — покрытие» (selective classification): покрытие — доля кейсов с
автоматическим вердиктом, риск — доля ошибок среди них.
Синтетика проверяет логику правил; точность на реальных документах измеряется
только на пилоте (docs/EVALUATION.md).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mandat.eval.metrics import required_n, wilson_interval
from mandat.eval.synth import DEFECTS, build
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case


def run_corpus(n_per_defect: int = 3, *, seed: int = 7, profile: str = "demo") -> dict[str, Any]:
    rows = []
    for defect, expected in DEFECTS.items():
        for k in range(n_per_defect):
            scenario = build(defect, seed=seed * 1000 + k)
            report = verify_case(load_case_inline(scenario.inline_manifest()), profile=profile)
            rows.append(
                {
                    "case_id": scenario.case_id,
                    "defect": defect,
                    "expected": expected,
                    "predicted": report.verdict.value,
                }
            )

    total = len(rows)
    correct = sum(r["expected"] == r["predicted"] for r in rows)
    negatives = [r for r in rows if r["expected"] != "confirmed"]
    false_confirmed = sum(r["predicted"] == "confirmed" for r in negatives)
    refute_rows = [r for r in rows if r["expected"] == "refuted"]
    refute_hits = sum(r["predicted"] == "refuted" for r in refute_rows)
    abstained = sum(r["predicted"] in {"needs_review", "insufficient_data"} for r in rows)
    auto = [r for r in rows if r["predicted"] in {"confirmed", "refuted"}]
    auto_errors = sum(r["predicted"] != r["expected"] for r in auto)
    confusion = Counter((r["expected"], r["predicted"]) for r in rows)
    return {
        "profile": profile,
        "cases": total,
        "defect_types": len(DEFECTS),
        "agreement": correct / total,
        "agreement_wilson95": wilson_interval(correct, total),
        "false_confirmed": false_confirmed,
        "false_confirmed_rate_upper95": wilson_interval(false_confirmed, len(negatives))[1],
        "refute_recall": refute_hits / len(refute_rows) if refute_rows else None,
        "refute_recall_wilson95": wilson_interval(refute_hits, len(refute_rows)),
        "abstention_rate": abstained / total,
        "coverage": len(auto) / total,
        "auto_risk": auto_errors / len(auto) if auto else None,
        "auto_risk_upper95": wilson_interval(auto_errors, len(auto))[1] if auto else None,
        "n_for_recall_0_9_pm_0_05": required_n(0.9, 0.05),
        "confusion": {f"{e}->{p}": c for (e, p), c in sorted(confusion.items())},
        "mismatches": [r for r in rows if r["expected"] != r["predicted"]],
        "boundary": "синтетический корпус: проверка логики правил, не точность на документах банка",
    }
