"""Слайды для сайта и текст выступления из собранной колоды: картинки, заголовки и заметки докладчика.

    python submission/deck/export_slides.py submission/slides/Mandat_VTB.pptx submission/slides/Mandat_VTB.pdf

Картинки — страницы PDF (pdftoppm из poppler) в WebP 1600×900 и миниатюры 400 px в ``site/slides``
(convert из ImageMagick).
Заголовок, надзаголовок, строка источника и заметки берутся из XML самой колоды,
поэтому текст на сайте не расходится со слайдами.
Там же пишется ``submission/deck/SPEAKER_NOTES.md``: текст выступления по слайдам с хронометражем,
числом слов и темпом. Темп выше 2,8 слова в секунду — ошибка: так быстро не говорят.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "site" / "slides"
NOTES_MD = ROOT / "submission" / "deck" / "SPEAKER_NOTES.md"
WIDTH = 1600
THUMB = 400
MAX_WORDS_PER_SECOND = 2.8
TIME = re.compile(r"^\[(\d+):(\d{2})[–-](\d+):(\d{2})\]\s*")


def _text(xml: str) -> str:
    """Текст абзацев фигуры: <a:t> внутри <a:p>, абзацы через перевод строки; неразрывные знаки — обычные."""
    paras = []
    for p in re.findall(r"<a:p>(.*?)</a:p>", xml, re.S):
        parts = re.findall(r"<a:t>([^<]*)</a:t>|<a:br/>", p)
        paras.append("".join(parts))
    text = "\n".join(t for t in paras if t.strip())
    return html.unescape(text).replace(" ", " ").replace("‑", "-").strip()


def _placeholders(xml: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for sp in re.findall(r"<p:sp>.*?</p:sp>", xml, re.S):
        ph = re.search(r"<p:ph\b([^>]*)/?>", sp)
        if not ph:
            continue
        kind = re.search(r'type="(\w+)"', ph.group(1))
        idx = re.search(r'idx="(\d+)"', ph.group(1))
        key = kind.group(1) if kind and kind.group(1) == "title" else f"body{idx.group(1) if idx else ''}"
        out.setdefault(key, _text(sp))
    return out


def _sections(z: zipfile.ZipFile) -> list[tuple[str, int]]:
    xml = z.read("ppt/presentation.xml").decode("utf-8")
    return [
        (name, len(re.findall(r"<p14:sldId ", body)))
        for name, body in re.findall(r'<p14:section name="([^"]+)"[^>]*>(.*?)</p14:section>', xml, re.S)
    ]


def _notes(z: zipfile.ZipFile, n: int) -> str:
    rels = z.read(f"ppt/slides/_rels/slide{n}.xml.rels").decode("utf-8")
    target = re.search(r'Target="\.\./notesSlides/(notesSlide\d+\.xml)"', rels)
    if not target:
        return ""
    xml = z.read(f"ppt/notesSlides/{target.group(1)}").decode("utf-8")
    bodies = [sp for sp in re.findall(r"<p:sp>.*?</p:sp>", xml, re.S) if re.search(r'<p:ph[^>]*type="body"', sp)]
    return " ".join(_text(sp) for sp in bodies).strip()


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _seconds(m: str, s: str) -> int:
    return int(m) * 60 + int(s)


def export(pptx: Path, pdf: Path) -> dict:
    z = zipfile.ZipFile(pptx)
    names = sorted(
        (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
    )
    sections = _sections(z)
    pitch = next((count for name, count in sections if name == "Питч"), len(names))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("slide-*.webp"):
        old.unlink()
    slides = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-png", "-scale-to-x", str(WIDTH), "-scale-to-y", "-1", str(pdf), f"{tmp}/p"], check=True
        )
        pages = sorted(Path(tmp).glob("p-*.png"), key=lambda p: int(p.stem.split("-")[1]))
        if len(pages) != len(names):
            raise SystemExit(f"в PDF {len(pages)} страниц, а в PPTX {len(names)} слайдов: пересоберите PDF")
        for i, (name, page) in enumerate(zip(names, pages, strict=True), 1):
            image = f"slide-{i:02d}.webp"
            # Ширина и высота — из заголовка PNG (IHDR), сжатие в WebP — ImageMagick, без сторонних пакетов Python.
            width, height = struct.unpack(">II", page.read_bytes()[16:24])
            subprocess.run(
                ["convert", str(page), "-strip", "-quality", "82", "-define", "webp:method=6", str(OUT / image)],
                check=True,
            )
            # Миниатюра для ленты слайдов и списка: 400 px вместо 1600 — лента из 42 слайдов весит в десятки раз меньше.
            thumb = f"slide-{i:02d}-400.webp"
            subprocess.run(
                ["convert", str(page), "-strip", "-resize", f"{THUMB}x", "-quality", "80"]
                + ["-define", "webp:method=6", str(OUT / thumb)],
                check=True,
            )
            ph = _placeholders(z.read(name).decode("utf-8"))
            raw = _notes(z, i)
            when = TIME.match(raw)
            notes = TIME.sub("", raw)
            slides.append(
                {
                    "n": i,
                    "image": image,
                    "thumb": thumb,
                    "width": width,
                    "height": height,
                    "title": ph.get("title", "").replace("\n", " "),
                    "kicker": ph.get("body102", "") if i > 1 else "",
                    "source": ph.get("body104", ""),
                    "time": f"{when.group(1)}:{when.group(2)}–{when.group(3)}:{when.group(4)}" if when else "",
                    "start": _seconds(when.group(1), when.group(2)) if when else None,
                    "end": _seconds(when.group(3), when.group(4)) if when else None,
                    "notes": notes,
                }
            )
    data = {
        "pitch": pitch,
        "pptx_sha256": hashlib.sha256(pptx.read_bytes()).hexdigest(),
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "slides": slides,
    }
    (OUT / "slides.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return data


def speaker_notes(data: dict) -> list[str]:
    """SPEAKER_NOTES.md и замечания: блоки идут подряд от 0:00, темп не выше предела."""
    problems: list[str] = []
    rows, total_words, last_end = [], 0, 0
    for s in data["slides"][: data["pitch"]]:
        words = len([w for w in re.split(r"\s+", s["notes"]) if re.search(r"\w", w)])
        total_words += words
        if s["start"] is None:
            problems.append(f"слайд {s['n']}: в заметках нет отметки времени [мм:сс–мм:сс]")
            continue
        if s["start"] != last_end:
            problems.append(
                f"слайд {s['n']}: блок начинается в {s['time']}, "
                f"а предыдущий кончился на {last_end // 60}:{last_end % 60:02d}"
            )
        span = s["end"] - s["start"]
        pace = words / span if span else 0.0
        if pace > MAX_WORDS_PER_SECOND:
            problems.append(
                f"слайд {s['n']}: {words} {_plural(words, 'слово', 'слова', 'слов')} за {span} с — "
                f"{pace:.1f} слова в секунду, предел {MAX_WORDS_PER_SECOND}"
            )
        last_end = s["end"]
        title = s["title"] or ("Обложка" if s["n"] == 1 else "Финал")
        rows.append(f"| {s['n']} | {title} | {s['time']} | {words} | {s['notes']} |")
    minutes = f"{last_end // 60}:{last_end % 60:02d}"
    head = [
        "# Текст выступления",
        "",
        f"Заметки докладчика из `submission/slides/Mandat_VTB.pptx`, слайды 1–{data['pitch']}. Файл пишет "
        "`submission/deck/export_slides.py` после сборки колоды: правьте заметки в `build_deck.js`, а не здесь.",
        "",
        f"**Хронометраж.** {total_words} {_plural(total_words, 'слово', 'слова', 'слов')}, речь заканчивается "
        f"на {minutes}. Темп каждого блока — не выше "
        f"{str(MAX_WORDS_PER_SECOND).replace('.', ',')} слова в секунду.",
        "",
        "| № | Слайд | Время | Слов | Что сказать |",
        "|---|---|---|---|---|",
    ]
    NOTES_MD.write_text("\n".join(head + rows) + "\n", encoding="utf-8")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = export(Path(argv[0]), Path(argv[1]))
    problems = speaker_notes(data)
    print(f"слайдов: {len(data['slides'])}, питч: {data['pitch']}; картинки и slides.json — в {OUT.relative_to(ROOT)}")
    for p in problems:
        print("— " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
