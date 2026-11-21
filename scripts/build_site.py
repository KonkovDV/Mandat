"""Сайт проекта для GitHub Pages.

Страница — шаблон ``site/index.html``; числа подставляются из ``evidence.json``, календарь норм — из пакета правил,
отчёты демо-кейсов собирает ``mandat demo``. На сайт уходят только HTML-отчёты: ключи стенда, журнал и удостоверения
демо остаются во временном каталоге. Внешних запросов и скриптов у страницы нет: шрифт, иконки и стили лежат рядом.

    python scripts/build_site.py --out _site
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandat.data import load_json  # noqa: E402
from mandat.rules.pack import default_pack  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import effect_model  # noqa: E402

SITE = ROOT / "site"
# Шрифт сайта — WOFF2 с кириллицей и латиницей (site/fonts), примерно в шесть раз легче TTF из колоды.
FONTS = ("GolosText-Regular.woff2", "GolosText-SemiBold.woff2", "GolosText-Bold.woff2")
STATIC = (
    "style.css",
    "favicon.svg",
    "manifest.webmanifest",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "og.jpg",
)
MATERIALS = {
    "Mandat_VTB.pdf": ROOT / "submission" / "slides" / "Mandat_VTB.pdf",
    "Mandat_analytics_all.pdf": ROOT / "submission" / "analytics" / "out" / "Mandat_analytics_all.pdf",
}
VERDICTS = {
    "confirmed": ("подтверждено", "ok"),
    "refuted": ("опровергнуто", "no"),
    "needs_review": ("на разбор", "review"),
    "insufficient_data": ("недостаточно данных", "gap"),
}
REPO = "https://github.com/KonkovDV/Mandat"
# Адрес страницы на GitHub Pages: нужен только метаданным Open Graph, браузер по нему ничего не грузит.
SITE_URL = "https://konkovdv.github.io/Mandat/"


def _num(value: float, digits: int = 0) -> str:
    """Число по-русски: узкий неразрывный пробел между разрядами и запятая."""
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text


def _plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    if 11 <= tail <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _day(value: date | None) -> str:
    return f"{value:%d.%m.%Y}" if value is not None else "—"


def _demo_reports(target: Path, demo_dir: Path | None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = demo_dir
        if source is None:
            from mandat.cli import main

            source = Path(tmp) / "demo"
            with contextlib.redirect_stdout(io.StringIO()):
                if main(["demo", "--out", str(source)]) != 0:
                    raise SystemExit("mandat demo завершился с ошибкой")
        for report in sorted(source.glob("*.html")):
            shutil.copy2(report, target / report.name)


def _demo_rows(evidence: dict) -> str:
    rows = []
    for case_id, result in evidence["demo_cases"]["results"].items():
        manifest = json.loads((ROOT / "examples" / "cases" / case_id / "case.json").read_text(encoding="utf-8"))
        label, css = VERDICTS[result["verdict"]]
        rows.append(
            f'<tr><td><a href="reports/{html.escape(case_id)}.html">{html.escape(manifest.get("title", case_id))}</a>'
            f'</td><td><span class="badge {css}">{label}</span></td></tr>'
        )
    return "\n".join(rows)


def _law_rows() -> str:
    rows = []
    for norm in default_pack().norms:
        rules = ", ".join(f'<span class="rule">{html.escape(rule)}</span>' for rule in norm.rules)
        rows.append(
            f'<tr><td><a href="{html.escape(norm.url)}">{html.escape(norm.title)}</a></td>'
            f"<td>{_day(norm.in_force_from)}</td><td>{_day(norm.in_force_until)}</td><td>{rules}</td></tr>"
        )
    return "\n".join(rows)


def _months(value: float) -> str:
    """Срок окупаемости, как в колоде и пакете аналитики: до года — с десятыми («7,5 месяца»)."""
    rounded = round(value) if value >= 12 else round(value, 1)
    if float(rounded).is_integer():
        n = int(rounded)
        return f"{n} {_plural(n, 'месяц', 'месяца', 'месяцев')}"
    return f"{_num(rounded, 1)} месяца"


def economics() -> dict[str, str]:
    """Ценность для банка в базовом сценарии: те же значения, что в колоде, форме спринта и пакете аналитики.

    Юнит-экономика — ``scripts/effect_model.py --unit`` (C-21), окупаемость, эффект и ставки — модель пакета
    аналитики, операции без согласия у юрлиц — обзоры Банка России в ``effect_scenarios.json`` (M-07, M-15).
    """
    unit = effect_model.unit_economics()[1]
    bank = json.loads((ROOT / "submission" / "analytics" / "out" / "model_values.json").read_text(encoding="utf-8"))[
        "bank"
    ]
    effect = load_json("effect_scenarios.json")
    fte = int(bank["fte_round"][1])
    return {
        "unit_manual": _num(round(float(unit["manual_check_rub"]) / 10) * 10),
        "unit_mandat": _num(float(unit["mandat_cost_per_check_rub"])),
        "unit_ratio": _num(float(unit["manual_to_mandat"]), 1),
        "payback": _months(float(bank["payback_cash"][1])),
        "prevented": _num(float(bank["prevented_round"][1])),
        "fte": str(fte),
        "fte_word": _plural(fte, "ставка", "ставки", "ставок"),
        "incidents_h1": _num(2 * float(effect["incidentsPerQuarter"])),
        "loss_h1_bln": _num(2 * float(effect["lossPerQuarterMln"]) / 1000, 1),
    }


def values(evidence: dict) -> dict[str, str]:
    corpus = evidence["synthetic_corpus"]
    confirmed = corpus["confusion"].get("confirmed->confirmed", 0)
    negatives = corpus["cases"] - confirmed
    # Пока build_evidence прогоняет тесты, в evidence.json ещё нет их итога («tests»: null): страница
    # собирается с прочерком, а не падает.
    run = evidence.get("tests") or {}
    tests = run.get("passed")
    rules = evidence["rule_pack"]["rules"]
    return {
        "rules": str(rules),
        "rules_word": _plural(rules, "правило", "правила", "правил"),
        "tests": _num(tests) if tests is not None else "—",
        "tests_word": _plural(tests, "автотест", "автотеста", "автотестов") if tests is not None else "автотестов",
        "coverage": _num(run["coverage_percent"], 1) if "coverage_percent" in run else "—",
        "cases": _num(corpus["cases"]),
        "cases_word": _plural(corpus["cases"], "проверочный кейс", "проверочных кейса", "проверочных кейсов"),
        "defect_types": str(corpus["defect_types"]),
        "defect_word": _plural(corpus["defect_types"], "вида", "видов", "видов"),
        "negatives": _num(negatives),
        "false_confirmed": str(corpus["false_confirmed"]),
        "upper95": _num(corpus["false_confirmed_rate_upper95"] * 100, 1),
        "refute_recall": _num(corpus["refute_recall"] * 100, 0),
        "chains": _num(evidence["subdelegation_property"]["chains"]),
        "chains_word": _plural(evidence["subdelegation_property"]["chains"], "цепочка", "цепочки", "цепочек"),
        "median_ms": _num(evidence["latency"]["median_ms"], 1),
        "p95_ms": _num(evidence["latency"]["p95_ms"], 1),
        "engine": html.escape(evidence["engine"]),
        "pack_version": html.escape(evidence["rule_pack"]["version"]),
        "as_of": evidence["generated_at"][:10],
        "repo": REPO,
        "site_url": SITE_URL,
        "demo_rows": _demo_rows(evidence),
        "law_rows": _law_rows(),
        **economics(),
    }


def build(out: Path, demo_dir: Path | None = None) -> Path:
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    page = Template((SITE / "index.html").read_text(encoding="utf-8")).substitute(values(evidence))
    (out / "index.html").write_text(page, encoding="utf-8")
    for name in STATIC:
        shutil.copy2(SITE / name, out / name)
    fonts = out / "assets" / "fonts"
    fonts.mkdir(parents=True)
    for name in FONTS:
        shutil.copy2(SITE / "fonts" / name, fonts / name)
    shutil.copy2(ROOT / "submission" / "deck" / "fonts" / "OFL.txt", fonts / "OFL.txt")
    materials = out / "materials"
    materials.mkdir()
    for name, source in MATERIALS.items():
        shutil.copy2(source, materials / name)
    _demo_reports(out / "reports", demo_dir)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out / "index.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="_site", help="каталог сайта (по умолчанию _site)")
    parser.add_argument("--demo", help="готовый каталог mandat demo; без него демо собирается заново")
    args = parser.parse_args(argv)
    index = build(Path(args.out), Path(args.demo) if args.demo else None)
    print(f"сайт: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
