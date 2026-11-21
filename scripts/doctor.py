"""Проверка окружения перед демо: версия Python, зависимости, пакет правил, демо-кейсы."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    problems: list[str] = []
    ok: list[str] = []

    if sys.version_info < (3, 11):  # noqa: UP036 — doctor запускают и на старом Python
        problems.append(f"нужен Python 3.11+, сейчас {sys.version.split()[0]}")
    else:
        ok.append(f"Python {sys.version.split()[0]}")

    modules = {
        "defusedxml": "pip install -e .",
        "cryptography": "pip install -e .",
        "fastapi": "pip install -e '.[api]' (только для API)",
        "pytest": "pip install -e '.[dev]'",
    }
    optional = {"cedarpy": "pip install -e '.[cedar]' (независимая проверка правил на Cedar)"}
    for mod, hint in modules.items():
        if importlib.util.find_spec(mod):
            ok.append(mod)
        else:
            problems.append(f"нет модуля {mod}: {hint}")

    for mod, hint in optional.items():
        ok.append(mod if importlib.util.find_spec(mod) else f"{mod} не установлен (необязательно): {hint}")

    try:
        from mandat.rules.pack import default_pack

        pack = default_pack()
        ok.append(f"пакет правил: {len(pack.rules)} правил")
    except Exception as exc:
        problems.append(f"пакет правил не загружается: {exc}")

    from mandat.examples import DEMO

    cases = sorted((ROOT / "examples" / "cases").glob("*/case.json"))
    (ok if len(cases) == len(DEMO) else problems).append(f"демо-кейсов: {len(cases)} (ожидается {len(DEMO)})")

    for line in ok:
        print(f"  ок   {line}")
    for line in problems:
        print(f"  !!   {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
