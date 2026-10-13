"""Падает, если запрещённая формулировка встречается вне реестра утверждений.

Перенесено из SynAPS-ProgramPlan. Список — BANNED_CLAIMS.txt, одна фраза в строке,
сравнение без учёта регистра. Текст презентации (слайды и заметки из .pptx) проверяется
тем же списком: она главный материал для жюри.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_FILES = {"CLAIMS_REGISTRY.md", "BANNED_CLAIMS.txt", "lint_claims.py"}
SKIP_DIRS = {".git", ".venv", "var", "build", "dist", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules"}
SUFFIXES = {".pptx", ".py", ".md", ".html", ".toml", ".json", ".txt", ".yml", ".yaml"}


def pptx_text(path: Path) -> str:
    """Текст слайдов и заметок без сторонних библиотек: только узлы <a:t>."""
    with zipfile.ZipFile(path) as z:
        parts = [n for n in z.namelist() if re.match(r"ppt/(slides|notesSlides)/[^/]+\.xml$", n)]
        xml = " ".join(z.read(n).decode("utf-8", errors="ignore") for n in sorted(parts))
    return " ".join(re.findall(r"<a:t>([^<]*)</a:t>", xml))


# Число правил в текстах должно совпадать с пакетом. История (CHANGELOG, записи аудитов) не проверяется.
RULE_COUNT = re.compile(r"\b(\d+)\s+правил\b|правило из (\d+)\b")
RULE_COUNT_SKIP = {"CHANGELOG.md"}
RULE_COUNT_SKIP_PREFIXES = ("docs/AUDIT_", "docs/audit/")


def rule_count_hits() -> list[str]:
    import json

    actual = len(json.loads((ROOT / "src" / "mandat" / "data" / "rules.json").read_text(encoding="utf-8"))["rules"])
    hits = []
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name in RULE_COUNT_SKIP or rel.startswith(RULE_COUNT_SKIP_PREFIXES):
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts):
            continue
        for number, line in (
            (int(m.group(1) or m.group(2)), i)
            for i, text in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1)
            for m in RULE_COUNT.finditer(text)
        ):
            if number != actual:
                hits.append(f"{rel}:{line}: «{number} правил», а в rules.json {actual}")
    return hits


# Ключевые числа для жюри должны совпадать с источником: автотесты и демо-кейсы — с evidence.json,
# мутации фазза — с тестом tests/test_redteam_fuzz.py.
EVIDENCE_COUNTS = {
    "автотест": re.compile(r"\b(\d+)\s+автотест"),
    "демо-кейс": re.compile(r"\b(\d+)\s+демо-кейс"),
    "мутаци": re.compile(r"\b(\d+)\s+мутаци"),
}


def fuzz_runs() -> int:
    """Число мутаций фазза берётся из теста, который его закрепляет."""
    text = (ROOT / "tests" / "test_redteam_fuzz.py").read_text(encoding="utf-8")
    match = re.search(r"assert len\(fuzz_rows\) == (\d+)", text)
    if match is None:
        raise SystemExit("в tests/test_redteam_fuzz.py не найдено закреплённое число мутаций")
    return int(match.group(1))


def evidence_count_hits() -> list[str]:
    import json

    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    expected = {
        "автотест": evidence["tests"]["passed"],
        "демо-кейс": len(evidence["demo_cases"]["results"]),
        "мутаци": fuzz_runs(),
    }
    hits = []
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name in RULE_COUNT_SKIP or rel.startswith(RULE_COUNT_SKIP_PREFIXES):
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts):
            continue
        for i, text in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            for name, pattern in EVIDENCE_COUNTS.items():
                for m in pattern.finditer(text):
                    if int(m.group(1)) != expected[name]:
                        hits.append(f"{rel}:{i}: «{m.group(0)}», а в источнике {expected[name]}")
    return hits


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
        if path.suffix == ".pptx":
            text = pptx_text(path).casefold()
        else:
            text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        hits += [f"{path.relative_to(ROOT)}: «{phrase}»" for phrase in banned if phrase.casefold() in text]
    if hits:
        sys.stderr.write("запрещённые формулировки:\n" + "\n".join(hits) + "\n")
        return 1
    count_hits = rule_count_hits()
    if count_hits:
        sys.stderr.write("число правил в текстах не совпадает с пакетом:\n" + "\n".join(count_hits) + "\n")
        return 1
    evidence_hits = evidence_count_hits()
    if evidence_hits:
        message = "числа в текстах не совпадают с evidence.json или тестом фазза:\n"
        sys.stderr.write(message + "\n".join(evidence_hits) + "\n")
        return 1
    print(f"проверено, запрещённых формулировок нет ({len(banned)} шаблонов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
