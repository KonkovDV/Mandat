"""Статичные карточки для жюри: копирует три отчёта `mandat demo` в docs/demo/ и, если есть
Playwright с Chromium, снимает с них PNG 1280×800.

    mandat demo && python scripts/export_demo_cards.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ("01_ok", "10_old_director_poa", "05_director_changed")


def export(src: Path, dst: Path) -> list[Path]:
    dst.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in CARDS:
        html = src / f"{name}.html"
        if not html.is_file():
            raise FileNotFoundError(f"нет {html}: сначала выполните mandat demo")
        target = dst / html.name
        shutil.copyfile(html, target)
        copied.append(target)
    return copied


def screenshots(pages: list[Path]) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright не установлен: картинки пропущены", file=sys.stderr)
        return False
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        for html in pages:
            page.goto(html.resolve().as_uri())
            page.screenshot(path=str(html.with_suffix(".png")))
        browser.close()
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="карточки демо-кейсов в docs/demo/")
    p.add_argument("--src", default=str(ROOT / "var" / "demo"))
    p.add_argument("--out", default=str(ROOT / "docs" / "demo"))
    args = p.parse_args()
    pages = export(Path(args.src), Path(args.out))
    shots = screenshots(pages)
    for html in pages:
        print(html.relative_to(ROOT) if html.is_relative_to(ROOT) else html)
    print("PNG сняты" if shots else "PNG не сняты")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
