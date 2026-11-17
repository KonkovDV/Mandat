"""R2, линза пользователя: доступность HTML-карточки (WCAG 2.2 AA) и видимость статуса draft."""

from __future__ import annotations

import re

import pytest

from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.report.html import COLORS, render
from mandat.services.verify import verify_case


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


@pytest.mark.parametrize("verdict", sorted(COLORS))
def test_verdict_badge_contrast_is_aa(verdict):
    # WCAG 2.2, 1.4.3: белый текст на цвете вердикта не ниже 4.5:1.
    assert 1.05 / (_luminance(COLORS[verdict]) + 0.05) >= 4.5


def _card(kind="ok"):
    return render(verify_case(load_case_inline(synth.build(kind, 2).inline_manifest())).as_dict())


def test_card_structure_is_accessible():
    html = _card()
    assert '<html lang="ru">' in html
    assert html.count("<h1") == 1
    assert "<th>" not in html and 'scope="col"' in html
    assert html.count("<caption") == len(re.findall(r"<table", html))


def test_card_shows_draft_status_and_next_steps():
    html = _card("revoked")
    assert "черновик (draft)" in html
    assert "Что сделать дальше" in html
