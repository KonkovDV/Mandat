"""Калькулятор годового эффекта пилота. Значений по умолчанию нет: каждый параметр вводится явно.

    python scripts/effect_model.py --cases 12000 --coverage 0.55 --t-manual 25 --t-auto 5 --rate 900 \\
        --return-share 0.3 --return-reduction 0.5 --return-cost 1500

Формула и источники параметров — docs/ECONOMICS.md. Сценарий предотвращённых инцидентов
считается отдельно и в итог по умолчанию не входит.
"""

from __future__ import annotations

import argparse
import json


def effect(
    *,
    cases: int,
    coverage: float,
    t_manual: float,
    t_auto: float,
    rate: float,
    return_share: float = 0.0,
    return_reduction: float = 0.0,
    return_cost: float = 0.0,
    incidents: float = 0.0,
    incident_loss: float = 0.0,
) -> dict[str, float]:
    if not 0 <= coverage <= 1 or not 0 <= return_share <= 1 or not 0 <= return_reduction <= 1:
        raise ValueError("доли задаются от 0 до 1")
    time_saving = cases * coverage * max(t_manual - t_auto, 0) / 60 * rate
    returns_saving = cases * return_share * return_reduction * return_cost
    incident_scenario = incidents * incident_loss
    return {
        "time_saving_rub": round(time_saving),
        "returns_saving_rub": round(returns_saving),
        "operational_total_rub": round(time_saving + returns_saving),
        "incident_scenario_rub": round(incident_scenario),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="годовой эффект пилота «Мандата»")
    p.add_argument("--cases", type=int, required=True, help="кейсов в год")
    p.add_argument("--coverage", type=float, required=True, help="доля автоматических вердиктов, 0..1")
    p.add_argument("--t-manual", type=float, required=True, help="минут на кейс вручную")
    p.add_argument("--t-auto", type=float, required=True, help="минут на кейс с «Мандатом»")
    p.add_argument("--rate", type=float, required=True, help="стоимость часа сотрудника, руб.")
    p.add_argument("--return-share", type=float, default=0.0)
    p.add_argument("--return-reduction", type=float, default=0.0)
    p.add_argument("--return-cost", type=float, default=0.0)
    p.add_argument("--incidents", type=float, default=0.0, help="сценарий: предотвращённых инцидентов в год")
    p.add_argument("--incident-loss", type=float, default=0.0, help="сценарий: средний ущерб, руб.")
    args = p.parse_args()
    result = effect(
        cases=args.cases,
        coverage=args.coverage,
        t_manual=args.t_manual,
        t_auto=args.t_auto,
        rate=args.rate,
        return_share=args.return_share,
        return_reduction=args.return_reduction,
        return_cost=args.return_cost,
        incidents=args.incidents,
        incident_loss=args.incident_loss,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
