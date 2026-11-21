"""Проверка PDF колоды: наезд текста, текст за краем слайда и за своей рамкой, слово, разорванное между строками,
и чужие шрифты.

    python submission/deck/qa_deck.py submission/slides/Mandat_VTB.pdf

Перенесено из InvaRadar (presentation/qa/check_collisions.py): слова сравниваются по рамкам из
``pdftotext -bbox-layout`` (poppler), а не строки целиком, поэтому число слева и подпись справа в одной строке
таблицы не считаются наложением. Шрифты — по ``pdffonts``: колода набрана Manrope, Golos Text и Cormorant Garamond;
если в них нет знака, LibreOffice наберёт его другим шрифтом, и он появится в списке. OpenSymbol допускается:
им LibreOffice рисует маркеры списков.

Разрыв слова: если слово не влезает в ширину рамки, LibreOffice рвёт его без дефиса («ДОКАЗАТЕЛЬСТ / ВА»).
Словарь берётся из PPTX рядом с PDF: конец строки и начало следующей в одном блоке, склеенные, дают слово слайда,
а сам конец строки словом слайда не является. Перенос после дефиса в составном слове допустим.

Текст за рамкой: LibreOffice не сжимает лишний текст, а рисует его поверх соседей. Последнее слово заголовка
не должно оставаться на строке одно. Рамка блока находится по тексту
и положению среди фигур слайда, у плейсхолдеров (заголовок, надзаголовок, источник) — по макету слайда.
Код выхода 1, если найдена хотя бы одна проблема.
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
import zipfile
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
LINE = re.compile(r'<line xMin="[\d.]+" yMin="[\d.]+" xMax="[\d.]+" yMax="[\d.]+">(.*?)</line>', re.S)
FONTS_OK = ("GolosText", "Manrope", "CormorantGaramond", "OpenSymbol")


def _lines(page: str):
    for m in LINE.finditer(page):
        words = [(float(a), float(b), float(c), float(d), html.unescape(w)) for a, b, c, d, w in WORD.findall(m.group(1))]
        if words:
            yield words


def _overlap(a, b) -> float:
    """Высота наложения двух слов, если они пересекаются заметно (больше трети высоты меньшего)."""
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix > 0.5 and iy > 0.3 * min(a[3] - a[1], b[3] - b[1]):
        return iy
    return 0.0


def collisions(pdf: str) -> list[str]:
    xml = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    out = []
    for n, page in enumerate(xml.split("<page ")[1:], 1):
        w = float(re.search(r'width="([\d.]+)"', page).group(1))
        h = float(re.search(r'height="([\d.]+)"', page).group(1))
        lines = list(_lines(page))
        for words in lines:
            if min(x[0] for x in words) < 1 or min(x[1] for x in words) < 1 or max(x[2] for x in words) > w - 1 or max(
                x[3] for x in words
            ) > h - 1:
                out.append(f"слайд {n}: текст за краем: {' '.join(x[4] for x in words)[:70]!r}")
        for i, a in enumerate(lines):
            for b in lines[i + 1 :]:
                hit = next(((_overlap(wa, wb), wa[4], wb[4]) for wa in a for wb in b if _overlap(wa, wb)), None)
                if hit:
                    out.append(f"слайд {n}: слова наезжают на {hit[0]:.1f} pt: {hit[1]!r} × {hit[2]!r}")
    return out


BLOCK = re.compile(r"<block\b[^>]*>(.*?)</block>", re.S)
EDGE = "«»„“”\"'()[]{}.,;:!?…*"
SPACES = re.compile(r"[\s   ]+")


def _bare(word: str) -> str:
    return word.strip(EDGE).replace("‑", "-").lower()


def _vocabulary(pptx: Path) -> dict[int, set[str]]:
    """Слова каждого слайда из XML колоды: текст абзаца целиком, пробелы любых видов — граница слова."""
    out: dict[int, set[str]] = {}
    with zipfile.ZipFile(pptx) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        for name in names:
            xml = z.read(name).decode("utf-8")
            words: set[str] = set()
            for p in re.findall(r"<a:p>(.*?)</a:p>", xml, re.S):
                text = html.unescape("".join(re.findall(r"<a:t>([^<]*)</a:t>", p)))
                words.update(_bare(w) for w in SPACES.split(text) if w)
            out[int(re.search(r"(\d+)", name.rsplit("/", 1)[1]).group(1))] = words
    return out


def _joined(line: str) -> list[str]:
    """Слова строки. При разрядке poppler режет слово на куски («ДО К АЗАТ»): куски с зазором меньше
    0,12 высоты строки склеиваются обратно (пробел между словами шире даже у крупного Manrope)."""
    out: list[str] = []
    prev = None
    for a, b, c, d, w in WORD.findall(line):
        x0, y0, x1, y1 = float(a), float(b), float(c), float(d)
        if prev is not None and x0 - prev < 0.12 * (y1 - y0):
            out[-1] += html.unescape(w)
        else:
            out.append(html.unescape(w))
        prev = x1
    return out


def broken(pdf: str, pptx: Path) -> list[str]:
    if not pptx.is_file():
        return [f"нет {pptx.name} рядом с PDF: без него разрывы слов не проверить"]
    vocab = _vocabulary(pptx)
    xml = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    out = []
    for n, page in enumerate(xml.split("<page ")[1:], 1):
        words = vocab.get(n, set())
        for block in BLOCK.findall(page):
            lines = [ws for ws in (_joined(line) for line in LINE.findall(block)) if ws]
            for a, b in zip(lines, lines[1:]):
                left, right = a[-1], b[0]
                if left.endswith(("-", "‑", "–", "—")):
                    continue
                if _bare(left + right) in words and _bare(left) not in words:
                    out.append(f"слайд {n}: слово разорвано между строками: {left!r} + {right!r}")
    return out


SHAPE = re.compile(r"<p:sp>(.*?)</p:sp>", re.S)
OFF = re.compile(r'<a:off x="(-?\d+)" y="(-?\d+)"/>\s*<a:ext cx="(\d+)" cy="(\d+)"/>')
EMU_PT = 12700


PH = re.compile(r"<p:ph\b([^>]*?)/?>")


def _ph(sp: str) -> tuple[str, str] | None:
    """Плейсхолдер фигуры: тип (title, body…) и номер idx, как в макете."""
    m = PH.search(sp)
    if not m:
        return None
    kind = re.search(r'type="(\w+)"', m.group(1))
    idx = re.search(r'idx="(\d+)"', m.group(1))
    return (kind.group(1) if kind else "body", idx.group(1) if idx else "")


def _layout_geometry(z: zipfile.ZipFile, slide: str) -> dict[tuple[str, str], re.Match[str]]:
    """Геометрия плейсхолдеров макета, к которому привязан слайд: у самого слайда её нет."""
    rels = z.read(slide.replace("slides/", "slides/_rels/") + ".rels").decode("utf-8")
    target = re.search(r'Target="\.\./slideLayouts/(slideLayout\d+\.xml)"', rels)
    if not target:
        return {}
    out = {}
    for sp in SHAPE.findall(z.read(f"ppt/slideLayouts/{target.group(1)}").decode("utf-8")):
        key, geo = _ph(sp), OFF.search(sp)
        if key and geo:
            out[key] = geo
    return out


def _boxes(pptx: Path) -> dict[int, list[tuple[float, float, float, float, str, str]]]:
    """Текстовые рамки слайдов: x, y, ширина, высота в пунктах и текст без пробелов; у плейсхолдеров — из макета."""
    out: dict[int, list[tuple[float, float, float, float, str, str]]] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if not m:
                continue
            layout = _layout_geometry(z, name)
            boxes = []
            for sp in SHAPE.findall(z.read(name).decode("utf-8")):
                geo = OFF.search(sp)
                key = _ph(sp)
                if geo is None and key is not None:
                    geo = layout.get(key) or next((g for k, g in layout.items() if k[0] == key[0]), None)
                text = html.unescape("".join(re.findall(r"<a:t>([^<]*)</a:t>", sp)))
                if geo and text.strip():
                    x, y, w, h = (int(v) / EMU_PT for v in geo.groups())
                    boxes.append((x, y, w, h, SPACES.sub("", text).lower(), key[0] if key else ""))
            out[int(m.group(1))] = boxes
    return out


def overflow(pdf: str, pptx: Path) -> list[str]:
    """Текст, который вылез за свою рамку сверху или снизу: LibreOffice не сжимает его, а рисует поверх соседей."""
    if not pptx.is_file():
        return []
    boxes = _boxes(pptx)
    xml = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    out = []
    for n, page in enumerate(xml.split("<page ")[1:], 1):
        for m in re.finditer(r'<block xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</block>', page, re.S):
            x0, y0, x1, y1 = (float(v) for v in m.groups()[:4])
            lines = [ws for ws in (_joined(line) for line in LINE.findall(m.group(5))) if ws]
            if not lines:
                continue
            # Рамка блока: весь его текст из неё, по горизонтали он внутри, по вертикали они пересекаются.
            # Подписи диаграмм и ячейки таблиц своей рамки среди фигур не имеют и не проверяются.
            # Маркеры списков («•», «1.») рисует LibreOffice, в тексте фигуры их нет.
            marks = [ws[1:] if ws[0] == "•" or re.fullmatch(r"\d+\.", ws[0]) else ws for ws in lines]
            text = SPACES.sub("", "".join("".join(ws) for ws in marks)).lower()
            fits = [
                b for b in boxes.get(n, [])
                if text in b[4] and b[0] - 3 <= x0 and x1 <= b[0] + b[2] + 3 and y0 < b[1] + b[3] and y1 > b[1]
            ]
            if not fits:
                continue
            bx, by, bw, bh, _, kind = min(fits, key=lambda b: abs(b[0] - x0) + abs(b[1] - y0))
            if kind == "title" and len(lines) > 1 and len(lines[-1]) == 1:
                out.append(f"слайд {n}: в заголовке последнее слово одно на строке: {lines[-1][0]!r}")
            # Рамка слова в PDF — по выносным элементам шрифта, она выше кегля: допуск — шестая часть строки.
            tol = max(3.0, (y1 - y0) / len(lines) / 6)
            if y1 > by + bh + tol or y0 < by - tol:
                out.append(f"слайд {n}: текст вылез за рамку на {max(y1 - by - bh, by - y0):.0f} pt: {' '.join(lines[-1])[:60]!r}")
    return out


def fonts(pdf: str) -> list[str]:
    listing = subprocess.run(["pdffonts", pdf], capture_output=True, text=True, check=True).stdout.splitlines()[2:]
    out = []
    for row in listing:
        name = row.split()[0].split("+")[-1]
        if not name.startswith(FONTS_OK):
            out.append(f"в PDF чужой шрифт: {name} — знак не нашёлся в Golos Text")
        if row.split()[-5] != "yes":
            out.append(f"шрифт не встроен: {name}")
    return out


def main(argv: list[str]) -> int:
    problems = []
    for pdf in argv:
        pptx = Path(pdf).with_suffix(".pptx")
        problems += [f"{pdf}: {p}" for p in collisions(pdf) + broken(pdf, pptx) + overflow(pdf, pptx) + fonts(pdf)]
    for p in problems:
        print("— " + p)
    print(f"проблем: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
