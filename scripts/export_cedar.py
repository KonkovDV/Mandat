"""Экспорт ядра правил передоверия в Cedar: policies/cedar/mandat.cedar и mandat.cedarschema.

    python scripts/export_cedar.py

Проверка схемы и сравнение решений с «Мандатом» — tests/test_cedar.py (нужен extra [cedar]).
"""

from __future__ import annotations

from pathlib import Path

from mandat.rules.cedar_export import export_policies, export_schema

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "policies" / "cedar"


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "mandat.cedar").write_text(export_policies(), encoding="utf-8")
    (TARGET / "mandat.cedarschema").write_text(export_schema(), encoding="utf-8")
    print(f"записано: {TARGET / 'mandat.cedar'}, {TARGET / 'mandat.cedarschema'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
