"""Падает, если запрещённая формулировка встречается вне реестра утверждений.

Перенесено из SynAPS-ProgramPlan. Список — BANNED_CLAIMS.txt, одна фраза в строке,
сравнение без учёта регистра. Текст презентации (слайды и заметки из .pptx) проверяется
тем же списком: она главный материал для жюри. Числа в текстах сверяются с evidence.json; число автотестов в
презентации может быть меньше фактического (колоду пересобирают к релизу, а не в каждом PR), но не больше.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ru_plural import FORMS, GENITIVE_PREPOSITIONS, plural  # noqa: E402

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
RULE_COUNT = re.compile(r"\b(\d+)\*{0,2}\s+\*{0,2}правил[оа]?\b|правило из (\d+)\b")
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
# Разметка между числом и словом («**1017** автотестов») не прячет число от проверки.
EMPH = r"\*{0,2}"
EVIDENCE_COUNTS = {
    "автотест": re.compile(rf"\b(\d+){EMPH}\s+{EMPH}автотест"),
    "демо-кейс": re.compile(rf"\b(\d+){EMPH}\s+{EMPH}демо-кейс"),
    # «8814», «8 814» и «8\u00a0814»; «мутаций», «мутированных сценариев».
    "мутаци": re.compile(
        rf"\b(\d{{1,3}}(?:[ \u00a0\u202f]\d{{3}})+|\d+){EMPH}\s+{EMPH}(?:мутаци|мутированн)", re.IGNORECASE
    ),
}


def fuzz_runs() -> int:
    """Число мутаций фазза берётся из теста, который его закрепляет."""
    text = (ROOT / "tests" / "test_redteam_fuzz.py").read_text(encoding="utf-8")
    match = re.search(r"assert len\(fuzz_rows\) == (\d+)", text)
    if match is None:
        raise SystemExit("в tests/test_redteam_fuzz.py не найдено закреплённое число мутаций")
    return int(match.group(1))


def red_run(tests: dict | None) -> str | None:
    """Число автотестов берётся только из зелёного прогона: иначе в тексты уйдёт «N−1»."""
    if not tests:
        return "evidence.json: нет результатов pytest, соберите без --no-tests"
    if tests.get("returncode") != 0 or tests.get("failed") or tests.get("error"):
        return f"evidence.json собран на красном прогоне ({tests.get('summary', '')}): пересоберите"
    return None


# В презентации число автотестов может отставать от evidence.json: «N автотестов проходят» остаётся верным, пока
# N не больше фактического. Так колоду не пересобирают в каждом PR, а только к релизу. Остальные числа — точно.
DECK_AT_MOST = {"автотест"}


def count_mismatch(name: str, found: int, expected: int | None, *, deck: bool) -> bool:
    """Число в тексте расходится с источником. В презентации для DECK_AT_MOST допустимо меньшее, но не 0."""
    if expected is None:
        return False
    if deck and name in DECK_AT_MOST:
        return not 0 < found <= expected
    return found != expected


def evidence_count_hits() -> list[str]:
    import json

    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    building = os.environ.get("MANDAT_EVIDENCE_BUILDING") == "1"
    red = None if building else red_run(evidence.get("tests"))
    if red:
        return [red]
    expected = {
        "автотест": None if building else evidence["tests"]["passed"],
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
                    if count_mismatch(name, int(re.sub(r"\D", "", m.group(1))), expected[name], deck=False):
                        hits.append(f"{rel}:{i}: «{m.group(0)}», а в источнике {expected[name]}")
    # Слайды собираются из evidence.json (make deck). Число автотестов в колоде может быть меньше фактического
    # (DECK_AT_MOST), больше — нет: утверждение стало бы неверным.
    for deck in sorted((ROOT / "submission" / "slides").glob("*.pptx")):
        text = pptx_text(deck)
        for name, pattern in EVIDENCE_COUNTS.items():
            for m in pattern.finditer(text):
                if count_mismatch(name, int(re.sub(r"\D", "", m.group(1))), expected[name], deck=True):
                    rel = deck.relative_to(ROOT).as_posix()
                    bound = "не больше " if name in DECK_AT_MOST else ""
                    hits.append(f"{rel}: «{m.group(0)}», а в источнике {bound}{expected[name]}")
    return hits


# Форма слова рядом с числом: «1384 автотеста», «41 правило». Проверяются только формы числительного;
# другие падежи («на 39 правилах») и родительный после предлога («из 1384 автотестов») не трогаются.
FORM_WORD = re.compile(
    r"(?<![\w.,])(\d+)\s+("
    + "|".join(sorted({re.escape(f) for forms in FORMS.values() for f in forms}, key=len, reverse=True))
    + r")(?![\w-])"
)
FORM_STEM = {form: stem for stem, forms in FORMS.items() for form in forms}


def form_mismatches(text: str) -> list[str]:
    """Несогласованные пары «число — слово» в тексте: «1384 автотестов → 1384 автотеста»."""
    hits = []
    for m in FORM_WORD.finditer(text):
        before = re.search(r"(\w+)\s*$", text[: m.start()])
        if before is not None and before.group(1).casefold() in GENITIVE_PREPOSITIONS:
            continue
        n, word = int(m.group(1)), m.group(2)
        expected = plural(n, *FORMS[FORM_STEM[word]])
        if word != expected:
            hits.append(f"«{m.group(0)}» → «{n} {expected}»")
    return hits


def form_hits() -> list[str]:
    hits = []
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name in RULE_COUNT_SKIP or rel.startswith(RULE_COUNT_SKIP_PREFIXES):
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts):
            continue
        for i, text in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            hits += [f"{rel}:{i}: {hit}" for hit in form_mismatches(text)]
    for deck in sorted((ROOT / "submission" / "slides").glob("*.pptx")):
        hits += [f"{deck.relative_to(ROOT).as_posix()}: {hit}" for hit in form_mismatches(pptx_text(deck))]
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
    wrong_forms = form_hits()
    if wrong_forms:
        sys.stderr.write("число и слово не согласованы:\n" + "\n".join(wrong_forms) + "\n")
        return 1
    print(f"проверено, запрещённых формулировок нет ({len(banned)} шаблонов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
