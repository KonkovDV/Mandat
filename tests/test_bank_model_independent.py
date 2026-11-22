"""Независимая сверка финансовой модели: лист «Банк» пересчитан здесь заново, без кода модели и калькулятора.

Модель считает LibreOffice по формулам Excel (submission/analytics/build_model.py), калькулятор сайта — свой код
(site/js/economics.js). Здесь те же величины выведены третьим способом прямо из входов модели по формулам
docs/ECONOMICS.md, и проверены тождества, на которые опираются слайды: ущерб не зависит от числа операций,
окупаемость — месяц, где накопленные деньги банка переходят через ноль, отдача — чистые деньги на рубль затрат.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALUES = json.loads((ROOT / "submission" / "analytics" / "out" / "model_values.json").read_text(encoding="utf-8"))
BANK = VALUES["bank"]
EFFECT = json.loads((ROOT / "src" / "mandat" / "data" / "effect_scenarios.json").read_text(encoding="utf-8"))
SCENARIOS = range(len(VALUES["scenarios"]))

# Банк России, операции без согласия у юрлиц: (операций, млн руб.) за I, II и III кварталы 2026 (M-07, M-15, M-52).
CBR_QUARTERS = [(818, 573.0), (780, 831.4), (931, 695.1)]


def recompute(i: int) -> dict[str, float]:
    """Лист «Банк» из входов сценария i. Деньги — млн руб., кейсы и часы — тысячи."""
    b = {k: v[i] for k, v in BANK.items()}
    events = b["clients"] * 1000 * b["event_share"]
    hours = events * b["minutes"] / 60
    fte = hours * 1000 / b["hours_fte"]
    cost_fte = b["salary_ops"] * 12 * (1 + b["bank_contrib"]) * (1 + b["overhead"]) / 1000
    capacity = fte * cost_fte
    cash_saving = capacity * b["realization"]
    loss_op = b["loss_q"] / b["incidents_q"]
    prevented = b["incidents_q"] * 4 * b["vtb_share"] * b["authority_share"] * loss_op
    bank_loss = prevented * b["bank_loss_share"]
    effect_cash = cash_saving + bank_loss
    # Сопровождение и поддержка — услуга с НДС 22 % (банк не принимает его к вычету, п. 5 ст. 170 НК РФ);
    # плата за клиентов — лицензия на ПО из реестра, без НДС (пп. 26 п. 2 ст. 149 НК РФ).
    sub_vtb = b["fixed_t1"] * (1 + b["vat"]) + b["clients"] * b["price_client"]
    integration = b["bank_integration_py"] * b["bank_it_cost"]
    one_off = b["impl_t1"] * (1 + b["vat"]) + integration + b["bank_infra"] + b["bank_security"]
    annual_cost = sub_vtb + b["bank_support_py"] * b["bank_it_cost"]
    y1 = effect_cash * b["ramp"] - annual_cost
    y2 = effect_cash - annual_cost
    return {
        "events": events,
        "hours": hours,
        "fte": fte,
        "cost_fte": cost_fte,
        "capacity": capacity,
        "cash_saving": cash_saving,
        "loss_op": loss_op,
        "prevented": prevented,
        "bank_loss": bank_loss,
        "effect_cash": effect_cash,
        "sub_vtb": sub_vtb,
        "one_off": one_off,
        "annual_cost": annual_cost,
        "y1_cash": y1,
        "y2_cash": y2,
    }


def cumulative(i: int, month: float) -> float:
    """Накопленные чистые деньги банка к месяцу: в месяц 0 — разовые затраты, в первый год — эффект с раскачкой."""
    b = {k: v[i] for k, v in BANK.items()}
    if month <= 12:
        return -b["one_off"] + month * b["y1_cash"] / 12
    return -b["one_off"] + b["y1_cash"] + (month - 12) * b["y2_cash"] / 12


@pytest.mark.parametrize("i", SCENARIOS)
def test_bank_sheet_recomputed_from_inputs_matches_the_model(i):
    """Формулы Excel в модели связаны правильно: третий, независимый пересчёт даёт те же значения."""
    for key, value in recompute(i).items():
        assert value == pytest.approx(BANK[key][i], rel=1e-9, abs=1e-9), (VALUES["scenarios"][i], key)


@pytest.mark.parametrize("i", SCENARIOS)
def test_prevented_loss_does_not_depend_on_the_number_of_operations(i):
    """Ущерб = 4 квартала × потери за квартал × доля ВТБ × доля случаев с полномочиями: число операций сокращается."""
    b = {k: v[i] for k, v in BANK.items()}
    assert b["prevented"] == pytest.approx(4 * b["loss_q"] * b["vtb_share"] * b["authority_share"], rel=1e-12)


@pytest.mark.parametrize("i", SCENARIOS)
def test_payback_is_where_the_cumulative_cash_crosses_zero(i):
    """Окупаемость на слайдах — точка, где линия «Деньги банка по месяцам» на сайте пересекает ноль."""
    payback = BANK["payback_cash"][i]
    assert cumulative(i, payback) == pytest.approx(0, abs=1e-6)
    assert cumulative(i, payback - 0.05) < 0 < cumulative(i, payback + 0.05)


@pytest.mark.parametrize("i", SCENARIOS)
def test_roi_is_net_cash_per_ruble_spent_over_three_years(i):
    b = {k: v[i] for k, v in BANK.items()}
    spent = b["one_off"] + 3 * b["annual_cost"]
    net = b["y1_cash"] + 2 * b["y2_cash"] - b["one_off"]
    assert b["roi_cash"] == pytest.approx(net / spent, rel=1e-12)
    # Чистые деньги за три года — та же точка, где кривая «Деньги банка по месяцам» стоит через 36 месяцев.
    assert cumulative(i, 36) == pytest.approx(net, rel=1e-12)


def test_effect_base_is_the_average_of_three_quarters_of_bank_of_russia_data():
    ops = sum(q[0] for q in CBR_QUARTERS)
    loss = sum(q[1] for q in CBR_QUARTERS)
    assert EFFECT["incidentsPerQuarter"] == ops / 3
    assert EFFECT["lossPerQuarterMln"] == pytest.approx(loss / 3, abs=0.05)
    for i in SCENARIOS:
        assert BANK["incidents_q"][i] == EFFECT["incidentsPerQuarter"]
        assert BANK["loss_q"][i] == EFFECT["lossPerQuarterMln"]


def test_rounded_numbers_in_documents_match_the_model():
    """42 / 140 / 280 млн руб. и 33 / 75 / 167 ставок — в реестре, экономике и ответах жюри те же, что в модели."""
    prevented = [round(v) for v in BANK["prevented"]]
    fte = [round(v) for v in BANK["fte"]]
    triple = " / ".join(str(v) for v in prevented)
    registry = (ROOT / "CLAIMS_REGISTRY.md").read_text(encoding="utf-8")
    jury = (ROOT / "docs" / "JURY.md").read_text(encoding="utf-8")
    economics = (ROOT / "docs" / "ECONOMICS.md").read_text(encoding="utf-8")
    assert f"Сценарий предотвращённого ущерба: {triple} млн руб. в год" in registry
    assert f"| Предотвращённый ущерб | {triple} млн руб. в год |" in jury
    row = r"^\| (Консервативный|Базовый|Оптимистичный) \|.*\| (\d+) \| [\d ]+% \| [\d ]+% \| (\d+) \|$"
    rows = re.findall(row, economics, re.M)
    assert [(int(r[1]), int(r[2])) for r in rows] == list(zip(fte, prevented, strict=True))
