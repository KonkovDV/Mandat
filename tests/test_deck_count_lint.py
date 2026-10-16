"""Число автотестов в презентации может отставать от evidence.json, но не превышать его (#54)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("lint_claims", ROOT / "scripts" / "lint_claims.py")
assert _spec is not None and _spec.loader is not None
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


@pytest.mark.parametrize(("found", "bad"), [(1354, False), (1000, False), (1355, True), (0, True)])
def test_deck_test_count_may_lag_but_not_exceed(found, bad):
    assert lint.count_mismatch("автотест", found, 1354, deck=True) is bad


@pytest.mark.parametrize("found", [1353, 1355])
def test_texts_outside_the_deck_stay_exact(found):
    assert lint.count_mismatch("автотест", found, 1354, deck=False)
    assert not lint.count_mismatch("автотест", 1354, 1354, deck=False)


@pytest.mark.parametrize("name", ["демо-кейс", "мутаци"])
def test_other_deck_numbers_stay_exact(name):
    assert lint.count_mismatch(name, 9, 10, deck=True)
    assert not lint.count_mismatch(name, 10, 10, deck=True)


def test_no_source_means_no_check():
    assert not lint.count_mismatch("автотест", 5, None, deck=True)
