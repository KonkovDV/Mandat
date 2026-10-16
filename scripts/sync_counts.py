"""Переписать числа автотестов и демо-кейсов в README и JURY из evidence.json с согласованной формой слова."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ru_plural import FORMS, GENITIVE_PREPOSITIONS, count_phrase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("README.md", "docs/JURY.md")


def counts(evidence: dict) -> dict[str, int]:
    return {"автотест": evidence["tests"]["passed"], "демо-кейс": len(evidence["demo_cases"]["results"])}


def word_pattern(stem: str) -> re.Pattern[str]:
    """Число и одна из форм числительного для stem; другие падежи («на 39 правилах») не совпадают."""
    forms = "|".join(sorted({re.escape(f) for f in FORMS[stem]}, key=len, reverse=True))
    return re.compile(rf"(?<![\w.,])(\d+)(\s+)({forms})(?![\w-])")


def after_genitive_preposition(text: str, start: int) -> bool:
    before = re.search(r"(\w+)\s*$", text[:start])
    return before is not None and before.group(1).casefold() in GENITIVE_PREPOSITIONS


def rewrite(text: str, values: dict[str, int]) -> str:
    for stem, n in values.items():
        word = count_phrase(n, stem).split(" ", 1)[1]

        def sub(m: re.Match[str], n: int = n, word: str = word) -> str:
            # После предлога форма падежная, не по числу: меняется только число.
            keep = after_genitive_preposition(m.string, m.start())
            return f"{n}{m.group(2)}{m.group(3) if keep else word}"

        text = word_pattern(stem).sub(sub, text)
    return text


def main() -> int:
    values = counts(json.loads((ROOT / "evidence.json").read_text(encoding="utf-8")))
    for rel in TARGETS:
        path = ROOT / rel
        old = path.read_text(encoding="utf-8")
        new = rewrite(old, values)
        if new != old:
            path.write_text(new, encoding="utf-8")
            print(f"обновлено: {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
