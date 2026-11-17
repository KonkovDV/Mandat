"""Согласование числа и слова в текстах для жюри (#59)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ru = _load("ru_plural")
lint = _load("lint_claims")
sync = _load("sync_counts")


@pytest.mark.parametrize(
    ("n", "word"),
    [
        (1, "автотест"),
        (4, "автотеста"),
        (11, "автотестов"),
        (14, "автотестов"),
        (21, "автотест"),
        (104, "автотеста"),
        (111, "автотестов"),
        (1384, "автотеста"),
        (0, "автотестов"),
        (5, "автотестов"),
        (12, "автотестов"),
        (22, "автотеста"),
        (1375, "автотестов"),
    ],
)
def test_plural(n, word):
    assert ru.plural(n, "автотест", "автотеста", "автотестов") == word


def test_count_phrase_rules():
    assert ru.count_phrase(41, "правил") == "41 правило"
    assert ru.count_phrase(39, "правил") == "39 правил"
    assert ru.count_phrase(13, "демо-кейс") == "13 демо-кейсов"


def test_lint_flags_wrong_form():
    assert lint.form_mismatches("демо одной командой, 1384 автотестов") == ["«1384 автотестов» → «1384 автотеста»"]
    assert lint.form_mismatches("всего 42 правил") == ["«42 правил» → «42 правила»"]


@pytest.mark.parametrize(
    "text",
    [
        "1384 автотеста проходят",
        "ни один из 1384 автотестов не упал",
        "на 39 правилах",
        "39 правил с нормой",
        "версия 1.384 автотест",
    ],
)
def test_lint_accepts_correct_or_other_cases(text):
    assert lint.form_mismatches(text) == []


def test_rule_count_lint_sees_singular_and_few_forms():
    assert lint.RULE_COUNT.search("41 правило").group(1) == "41"
    assert lint.RULE_COUNT.search("42 правила").group(1) == "42"


def test_sync_rewrites_number_and_form_but_keeps_genitive_word():
    text = "1375 автотестов проходят; ни один из 1375 автотестов; 13 демо-кейсов"
    out = sync.rewrite(text, {"автотест": 1384, "демо-кейс": 13})
    assert out == "1384 автотеста проходят; ни один из 1384 автотестов; 13 демо-кейсов"


def test_repository_texts_agree():
    assert lint.form_hits() == []
