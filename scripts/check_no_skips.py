"""Падает, если в отчёте JUnit есть пропущенные тесты (T-2).

Защитные тесты не должны тихо пропускаться из-за отсутствующего extra или окружения:
в CI ставятся все extras, поэтому любой skip здесь означает, что проверка не выполнилась.

    python -m pytest --junitxml=var/junit.xml && python scripts/check_no_skips.py var/junit.xml
"""

from __future__ import annotations

import sys
from xml.etree.ElementTree import parse  # noqa: S405 - отчёт pytest, созданный в этом же прогоне


def skipped(path: str) -> list[str]:
    root = parse(path).getroot()  # noqa: S314
    return [
        f"{case.get('classname')}::{case.get('name')}"
        for case in root.iter("testcase")
        if case.find("skipped") is not None
    ]


def main(argv: list[str]) -> int:
    found = skipped(argv[1] if len(argv) > 1 else "var/junit.xml")
    if found:
        sys.stderr.write("пропущены тесты, в CI пропусков быть не должно:\n" + "\n".join(found) + "\n")
        return 1
    print("пропущенных тестов нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
