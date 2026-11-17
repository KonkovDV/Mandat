"""Минимальное мутационное тестирование без зависимостей: точечные мутации операторов в одном модуле.

    python tools/mutation/mini_mutate.py src/mandat/intake/xpath.py tests/test_xpath_properties.py

Каждая мутация применяется к копии исходника на месте, запускаются указанные тесты; упавшие тесты —
мутант «убит». Исходник всегда восстанавливается. Это грубый аналог mutmut для быстрой оценки силы тестов.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SWAPS = [
    (r" == ", " != "),
    (r" != ", " == "),
    (r" > ", " >= "),
    (r" < ", " <= "),
    (r" >= ", " > "),
    (r" <= ", " < "),
    (r"\bis None\b", "is not None"),
    (r"\bis not None\b", "is None"),
    (r"\bif not ", "if "),
    (r"\band\b", "or"),
    (r"\bor\b", "and"),
    (r"\bTrue\b", "False"),
    (r"\.strip\(\)", ""),
    (r"\[1:\]", "[0:]"),
    (r"\+ 1\b", "+ 2"),
]


def mutants(source: str):
    lines = source.splitlines(keepends=True)
    for number, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("#", '"""', "from ", "import ")) or not stripped.strip():
            continue
        for pattern, repl in SWAPS:
            for match in re.finditer(pattern, line):
                mutated = line[: match.start()] + re.sub(pattern, repl, match.group(0)) + line[match.end() :]
                yield number + 1, line.strip(), "".join(lines[:number] + [mutated] + lines[number + 1 :])


def main(argv: list[str]) -> int:
    target, tests = Path(argv[1]), argv[2:]
    original = target.read_text(encoding="utf-8")
    killed, survived = 0, []
    try:
        for line_no, text, mutated in mutants(original):
            target.write_text(mutated, encoding="utf-8")
            proc = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "pytest", "-q", "-x", "-o", "addopts=", "-p", "no:cacheprovider", *tests],
                capture_output=True,
            )
            if proc.returncode == 0:
                survived.append(f"{target}:{line_no}: {text}")
            else:
                killed += 1
    finally:
        target.write_text(original, encoding="utf-8")
    total = killed + len(survived)
    print(f"мутантов: {total}, убито: {killed}, выжило: {len(survived)}, score: {killed / max(total, 1):.0%}")
    for item in survived:
        print("  выжил:", item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
