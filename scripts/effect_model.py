"""Калькулятор годового эффекта пилота. Значений по умолчанию нет: каждый параметр вводится явно.

    python scripts/effect_model.py --cases 12000 --coverage 0.55 --t-manual 25 --t-auto 5 --rate 900 \\
        --return-share 0.3 --return-reduction 0.5 --return-cost 1500

Формула и источники параметров — docs/ECONOMICS.md. Сценарий предотвращённых инцидентов
считается отдельно и в итог по умолчанию не входит.

    python scripts/effect_model.py --scenarios

печатает три сценария слайда 9 (ставки и предотвращённый ущерб) по параметрам из
src/mandat/data/effect_scenarios.json — тот же файл читает submission/deck/build_deck.js.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCENARIOS = Path(__file__).resolve().parents[1] / "src" / "mandat" / "data" / "effect_scenarios.json"


def scenarios(path: Path = SCENARIOS) -> list[dict[str, object]]:
    """Сценарии слайда 9. Минуты считаются на кейс, включая разбор, поэтому множителя покрытия нет."""
    p = json.loads(path.read_text(encoding="utf-8"))
    loss_per_incident = p["lossPerQuarterMln"] / p["incidentsPerQuarter"]
    out = []
    for i, name in enumerate(p["names"]):
        fte = p["clients"] * p["eventShare"][i] * p["minutesSaved"][i] / 60 / p["hoursPerFte"]
        incidents = p["incidentsPerQuarter"] * 4 * p["vtbShare"][i] * p["authorityShare"][i]
        out.append(
            {
                "scenario": name,
                "fte": round(fte),
                "prevented_loss_mln_rub": round(incidents * loss_per_incident),
                "incidents_per_year": round(incidents, 2),
                "params": {
                    "clients": p["clients"],
                    "event_share": p["eventShare"][i],
                    "minutes_saved": p["minutesSaved"][i],
                    "hours_per_fte": p["hoursPerFte"],
                    "vtb_share": p["vtbShare"][i],
                    "authority_share": p["authorityShare"][i],
                },
            }
        )
    return out


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
    # Отрицательная разница сохраняется: если инструмент медленнее ручной работы, эффект отрицательный.
    time_saving = cases * coverage * (t_manual - t_auto) / 60 * rate
    returns_saving = cases * return_share * return_reduction * return_cost
    incident_scenario = incidents * incident_loss
    return {
        "time_saving_rub": round(time_saving),
        "returns_saving_rub": round(returns_saving),
        "operational_total_rub": round(time_saving + returns_saving),
        "incident_scenario_rub": round(incident_scenario),
    }


def main() -> int:
    import sys

    if "--scenarios" in sys.argv[1:]:
        print(json.dumps(scenarios(), ensure_ascii=False, indent=2))
        return 0
    p = argparse.ArgumentParser(description="годовой эффект пилота «Мандата»; --scenarios — сценарии слайда 9")
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
