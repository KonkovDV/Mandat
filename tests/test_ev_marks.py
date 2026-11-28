"""Метки ev: в текстах для жюри заполняются из evidence.json и сверяются с ним (scripts/sync_evidence_marks.py)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


marks = _load("sync_evidence_marks")

EVIDENCE = {
    "tests": {"passed": 2541, "coverage_percent": 95.94},
    "latency": {"median_ms": 2.58, "p95_ms": 3.56},
    "synthetic_corpus": {
        "cases": 294,
        "coverage": 0.3877551020408163,
        "abstention_rate": 0.6122448979591837,
        "false_confirmed_rate_upper95": 0.01272692809310183,
        "agreement_wilson95": [0.987, 1.0],
        "false_confirmed_defect_types": [],
    },
    "demo_cases": {"results": {"01_ok": {}, "02_bad": {}}},
    "engine": "mandat/0.4.0",
    "big": 1234567,
}


def mark(key: str, value: str) -> str:
    return f"<!--ev:{key}-->{value}<!--/ev-->"


def test_fill_replaces_value_and_reports_the_difference():
    text = f"Итог: {mark('synthetic_corpus.cases', '280')} кейсов.\nВторая: {mark('engine', 'mandat/0.4.0')}\n"
    new, diffs, errors = marks.fill(text, EVIDENCE)
    assert errors == []
    assert new == f"Итог: {mark('synthetic_corpus.cases', '294')} кейсов.\nВторая: {mark('engine', 'mandat/0.4.0')}\n"
    assert diffs == [(1, "<!--ev:synthetic_corpus.cases-->", "294", "280")]
    assert marks.fill(new, EVIDENCE)[1:] == ([], [])


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("tests.passed", "2541"),
        ("big", "1 234 567"),
        ("demo_cases.results|len", "2"),
        ("synthetic_corpus.false_confirmed_defect_types|len", "0"),
        ("latency.median_ms|ru1", "2,6"),
        ("latency.p95_ms|ru1", "3,6"),
        ("tests.coverage_percent|ru1", "95,9"),
        ("synthetic_corpus.abstention_rate|ru0%", "61"),
        ("synthetic_corpus.coverage|ru1%", "38,8"),
        ("synthetic_corpus.false_confirmed_rate_upper95|ru1%", "1,3"),
        ("synthetic_corpus.cases|str", "294"),
        ("engine", "mandat/0.4.0"),
    ],
)
def test_formats(key, expected):
    new, _, errors = marks.fill(f"x {mark(key, '?')} мс", EVIDENCE)
    assert errors == []
    assert new == f"x {mark(key, expected)} мс", "единица остаётся в тексте за меткой"


def test_nested_keys_and_list_index():
    text = f"граница {mark('synthetic_corpus.agreement_wilson95.0|ru1%', '')} %"
    assert marks.fill(text, EVIDENCE)[0] == f"граница {mark('synthetic_corpus.agreement_wilson95.0|ru1%', '98,7')} %"


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("tests.failed", "нет ключа «tests.failed»"),
        ("tests.passed.count", "нет ключа «tests.passed.count»"),
        ("synthetic_corpus.agreement_wilson95.2|ru1", "нет ключа"),
        ("latency.median_ms", "нужен явный формат"),
        ("latency.median_ms|ru2", "неизвестный формат «ru2»"),
        ("tests.coverage_percent|int", "ждёт целое"),
        ("engine|ru1", "ждёт число"),
        ("tests.passed|len", "ждёт список"),
    ],
)
def test_unknown_key_or_bad_format_is_an_error(key, message):
    text = f"всего {mark(key, '1')} штук"
    new, diffs, errors = marks.fill(text, EVIDENCE)
    assert new == text and diffs == []
    assert len(errors) == 1 and errors[0][0] == 1 and message in errors[0][1], errors


def test_word_after_number_agrees_except_after_genitive_preposition():
    def phrase(n: str) -> str:
        return f"проходят {mark('tests.passed', n)} автотестов; ни один из **{mark('tests.passed', n)}** автотестов"

    new, diffs, errors = marks.fill(phrase("1375") + f"; {mark('demo_cases.results|len', '13')} демо-кейсов", EVIDENCE)
    assert errors == []
    assert new == (
        f"проходят {mark('tests.passed', '2541')} автотест; ни один из **{mark('tests.passed', '2541')}** автотестов; "
        f"{mark('demo_cases.results|len', '2')} демо-кейса"
    )
    assert (1, "<!--ev:tests.passed--> (слово за числом)", "автотест", "автотестов") in diffs
    assert marks.fill(new, EVIDENCE)[1:] == ([], [])


def test_table_rows_need_an_escaped_pipe_and_marks_need_words_before_them():
    escaped = "| C-14 | медиана <!--ev:latency.median_ms\\|ru1-->2,6<!--/ev--> мс |"
    assert marks.fill(escaped, EVIDENCE)[1:] == ([], [])
    bare = "| C-14 | медиана <!--ev:latency.median_ms|ru1-->2,6<!--/ev--> мс |"
    assert "\\|" in marks.fill(bare, EVIDENCE)[2][0][1]
    for text in (f"{mark('tests.passed', '2541')} автотест", f"- {mark('tests.passed', '2541')} автотест"):
        assert "в начале строки" in marks.fill(text, EVIDENCE)[2][0][1]
    assert marks.fill(f"> Итог: {mark('tests.passed', '2541')} автотест", EVIDENCE)[2] == []
    errors = marks.fill("а\nвсего <!--ev:tests.passed-->2541 автотест", EVIDENCE)[2]
    assert errors == [(2, "метка без <!--/ev--> в той же строке")]


def _repo(tmp_path: Path, text: str) -> Path:
    (tmp_path / "evidence.json").write_text(json.dumps(EVIDENCE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "JURY.md").write_text(text, encoding="utf-8")
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "skip.md").write_text(f"x {mark('nope', '1')}\n", encoding="utf-8")
    return tmp_path / "docs" / "JURY.md"


def test_check_lists_differences_and_write_fixes_them(tmp_path, capsys):
    doc = _repo(tmp_path, f"# Итог\n\nПроходят {mark('tests.passed', '2537')} автотестов.\n")
    assert marks.main(["--check", "--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "docs/JURY.md:3: <!--ev:tests.passed--> ожидается «2541», в тексте «2537»" in err
    assert "ожидается «автотест», в тексте «автотестов»" in err
    assert marks.main(["--write", "--root", str(tmp_path)]) == 0
    assert doc.read_text(encoding="utf-8") == f"# Итог\n\nПроходят {mark('tests.passed', '2541')} автотест.\n"
    assert marks.main(["--check", "--root", str(tmp_path)]) == 0


def test_write_changes_nothing_when_a_key_is_unknown(tmp_path, capsys):
    text = f"Проходят {mark('tests.passed', '2537')} автотестов, {mark('tests.flaky', '0')} нестабильных.\n"
    doc = _repo(tmp_path, text)
    assert marks.main(["--write", "--root", str(tmp_path)]) == 1
    assert "docs/JURY.md:1: <!--ev:tests.flaky-->: в evidence.json нет ключа «tests.flaky»" in capsys.readouterr().err
    assert doc.read_text(encoding="utf-8") == text


def test_site_tables_show_the_number_without_the_mark():
    site = _load("build_site")
    text = "## Итог\n\n| Что | Число |\n|---|---|\n| медиана | <!--ev:latency.median_ms\\|ru1-->2,6<!--/ev--> мс |\n"
    assert site._table(text, "## Итог") == [["медиана", "2,6 мс"]]
