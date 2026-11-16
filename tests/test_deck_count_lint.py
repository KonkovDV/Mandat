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


def test_submitted_form_is_a_dated_record(monkeypatch, tmp_path):
    """Сданная форма не догоняет evidence.json, но только пока сама называет дату среза."""
    # Внутри make evidence число автотестов ещё собирается, и lint_claims его не сверяет: здесь сверяет.
    monkeypatch.delenv("MANDAT_EVIDENCE_BUILDING", raising=False)
    assert lint.DATED_RECORDS == {"submission/FORM_SPRINT1.md": "31.10.2026"}
    head = (ROOT / "submission" / "FORM_SPRINT1.md").read_text(encoding="utf-8").splitlines()[:5]
    assert any("срез на 31.10.2026" in line.casefold() for line in head)

    (tmp_path / "submission").mkdir()
    (tmp_path / "evidence.json").write_text(
        '{"tests": {"returncode": 0, "passed": 2000}, "demo_cases": {"results": {}}}', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_redteam_fuzz.py").write_text("assert len(fuzz_rows) == 1\n", encoding="utf-8")
    form = tmp_path / "submission" / "FORM_SPRINT1.md"
    current = tmp_path / "README.md"
    current.write_text("1621 автотест проходит\n", encoding="utf-8")
    monkeypatch.setattr(lint, "ROOT", tmp_path)

    form.write_text("# Форма\n\nСрез на 31.10.2026.\n\n1621 автотест проходит\n", encoding="utf-8")
    hits = lint.evidence_count_hits()
    assert [h for h in hits if h.startswith("README.md")], "текущий документ проверяется как раньше"
    assert not [h for h in hits if h.startswith("submission/")]

    form.write_text("# Форма\n\n1621 автотест проходит\n", encoding="utf-8")
    hits = lint.evidence_count_hits()
    assert any("нет «срез на 31.10.2026»" in h for h in hits)
    assert any(h.startswith("submission/FORM_SPRINT1.md:3:") for h in hits), "без даты числа проверяются"
