"""PDF для жюри и банка: язык документа, разметка для экранного диктора и закладки по слайдам.

Колоду в PDF собирает LibreOffice и пишет в каталог язык своей локали (en-US), хотя текст русский: диктор
читает такой PDF по английским правилам. ``finish_deck.py --pdf`` ставит ru-RU; тест держит это и для
пакета аналитики. Проверка по байтам файла, без библиотеки разбора PDF.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "submission" / "slides" / "Mandat_VTB.pdf"
ANALYTICS = sorted((ROOT / "submission" / "analytics" / "out").glob("Mandat_*.pdf"))


def _slides() -> int:
    import zipfile

    with zipfile.ZipFile(DECK.with_suffix(".pptx")) as z:
        return len([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)])


def test_deck_pdf_is_russian_tagged_and_has_a_bookmark_per_slide():
    data = DECK.read_bytes()
    assert re.findall(rb"/Lang\s*\(([^)]*)\)", data) == [b"ru-RU"]
    assert b"/StructTreeRoot" in data and b"/Marked true" in data
    outline = re.search(rb"/Outlines (\d+) 0 R", data)
    assert outline, "нет закладок"
    root = re.search(rb"\n" + outline.group(1) + rb" 0 obj\s*<<\s*/Count (\d+)", data)
    assert root and int(root.group(1)) == _slides()


@pytest.mark.parametrize("pdf", ANALYTICS, ids=lambda p: p.name)
def test_analytics_pdfs_declare_russian(pdf):
    langs = {m.replace(b"\\055", b"-") for m in re.findall(rb"/Lang\s*\(([^)]*)\)", pdf.read_bytes())}
    assert langs and langs <= {b"ru", b"ru-RU"}, langs
