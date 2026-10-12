"""Падает, если запрещённая формулировка встречается вне реестра утверждений.

Перенесено из SynAPS-ProgramPlan. Список — BANNED_CLAIMS.txt, одна фраза в строке,
сравнение без учёта регистра.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_FILES = {"CLAIMS_REGISTRY.md", "BANNED_CLAIMS.txt", "lint_claims.py"}
SKIP_DIRS = {".git", ".venv", "var", "build", "dist", ".pytest_cache", ".ruff_cache", "__pycache__"}
SUFFIXES = {".py", ".md", ".html", ".toml", ".json", ".txt", ".yml", ".yaml"}


def main() -> int:
    banned = [
        line.strip()
        for line in (ROOT / "BANNED_CLAIMS.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES or path.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        hits += [f"{path.relative_to(ROOT)}: «{phrase}»" for phrase in banned if phrase.casefold() in text]
    if hits:
        sys.stderr.write("запрещённые формулировки:\n" + "\n".join(hits) + "\n")
        return 1
    print(f"проверено, запрещённых формулировок нет ({len(banned)} шаблонов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
