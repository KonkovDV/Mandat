"""Числа в текстах для жюри из evidence.json: метки ev: в Markdown.

Метка — пара HTML-комментариев вокруг числа, на GitHub их не видно:

    <!--ev:latency.median_ms|ru1-->2,6<!--/ev--> мс

Ключ — точечный путь в evidence.json; элемент списка — по номеру («synthetic_corpus.agreement_wilson95.0»).
Формат после «|» (в строке таблицы — «\\|», иначе GitHub разрежет ячейку):

    int   целое; от 10 000 — группы по три цифры через узкий неразрывный пробел, четырёхзначные слитно: «2537»;
    len   число элементов списка или словаря, как int;
    ru1   один знак после запятой: 2.58 → «2,6»;
    ru0%  доля в процентах без дробной части: 0.612 → «61»;
    ru1%  доля в процентах с одним знаком: 0.0127 → «1,3»;
    str   значение как есть.

Без формата целое пишется как int, строка — как str; дробному числу формат нужен явно. Единицы («мс», «%»)
стоят в тексте за меткой и в значение не входят. Слово из ru_plural.FORMS сразу за числом согласуется с ним
(«2537 автотестов», «2541 автотест»), после предлога родительного падежа («из 2537 автотестов») — нет.

    python scripts/sync_evidence_marks.py --write   # заполнить метки во всех *.md
    python scripts/sync_evidence_marks.py --check   # код 1 и список: файл, строка, метка, ожидалось, в тексте

Неизвестный ключ или формат, метка без закрывающего комментария, метка в начале строки (GitHub покажет строку
как HTML и не разберёт в ней разметку) и «|» без «\\» в таблице — ошибки в обоих режимах; --write тогда ничего
не пишет. Только стандартная библиотека и соседний ru_plural.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ru_plural import FORMS, GENITIVE_PREPOSITIONS, plural  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "var", "build", "dist", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules"}
FORMATS = ("int", "len", "ru1", "ru0%", "ru1%", "str")

OPEN = re.compile(r"<!--ev:")
MARK = re.compile(r"<!--ev:(?P<key>[^|\\\s]+?)(?:(?P<pipe>\\?\|)(?P<fmt>[^\s>]+?))?-->(?P<value>.*?)<!--/ev-->")
# Слово сразу за меткой; разметка «**» между числом и словом не мешает.
AGREE = re.compile(
    r"\*{0,2}\s+\*{0,2}("
    + "|".join(sorted({re.escape(f) for forms in FORMS.values() for f in forms}, key=len, reverse=True))
    + r")(?![\w-])"
)
STEM = {form: stem for stem, forms in FORMS.items() for form in forms}
# Перед меткой только отступ, маркер списка или цитаты: со строки, которая начинается с «<!--», CommonMark
# начинает HTML-блок, и ссылки, код и выделение в ней остаются сырым текстом.
BLOCK_START = re.compile(r"[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+|>[ \t]?)*")


def lookup(evidence: Any, key: str) -> Any:
    value = evidence
    for part in key.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise KeyError(f"в evidence.json нет ключа «{key}»")
    return value


def group(n: int) -> str:
    return f"{n:,}".replace(",", " ") if abs(n) >= 10_000 else str(n)


def render(value: Any, fmt: str | None) -> tuple[str, int | None]:
    """Текст значения и целое для согласования слова за ним (None, если число не целое)."""
    kind = type(value).__name__
    if fmt is None:
        if isinstance(value, str):
            fmt = "str"
        elif isinstance(value, int) and not isinstance(value, bool):
            fmt = "int"
        else:
            raise ValueError(f"для значения {kind} нужен явный формат: {', '.join(FORMATS)}")
    if fmt not in FORMATS:
        raise ValueError(f"неизвестный формат «{fmt}», есть: {', '.join(FORMATS)}")
    if fmt == "str":
        return str(value), None
    if fmt == "len":
        if not isinstance(value, list | dict):
            raise ValueError(f"формат len ждёт список или словарь, а не {kind}")
        return group(len(value)), len(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"формат {fmt} ждёт число, а не {kind}")
    if fmt == "int":
        if not isinstance(value, int):
            raise ValueError(f"формат int ждёт целое, а не {value!r}")
        return group(value), value
    scaled = value * 100 if fmt.endswith("%") else value
    return f"{scaled:.{fmt[2]}f}".replace(".", ","), None


def fill(text: str, evidence: Any) -> tuple[str, list[tuple[int, str, str, str]], list[tuple[int, str]]]:
    """Текст с заполненными метками, расхождения (строка, метка, ожидалось, было) и ошибки (строка, сообщение)."""
    out: list[str] = []
    diffs: list[tuple[int, str, str, str]] = []
    errors: list[tuple[int, str]] = []
    starts: set[int] = set()
    pos = 0
    for m in MARK.finditer(text):
        starts.add(m.start())
        line = text.count("\n", 0, m.start()) + 1
        label = text[m.start() : m.start("value")]
        prefix = text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
        if BLOCK_START.fullmatch(prefix):
            errors.append((line, f"{label} в начале строки: поставьте перед числом слово"))
        if prefix.lstrip().startswith("|") and m.group("pipe") == "|":
            errors.append((line, f"{label} в строке таблицы: формат отделяется «\\|», иначе GitHub разрежет ячейку"))
        try:
            value, n = render(lookup(evidence, m.group("key")), m.group("fmt"))
        except (KeyError, ValueError) as exc:
            errors.append((line, f"{label}: {exc.args[0]}"))
            continue
        out += [text[pos : m.start("value")], value]
        pos = m.end("value")
        if value != m.group("value"):
            diffs.append((line, label, value, m.group("value")))
        word = AGREE.match(text, m.end())
        before = re.search(r"(\w+)[\s*]*$", prefix)
        if n is None or word is None or (before is not None and before.group(1).casefold() in GENITIVE_PREPOSITIONS):
            continue
        expected = plural(n, *FORMS[STEM[word.group(1)]])
        if expected != word.group(1):
            diffs.append((line, f"{label} (слово за числом)", expected, word.group(1)))
            out += [text[pos : word.start(1)], expected]
            pos = word.end(1)
    out.append(text[pos:])
    for m in OPEN.finditer(text):
        if m.start() not in starts:
            errors.append((text.count("\n", 0, m.start()) + 1, "метка без <!--/ev--> в той же строке"))
    return "".join(out), diffs, errors


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(root).parts)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Числа в текстах для жюри из evidence.json (метки ev:).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="заполнить метки из evidence.json")
    mode.add_argument("--check", action="store_true", help="код 1, если число в тексте расходится с evidence.json")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root: Path = args.root
    evidence = json.loads((root / "evidence.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    diffs: list[str] = []
    changed: dict[Path, str] = {}
    marks = 0
    for path in markdown_files(root):
        text = path.read_text(encoding="utf-8")
        if "<!--ev:" not in text:
            continue
        rel = path.relative_to(root).as_posix()
        new, file_diffs, file_errors = fill(text, evidence)
        marks += len(OPEN.findall(text))
        errors += [f"{rel}:{line}: {message}" for line, message in file_errors]
        diffs += [f"{rel}:{n}: {label} ожидается «{want}», в тексте «{got}»" for n, label, want, got in file_diffs]
        if new != text:
            changed[path] = new
    if errors:
        sys.stderr.write("метки ev: с ошибками:\n" + "\n".join(errors) + "\n")
        return 1
    if args.check:
        if diffs:
            hint = "числа в текстах расходятся с evidence.json, исправит python scripts/sync_evidence_marks.py --write"
            sys.stderr.write(hint + ":\n" + "\n".join(diffs) + "\n")
            return 1
        print(f"метки ev: совпадают с evidence.json ({marks})")
        return 0
    for path, new in changed.items():
        path.write_text(new, encoding="utf-8")
        print(f"обновлено: {path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
