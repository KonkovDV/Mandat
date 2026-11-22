"""Числа внутри разметки Markdown не прячутся от сверки с evidence.json."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("lint_claims", ROOT / "scripts" / "lint_claims.py")
assert _spec is not None and _spec.loader is not None
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


@pytest.mark.parametrize(
    "text",
    ["**1017** автотестов", "1017 **автотестов**", "**1017 автотестов**", "1017 автотестов"],
)
def test_test_count_found_through_emphasis(text):
    m = lint.EVIDENCE_COUNTS["автотест"].search(text)
    assert m is not None and m.group(1) == "1017"


@pytest.mark.parametrize("text", ["**14** демо-кейсов", "**8814** мутаций", "**39** правил"])
def test_other_counts_found_through_emphasis(text):
    patterns = [*lint.EVIDENCE_COUNTS.values(), lint.RULE_COUNT]
    assert any(p.search(text) for p in patterns)
