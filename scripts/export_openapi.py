"""Записать контракт HTTP API в docs/api/openapi.json (идея из KonkovDV/Kontur: contracts/ + проверка дрейфа).

Запуск: ``python scripts/export_openapi.py``; ``--check`` — только сравнить, код 1 при расхождении.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "api" / "openapi.json"


def render() -> str:
    from mandat.api.app import create_app

    app = create_app(token="contract", docs=True)  # noqa: S106 - не секрет: схема от токена не зависит
    spec = app.openapi()
    spec["info"].pop("version", None)  # версия пакета меняется на каждом релизе и не часть контракта
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != text:
            print("docs/api/openapi.json устарел: python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print("контракт API совпадает с docs/api/openapi.json")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"записано: {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
