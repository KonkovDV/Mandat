"""Изображения из Figma — в колоду и на сайт: фото амбассадора, 3D-значки и картинка для превью ссылки.

    python submission/deck/import_visuals.py ПАПКА

В ПАПКЕ лежат файлы, выгруженные из Figma: ``anna-hero.png``,
``anna-final.png``, ``anna-vision.png``, ``anna-office.png``, ``bg-lux.png`` (фон тёмных слайдов) и ``icons-3d.png`` —
лист 3×3 с прозрачным фоном.
Промты, правила и где что используется — docs/VISUALS.md. Скрипт пишет:

* ``submission/deck/assets/visuals/`` — JPEG для колоды и PNG значков с прозрачностью;
* ``site/img/`` — WebP двух ширин для ``srcset`` и значки страниц;
* ``site/og.jpg`` — картинку 1200×630 для превью ссылки.

Нужен ImageMagick 6 (``convert``) с WebP и FreeType; шрифт — Golos Text из ``submission/deck/fonts``.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "submission" / "deck" / "assets" / "visuals"
SITE = ROOT / "site" / "img"
OG = ROOT / "site" / "og.jpg"
FONTS = ROOT / "submission" / "deck" / "fonts"

# Ширины для srcset: обычный экран и экран с двойной плотностью.
PHOTOS = {
    "anna-hero": (480, 960),
    "anna-final": (480, 960),
    "anna-vision": (480, 960),
    "anna-office": (768, 1536),
    "bg-lux": (768, 1536),
}
# Значки листа icons-3d.png по строкам слева направо — и страница сайта, которой каждый отдан.
ICONS = ("cases", "showcase", "verify", "economics", "pilot", "law", "criteria", "search", "secure")


def _im(*args: str) -> str:
    return subprocess.run(["convert", *args], check=True, capture_output=True, text=True).stdout.strip()


def _size(path: Path) -> tuple[int, int]:
    w, h = _im(str(path), "-format", "%w %h", "info:").split()
    return int(w), int(h)


def photos(src: Path) -> None:
    for name, widths in PHOTOS.items():
        image = src / f"{name}.png"
        _im(
            str(image),
            "-strip",
            "-sampling-factor",
            "4:2:0",
            "-interlace",
            "JPEG",
            "-quality",
            "88",
            str(DECK / f"{name}.jpg"),
        )
        for w in widths:
            _im(
                str(image),
                "-resize",
                f"{w}x",
                "-strip",
                "-quality",
                "80",
                "-define",
                "webp:method=6",
                str(SITE / f"{name}-{w}.webp"),
            )


def icons(src: Path) -> None:
    sheet = src / "icons-3d.png"
    w, h = _size(sheet)
    cell = min(w, h) // 3
    for i, name in enumerate(ICONS):
        x, y = (i % 3) * cell, (i // 3) * cell
        png = DECK / f"icon-{name}.png"
        # Значок из своей клетки листа, по центру квадрата того же размера: масштаб у всех один.
        # Почти прозрачная дымка вокруг значка на тёмной теме видна прямоугольником: её срезаем.
        _im(
            str(sheet),
            "-crop",
            f"{cell}x{cell}+{x}+{y}",
            "+repage",
            "-channel",
            "A",
            "-level",
            "6%,100%",
            "+channel",
            "-trim",
            "+repage",
            "-background",
            "none",
            "-gravity",
            "center",
            "-extent",
            f"{cell}x{cell}",
            str(png),
        )
        _im(
            str(png),
            "-quality",
            "88",
            "-define",
            "webp:alpha-quality=90",
            "-define",
            "webp:method=6",
            str(SITE / f"icon-{name}.webp"),
        )
        # Карточки обзора показывают значок в 76 px: им хватает 160 px (вдвое для экранов Retina), а не всей клетки.
        _im(
            str(png),
            "-resize",
            "160x160",
            "-quality",
            "86",
            "-define",
            "webp:alpha-quality=90",
            "-define",
            "webp:method=6",
            str(SITE / f"icon-{name}-160.webp"),
        )


def _logo_png(svg: Path, height: int, out: Path) -> None:
    """Логотип из docs/assets для превью: в SVG одни многоугольники (M … Z), поэтому он рисуется без SVG-движка —
    PIL с четырёхкратным сглаживанием и чётно-нечётной заливкой, чтобы у букв остались отверстия."""
    from PIL import Image, ImageChops, ImageColor, ImageDraw

    text = svg.read_text(encoding="utf-8")
    x0, y0, vw, vh = map(float, re.search(r'viewBox="([^"]+)"', text).group(1).split())
    k = 4
    scale = height * k / vh
    w, h = round(vw * scale), height * k
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for fill, d in re.findall(r'<path fill="(#[0-9A-Fa-f]{6})"[^>]*?\sd="([^"]+)"', text):
        mask = Image.new("1", (w, h), 0)
        for sub in re.findall(r"M([^MZ]+)Z", d):
            nums = [float(v) for v in sub.split()]
            pts = [((nums[i] - x0) * scale, (nums[i + 1] - y0) * scale) for i in range(0, len(nums), 2)]
            one = Image.new("1", (w, h), 0)
            ImageDraw.Draw(one).polygon(pts, fill=1)
            mask = ImageChops.logical_xor(mask, one)
        canvas.paste(Image.new("RGBA", (w, h), (*ImageColor.getrgb(fill), 255)), (0, 0), mask.convert("L"))
    canvas.convert("RGBa").resize((w // k, h // k), Image.Resampling.LANCZOS).convert("RGBA").save(out)


def og(src: Path) -> None:
    """Превью ссылки: вопрос продукта слева, амбассадор справа с мягким переходом в фон."""
    bold, regular = str(FONTS / "GolosText-Bold.ttf"), str(FONTS / "GolosText-Regular.ttf")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        # Фон — атласные волны из Figma; левая половина притемнена под текст, как на тёмных слайдах колоды.
        _im(
            str(src / "bg-lux.png"),
            "-resize",
            "1200x630^",
            "-gravity",
            "center",
            "-extent",
            "1200x630",
            "(",
            "-size",
            "630x700",
            "gradient:#061A4AE0-#061A4A00",
            "-rotate",
            "-90",
            ")",
            "-gravity",
            "west",
            "-compose",
            "over",
            "-composite",
            str(t / "bg.png"),
        )
        # Портрет 420×630, левые 45 % уходят в прозрачность.
        _im(
            str(src / "anna-hero.png"),
            "-resize",
            "x630",
            "-gravity",
            "center",
            "-crop",
            "420x630+0+0",
            "+repage",
            "(",
            "-size",
            "630x190",
            "gradient:black-white",
            "-rotate",
            "-90",
            "-size",
            "420x630",
            "xc:white",
            "+swap",
            "-gravity",
            "west",
            "-composite",
            ")",
            "-alpha",
            "off",
            "-compose",
            "copy_opacity",
            "-composite",
            str(t / "anna.png"),
        )
        # Логотип — тот же, что в README и на сайте, светлый вариант для тёмного фона.
        _logo_png(ROOT / "docs" / "assets" / "logo-on-dark.svg", 60, t / "logo.png")
        _im(
            str(t / "bg.png"),
            str(t / "anna.png"),
            "-gravity",
            "northwest",
            "-geometry",
            "+780+0",
            "-compose",
            "over",
            "-composite",
            str(t / "logo.png"),
            "-gravity",
            "northwest",
            "-geometry",
            "+80+74",
            "-compose",
            "over",
            "-composite",
            "-font",
            bold,
            "-fill",
            "white",
            "-pointsize",
            "56",
            "-interline-spacing",
            "6",
            "-annotate",
            "+80+190",
            "Можно ли этому человеку\nпровести эту операцию\nкомпании сегодня?",
            "-font",
            regular,
            "-pointsize",
            "26",
            "-fill",
            "#CFE3FF",
            "-annotate",
            "+80+452",
            "МЧД, выписка ЕГРЮЛ и устав — в одну\nцепочку полномочий на дату операции",
            "-fill",
            "#1A9F68",
            "-draw",
            "roundrectangle 80,540 452,592 26,26",
            "-stroke",
            "white",
            "-strokewidth",
            "4",
            "-fill",
            "none",
            "-draw",
            "polyline 102,566 111,575 127,557",
            "-stroke",
            "none",
            "-font",
            bold,
            "-fill",
            "white",
            "-pointsize",
            "24",
            "-annotate",
            "+140+551",
            "ответ с доказательством",
            "-strip",
            "-quality",
            "86",
            str(OG),
        )


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        print(__doc__)
        return 2
    src = Path(argv[0])
    DECK.mkdir(parents=True, exist_ok=True)
    SITE.mkdir(parents=True, exist_ok=True)
    photos(src)
    icons(src)
    og(src)
    for path in sorted([*DECK.iterdir(), *SITE.iterdir(), OG]):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
