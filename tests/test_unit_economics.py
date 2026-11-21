"""Юнит-экономика одной проверки: таблица в docs/ECONOMICS.md совпадает с расчётом по значениям финансовой модели."""

from __future__ import annotations

import re
import sys

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
from effect_model import unit_economics  # noqa: E402

ROWS = unit_economics()


def _table() -> dict[str, list[int]]:
    text = (ROOT / "docs" / "ECONOMICS.md").read_text(encoding="utf-8")
    section = text.split("## Юнит-экономика одной проверки", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[0] in {r["scenario"] for r in ROWS}:
            out[cells[0]] = [int(re.sub(r"\D", "", c)) for c in cells[1:]]
    return out


def test_documented_table_matches_the_model():
    table = _table()
    assert set(table) == {r["scenario"] for r in ROWS}
    for row in ROWS:
        assert table[row["scenario"]] == [
            row["checks_per_year"],
            row["manual_check_rub"],
            row["mandat_cost_per_check_rub"],
            row["subscription_per_check_rub"],
            row["prevented_loss_per_check_rub"],
            row["cash_effect_per_check_rub"],
        ], row["scenario"]


def test_base_scenario_is_what_the_claims_registry_says():
    base = next(r for r in ROWS if r["scenario"] == "Базовый")
    assert base["manual_check_rub"] == 542 and base["mandat_cost_per_check_rub"] == 64
    assert base["manual_to_mandat"] == 8.5
    registry = (ROOT / "CLAIMS_REGISTRY.md").read_text(encoding="utf-8")
    assert "около 540 ₽" in registry and "около 64 ₽" in registry and "8,5 раза" in registry


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["scenario"])
def test_unit_values_are_consistent(row):
    assert row["subscription_per_check_rub"] <= row["mandat_cost_per_check_rub"]
    assert row["manual_check_rub"] > row["mandat_cost_per_check_rub"]
    assert row["checks_per_year"] > 0
