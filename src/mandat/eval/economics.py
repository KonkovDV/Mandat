"""Юнит-экономика одной проверки полномочий по значениям финансовой модели пакета аналитики.

Проверка — один кейс смены или добавления подписанта. Ручная проверка = минуты на кейс × стоимость минуты
сотрудника (полная стоимость ставки / рабочие минуты ставки в год). Затраты банка на «Мандат» — подписка и
сопровождение за год на число проверок в год. Ущерб и денежный эффект — годовые значения модели на одну проверку.
Все исходные величины — из модели (``submission/analytics/out/model_values.json``), здесь только деление: формулы и
допущения живут там. Расчёт один на всех: ``scripts/effect_model.py --unit`` (docs/ECONOMICS.md, сайт) и вкладка
«Аналитика» консоли стенда.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def unit_economics(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Строка на сценарий модели по листу «Эффект для банка»: ``values`` — содержимое ``model_values.json``."""
    bank = values["bank"]
    out: list[dict[str, Any]] = []
    for i, name in enumerate(values["scenarios"]):
        checks = bank["events"][i] * 1000  # в модели — тысячи кейсов в год
        per_minute = bank["cost_fte"][i] * 1e6 / (bank["hours_fte"][i] * 60)
        manual = bank["minutes"][i] * per_minute
        mandat = bank["annual_cost"][i] * 1e6 / checks
        out.append(
            {
                "scenario": name,
                "checks_per_year": round(checks),
                "manual_check_rub": round(manual),
                "mandat_cost_per_check_rub": round(mandat),
                "subscription_per_check_rub": round(bank["sub_vtb"][i] * 1e6 / checks),
                "prevented_loss_per_check_rub": round(bank["prevented"][i] * 1e6 / checks),
                "cash_effect_per_check_rub": round(bank["effect_cash"][i] * 1e6 / checks),
                "manual_to_mandat": round(manual / mandat, 1),
                "model": {"version": values["version"], "as_of": values["as_of"]},
            }
        )
    return out
