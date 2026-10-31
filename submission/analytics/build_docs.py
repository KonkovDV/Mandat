"""Документы пакета аналитики: шаблоны Markdown → числа из модели и evidence.json → HTML → PDF.

    python submission/analytics/build_model.py              # сначала модель: out/model_values.json
    python submission/analytics/build_docs.py               # PDF в out/
    python submission/analytics/build_docs.py --package DIR # ещё и папка для облачного диска с русскими именами

Числа в документах не набираются руками: о продукте — из evidence.json и tests/test_redteam_fuzz.py, эффект,
рынок и финансы — из пересчитанной модели. Шаблоны лежат в templates/ (Jinja2 с разделителями [[ ]] и [% %],
чтобы не спорить с разметкой pandoc). Готовый текст проверяется теми же правилами, что и репозиторий
(scripts/lint_claims.py): запрещённые формулировки, число правил, автотестов, демо-кейсов и мутаций, согласование
числа и слова. Нужны pandoc 3, Node.js с Playwright (Chromium) и Python-пакеты jinja2, pypdf, openpyxl, fontTools.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import jinja2
from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
BUILD = ROOT / "build" / "analytics"
DECK = ROOT / "submission" / "deck"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

import charts as ch  # noqa: E402
import lint_claims  # noqa: E402
from ru_plural import plural  # noqa: E402

NB = " "
AUTHOR = "Команда «Мандата»"
REPO = "https://github.com/KonkovDV/Mandat"

# id, файл в репозитории, имя в папке для диска, заголовок, подзаголовок обложки, номер в пакете
DOCS = [
    (
        "01_project",
        "Mandat_01_project",
        "01_Описание_проекта",
        "Описание проекта",
        "Задача банка, решение, что работает сегодня, ценность, бизнес-модель и зрелость",
        "01",
    ),
    (
        "02_model",
        "Mandat_02_financial_model_notes",
        "02_Финансовая_модель_пояснения",
        "Финансовая модель",
        "Эффект для ВТБ, выручка и затраты проекта, деньги, сценарии и чувствительность",
        "02",
    ),
    (
        "03_market",
        "Mandat_03_market",
        "03_Оценка_рынка",
        "Оценка рынка",
        "Кто платит и сколько, почему сейчас, TAM, SAM и SOM двумя способами, конкуренты",
        "03",
    ),
    (
        "04_plan",
        "Mandat_04_plan",
        "04_План_внедрения_и_развития",
        "План внедрения и развития",
        "От соревнования к пилоту и тиражированию: сроки, ворота перехода, ресурсы, риски",
        "04",
    ),
    (
        "00_overview",
        "Mandat_00_overview",
        "00_Читать_первым",
        "Пакет аналитики",
        "Что внутри, главные числа, что готово к этому этапу и как всё проверить",
        "00",
    ),
]


# ---------------------------------------------------------------- данные


def ru_date(iso: str) -> str:
    return ".".join(reversed(iso[:10].split("-")))


def evidence() -> dict:
    ev = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    sc = ev["synthetic_corpus"]
    expected = {"confirmed": 0, "refuted": 0, "needs_review": 0, "insufficient_data": 0}
    for pair, count in sc["confusion"].items():
        expected[pair.split("->")[0]] += count
    return {
        "version": ev["engine"].split("/")[1],
        "as_of": ru_date(ev["generated_at"]),
        "rules": ev["rule_pack"]["rules"],
        "demo": len(ev["demo_cases"]["results"]),
        "tests": ev["tests"]["passed"],
        "test_coverage": ev["tests"]["coverage_percent"] / 100,
        "cases": sc["cases"],
        "kinds": sc["defect_types"],
        "agree": round(sc["cases"] * sc["agreement"]),
        "expected": expected,
        "negatives": sc["cases"] - expected["confirmed"],
        "false_confirmed": sc["false_confirmed"],
        "fc_upper": sc["false_confirmed_rate_upper95"],
        "fc_upper_type": sc["false_confirmed_rate_upper95_by_defect_type"],
        "negative_types": sc["negative_defect_types"],
        "recall_low": sc["refute_recall_wilson95"][0],
        "coverage": sc["coverage"],
        "auto_risk_upper": sc["auto_risk_upper95"],
        "abstention": sc["abstention_rate"],
        "strict_coverage": ev["synthetic_corpus_strict"]["coverage"],
        "holdout_mismatches": ev["synthetic_corpus_holdout"]["mismatches"],
        "n_recall": sc["n_for_recall_0_9_pm_0_05"],
        "chains": ev["subdelegation_property"]["chains"],
        "median_ms": ev["latency"]["median_ms"],
        "p95_ms": ev["latency"]["p95_ms"],
        "fuzz": lint_claims.fuzz_runs(),
    }


def case14() -> dict:
    base = ROOT / "examples" / "cases" / "14_old_director_charter_cleared"
    egrul = json.loads((base / "egrul.json").read_text(encoding="utf-8"))
    kase = json.loads((base / "case.json").read_text(encoding="utf-8"))
    poa = (base / "poa-root.xml").read_text(encoding="utf-8")

    def attr(name: str) -> str:
        m = re.search(rf'{name}="([^"]+)"', poa)
        if m is None:
            raise SystemExit(f"в poa-root.xml нет атрибута {name}")
        return m.group(1)

    prev, nxt = egrul["executives"]
    return {
        "org": egrul["name"],
        "prev": prev,
        "next": nxt,
        "issued": attr("ДатаВыдДовер"),
        "until": attr("СрокДейст"),
        "op": kase["operation"],
        "extracted": egrul["extracted_at"],
    }


def model() -> dict:
    path = OUT / "model_values.json"
    if not path.exists():
        raise SystemExit("нет out/model_values.json: сначала python submission/analytics/build_model.py")
    return json.loads(path.read_text(encoding="utf-8"))


def deck_slides() -> int:
    with zipfile.ZipFile(ROOT / "submission" / "slides" / "Mandat_VTB.pptx") as z:
        return sum(1 for name in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name))


def formulas_count() -> int:
    wb = load_workbook(OUT / "Mandat_financial_model.xlsx")
    return sum(
        1
        for ws in wb.worksheets
        for row in ws.iter_rows()
        for c in row
        if isinstance(c.value, str) and c.value.startswith("=")
    )


# ---------------------------------------------------------------- форматирование для шаблонов


def n(x: float | int | None, d: int = 1) -> str:
    return ch.ru(x, d)


def pct(x: float, d: int = 0) -> str:
    return f"{ch.ru(100 * x, d)}{NB}%"


def pl(k: float, one: str, few: str, many: str) -> str:
    if not float(k).is_integer():
        return few
    return plural(int(k), one, few, many)


def cp(k: int, stem: str) -> str:
    forms = {
        "правил": ("правило", "правила", "правил"),
        "автотест": ("автотест", "автотеста", "автотестов"),
        "демо-кейс": ("демо-кейс", "демо-кейса", "демо-кейсов"),
        "банк": ("банк", "банка", "банков"),
        "кейс": ("кейс", "кейса", "кейсов"),
        "месяц": ("месяц", "месяца", "месяцев"),
        "ставка": ("ставка", "ставки", "ставок"),
        "год": ("год", "года", "лет"),
        "сотрудник": ("сотрудник", "сотрудника", "сотрудников"),
        "лист": ("лист", "листа", "листов"),
        "формула": ("формула", "формулы", "формул"),
        "страница": ("страница", "страницы", "страниц"),
        "мутация": ("мутация", "мутации", "мутаций"),
        "цепочка": ("цепочка", "цепочки", "цепочек"),
        "операция": ("операция", "операции", "операций"),
    }[stem]
    num = ch.ru(k, 0) if float(k).is_integer() else ch.ru(k, 1)
    return f"{num} {pl(k, *forms)}"


def months(x: float | str) -> str:
    if isinstance(x, str):
        return x
    # До года — с десятыми, как подписи графика окупаемости: «7,5 месяца», а не «7 месяцев» рядом с «7,5 мес.».
    v = round(x) if x >= 12 else round(x, 1)
    return f"{ch.ru(v, 0 if float(v).is_integer() else 1)} {pl(v, 'месяц', 'месяца', 'месяцев')}"


def yr(x: float | str) -> str:
    return x if isinstance(x, str) else str(int(x))


def bln(x: float, d: int = 2) -> str:
    """Млн ₽ → млрд ₽ строкой."""
    return ch.ru(x / 1000, d)


TAGS = {
    "факт": ("fact", "факт"),
    "вторичный": ("sec", "вторичный"),
    "допущение": ("asm", "допущение"),
    "гипотеза": ("hyp", "гипотеза"),
    "репозиторий": ("repo", "репозиторий"),
}


def tag(kind: str) -> str:
    cls, label = TAGS[kind]
    return f'<span class="tag {cls}">{label}</span>'


# ---------------------------------------------------------------- графики документов


class Figures:
    """Реестр графиков документа: шаблон вызывает fig(...), после pandoc метка заменяется на <figure>."""

    def __init__(self, ctx: dict) -> None:
        self.ctx = ctx
        self.items: dict[str, tuple[str, str, str, str]] = {}
        self.raw: list[str] = []

    def fig(self, name: str, title: str, sub: str, src: str) -> str:
        self.items[name] = (title, sub, src, BUILDERS[name](self.ctx, name, title))
        return f"\n\n@@FIG:{name}@@\n\n"

    def html(self, fragment: str) -> str:
        self.raw.append(fragment)
        return f"\n\n@@RAW:{len(self.raw) - 1}@@\n\n"

    def kpis(self, tiles: list[tuple[str, str, str]], cols: int = 4) -> str:
        cls = {4: "kpis", 3: "kpis k3", 2: "kpis k2"}[cols]
        body = "".join(
            f'<div class="kpi"><div class="v">{v}</div><div class="l">{html.escape(label)}</div>'
            f'<div class="s">{html.escape(src)}</div></div>'
            for v, label, src in tiles
        )
        return self.html(f'<div class="{cls}">{body}</div>')


def _years(ctx: dict) -> list[str]:
    return [str(y) for y in ctx["years"]]


def _scen(ctx: dict, key: str) -> list[tuple[str, str, list[float]]]:
    return [(name, ch.SCENARIO[s], ctx["sc"][s]["s"][key]) for s, name in enumerate(ctx["S"])]


BUILDERS = {
    "pipeline": lambda c, i, t: ch.pipeline(
        i,
        [
            ("Пакет документов", "МЧД, выписка ЕГРЮЛ, устав, заявка на операцию"),
            ("Разбор", "XML МЧД по схеме, ЕГРЮЛ, устав; хеш каждого документа"),
            (f"{c['ev']['rules']} правил", "у каждого норма, следующий шаг и тест"),
            ("Вердикт", "один из четырёх и сильнейшее возражение с доказательством"),
            ("Удостоверение и журнал", "подпись, статус «действует», запись с доказательством включения"),
            ("Мониторинг", "события ЕГРЮЛ и реестра: отзыв или приостановка"),
        ],
        title=t,
        desc="Шесть шагов проверки: пакет документов, разбор, правила, вердикт, удостоверение и журнал, мониторинг.",
    ),
    "corpus": lambda c, i, t: ch.stacked_bar(
        i,
        [
            ("на разбор", c["ev"]["expected"]["needs_review"], ch.VERDICT["needs_review"], ch.TEXT),
            ("опровергнуто", c["ev"]["expected"]["refuted"], ch.VERDICT["refuted"], "#FFFFFF"),
            (
                "недостаточно данных",
                c["ev"]["expected"]["insufficient_data"],
                ch.VERDICT["insufficient_data"],
                "#FFFFFF",
            ),
            ("подтверждено", c["ev"]["expected"]["confirmed"], ch.VERDICT["confirmed"], "#FFFFFF"),
        ],
        title=t,
        desc=(
            f"Синтетический корпус из {c['ev']['cases']} кейсов по ожидаемому вердикту: на разбор "
            f"{c['ev']['expected']['needs_review']}, опровергнуто {c['ev']['expected']['refuted']}, недостаточно "
            f"данных {c['ev']['expected']['insufficient_data']}, подтверждено {c['ev']['expected']['confirmed']}."
        ),
    ),
    "case14": lambda c, i, t: _case14(c, i, t),
    "revenue": lambda c, i, t: ch.grouped_columns(
        i, _years(c), _scen(c, "revenue"), title=t, desc="Выручка проекта по годам в трёх сценариях, млн ₽ без НДС."
    ),
    "cum": lambda c, i, t: ch.lines(
        i,
        _years(c),
        _scen(c, "cum"),
        title=t,
        desc="Накопленный денежный поток проекта до внешнего финансирования по сценариям, млн ₽.",
    ),
    "banks": lambda c, i, t: ch.grouped_columns(
        i,
        _years(c),
        _scen(c, "banks"),
        title=t,
        unit="банков",
        digits=0,
        desc="Число банков с договором на конец года по сценариям.",
    ),
    "payback": lambda c, i, t: ch.hbars(
        i,
        [(name, c["bank"]["payback_cash"][s], ch.SCENARIO[s]) for s, name in enumerate(c["S"])],
        title=t,
        digits=1,
        value_suffix=" мес.",
        desc="Срок окупаемости разовых затрат ВТБ по денежному эффекту, месяцев, по сценариям.",
    ),
    "costs": lambda c, i, t: _costs(c, i, t),
    "tornado": lambda c, i, t: ch.tornado(
        i,
        [(r["name"], r["low"], r["high"]) for r in c["tornado"]],
        c["tornado"][0]["base"],
        title=t,
        desc="Чувствительность NPV базового сценария: каждое допущение меняется на ±20 %.",
    ),
    "tamsam": lambda c, i, t: _tamsam(c, i, t),
    "regs": lambda c, i, t: _regs(c, i, t),
    "map": lambda c, i, t: _map(c, i, t),
    "gantt": lambda c, i, t: _gantt(c, i, t),
    "risks": lambda c, i, t: ch.heatmap(
        i,
        [(r[0], r[2], r[3]) for r in RISKS],
        title=t,
        desc="Тепловая карта рисков: вероятность и влияние по шкале от 1 до 5.",
    ),
    "team": lambda c, i, t: ch.grouped_columns(
        i,
        _years(c),
        _scen(c, "hc"),
        title=t,
        unit="ставок",
        digits=1,
        desc="Численность команды на конец года по сценариям, ставок.",
    ),
}


def _case14(c: dict, i: str, t: str) -> str:
    k = c["c14"]
    d = date.fromisoformat
    prev, nxt = k["prev"], k["next"]
    op = d(k["op"]["date"])
    lanes = [
        (
            "Руководитель по ЕГРЮЛ",
            [
                (d(prev["valid_from"]), d(prev["valid_to"]), ch.LIGHT, "прежний руководитель"),
                (d(nxt["valid_from"]), date(2027, 6, 30), ch.COBALT, "новый руководитель"),
            ],
        ),
        ("МЧД, выдана прежним", [(d(k["issued"]), d(k["until"]), "#1A9F68", "срок действия МЧД")]),
        ("Реестр МЧД ФНС", [(d(k["issued"]), date(2027, 6, 30), ch.CONTEXT, "статус «действует»")]),
    ]
    return ch.intervals(
        i,
        lanes,
        (op, f"операция {ru_date(k['op']['date'])}"),
        date(2024, 1, 1),
        date(2027, 6, 30),
        title=t,
        desc=(
            f"Демо-кейс 14: МЧД выдана прежним руководителем {ru_date(k['issued'])}, руководитель "
            f"сменился {ru_date(nxt['valid_from'])}, операция {ru_date(k['op']['date'])} попадает "
            "в срок МЧД, реестр показывает «действует»."
        ),
    )


def _costs(c: dict, i: str, t: str) -> str:
    s = c["sc"][1]["s"]
    rows = [
        ("Фонд оплаты труда", s["payroll"][-1]),
        ("Страховые взносы", s["contrib"][-1]),
        ("Продажи и маркетинг", s["cost_sales"][-1]),
        ("Административные", s["cost_admin"][-1]),
        ("Резерв", s["cost_res"][-1]),
        ("Оценка ИБ", s["cost_sec"][-1]),
        ("Инфраструктура", s["cost_infra"][-1]),
        ("Юридическая экспертиза", s["cost_legal"][-1]),
        ("Оборудование", s["cost_hw"][-1]),
    ]
    rows.sort(key=lambda r: -r[1])
    return ch.hbars(
        i,
        [(label, v, ch.COBALT) for label, v in rows],
        title=t,
        unit="млн ₽, 2031 год",
        desc="Затраты проекта в 2031 году в базовом сценарии по статьям, млн ₽.",
    )


def _tamsam(c: dict, i: str, t: str) -> str:
    m = c["mk"]
    rows = [
        ("TAM, полный охват операций", m["tam_full"][1], ch.SCENARIO[0]),
        ("TAM, одна операция", m["tam"][1], ch.SCENARIO[0]),
        ("SAM, полный охват операций", m["sam_full"][1], ch.SCENARIO[1]),
        ("SAM, одна операция", m["sam"][1], ch.SCENARIO[1]),
        ("SOM, подписка на конец 2031 года", m["som"][1], ch.SCENARIO[2]),
    ]
    return ch.hbars(
        i,
        rows,
        title=t,
        unit="млн ₽ в год, цены 2027 года, базовый сценарий",
        desc="TAM, SAM и SOM базового сценария, млн ₽ в год.",
    )


def _regs(c: dict, i: str, t: str) -> str:
    ev = [
        (date(2024, 9, 1), "01.09.2024\nКЭП представителя\nи МЧД (476-ФЗ)", "ru"),
        (date(2026, 2, 1), "01.02.2026\nреквизиты МЧД\n(Приказ № 1001)", "ru"),
        (date(2026, 9, 1), "01.09.2026\n210-ФЗ, первый\nэтап", "ru"),
        (date(2026, 9, 30), "30.09.2026\nфинальный проект\nRTS AMLA", "eu"),
        (date(2027, 7, 10), "10.07.2027\nРегламент ЕС\n2024/1624", "eu"),
        (date(2027, 9, 1), "01.09.2027\n315-ФЗ: единая\nформа МЧД", "ru"),
        (date(2028, 1, 1), "01.01.2028\n210-ФЗ,\nпоследний этап", "ru"),
        (date(2028, 7, 31), "31.07.2028\nДиректива ЕС\n2025/25", "eu"),
    ]
    return ch.calendar(
        i,
        ev,
        date(2024, 6, 1),
        date(2028, 12, 31),
        title=t,
        desc="Нормы о представителях и МЧД в России, ЕС и FATF с 2024 по 2028 год.",
    )


def _map(c: dict, i: str, t: str) -> str:
    players = [
        ("Реестр МЧД ФНС", 0.18, 0.52, False),
        ("Контур.Доверенность, Астрал, Такском, 1С-ЭДО, Saby", 0.08, 0.18, False),
        ("Контур.Диадок: проверка МЧД под документом", 0.33, 0.34, False),
        ("Правила в АБС и ДБО банка", 0.55, 0.8, False),
        ("Ручная проверка сотрудником", 0.66, 0.62, False),
        ("«Мандат»", 0.9, 0.86, True),
    ]
    return ch.positioning(
        i,
        players,
        x_axis=("статус и срок документа", "полномочие на операцию на дату"),
        y_axis=("выпуск и хранение МЧД", "решение банка, который МЧД принимает"),
        title=t,
        desc="Качественная карта: по горизонтали — что проверяется, по вертикали — чью задачу решает продукт.",
    )


def _gantt(c: dict, i: str, t: str) -> str:
    d = date
    rows = [
        ("Спринт 1: срез, интервью, аналитика", d(2026, 10, 1), d(2026, 11, 1), "work"),
        ("Онлайн-питч", d(2026, 11, 10), d(2026, 11, 15), "work"),
        ("Спринт 2: строгий профиль", d(2026, 11, 16), d(2026, 11, 29), "work"),
        ("Спринт 3: протокол пилота", d(2026, 12, 3), d(2026, 12, 8), "work"),
        ("Акселератор ВТБ, NDA, протокол", d(2026, 12, 12), d(2027, 2, 26), "prep"),
        ("Компания, аккредитация ИТ, реестр ПО", d(2027, 1, 11), d(2027, 6, 30), "prep"),
        ("Теневой пилот, 6 недель", d(2027, 3, 1), d(2027, 4, 12), "pilot"),
        ("Решение и договор", d(2027, 4, 13), d(2027, 6, 30), "prep"),
        ("Адаптеры, СКЗИ, строгий профиль", d(2027, 5, 1), d(2027, 9, 30), "work"),
        ("Анализ уязвимостей по ОУД4", d(2027, 7, 1), d(2027, 10, 31), "work"),
        ("Формы МЧД по 315-ФЗ", d(2027, 6, 1), d(2027, 8, 31), "work"),
        ("Эксплуатация первой операции", d(2027, 11, 1), d(2028, 6, 30), "pilot"),
        ("Новые операции, банки уровня 2", d(2028, 1, 10), d(2028, 6, 30), "work"),
    ]
    marks = [
        (d(2026, 12, 11), "финал соревнования, 11.12.2026"),
        (d(2027, 3, 1), "G1 — старт пилота, ≈01.03.2027"),
        (d(2027, 4, 15), "G2 — итог пилота, ≈15.04.2027"),
        (d(2027, 9, 1), "315-ФЗ вступает в силу, 01.09.2027"),
        (d(2027, 11, 1), "G3 — эксплуатация, ≈01.11.2027"),
        (d(2028, 6, 30), "G4 — тиражирование, ≈30.06.2028"),
    ]
    return ch.gantt(
        i,
        rows,
        marks,
        d(2026, 10, 1),
        d(2028, 7, 1),
        title=t,
        desc="План с октября 2026 по июнь 2028: соревнование, подготовка, теневой пилот, внедрение и тиражирование.",
    )


# Реестр рисков: номер, риск, вероятность 1–5, влияние 1–5, признак, мера, владелец.
RISKS = [
    (
        "Р1",
        "Ложное «подтверждено» на реальных пакетах",
        2,
        5,
        "любой случай в пилоте",
        "стоп-критерий пилота, строгий профиль, независимая разметка, попытка построить ложное «да»",
        "архитектор правил",
    ),
    (
        "Р2",
        "Нет данных банка для пилота",
        3,
        4,
        "к неделе 1 нет пакетов и тестового контура",
        "готовый корпус из 49 видов ситуаций и разметка экспертами банка; запрос данных с первого созвона",
        "капитан",
    ),
    (
        "Р3",
        "ИБ-требования удлиняют внедрение",
        3,
        4,
        "нет решения ИБ через 4 недели после пилота",
        "ранний анализ уязвимостей, документы ИБ, работа только в контуре банка",
        "инженер качества и ИБ",
    ),
    (
        "Р4",
        "Банк решает сделать сам",
        3,
        4,
        "запрос исходников вместо договора",
        "открытое ядро, цена ниже собственной разработки, сопровождение правил юристом",
        "капитан",
    ),
    (
        "Р5",
        "Юрист банка трактует норму иначе",
        3,
        3,
        "несогласие по правилу",
        "правила-данные со статусом, утверждение юристом до пилота, журнал изменений",
        "юрист-аналитик",
    ),
    (
        "Р6",
        "Меняются нормы и формат МЧД",
        4,
        3,
        "новый приказ, 315-ФЗ",
        "реестр форматов, годовое обновление правил в подписке, мониторинг норм",
        "юрист-аналитик",
    ),
    (
        "Р7",
        "Команда из двух человек",
        3,
        4,
        "болезнь, уход, перегрузка",
        "найм разработчика и юриста в 2027 году, документация, воспроизводимая сборка",
        "капитан",
    ),
    (
        "Р8",
        "Нет финансирования до первой выручки",
        3,
        4,
        "к марту 2027 нет денег на команду",
        "гранты, акселератор, поэтапный найм, договор с оплатой внедрения",
        "капитан",
    ),
    (
        "Р9",
        "Интеграция с ДБО и АБС сложнее плана",
        3,
        3,
        "сдвиг сроков адаптеров",
        "порты и OpenAPI готовы, работа с ИТ банка с первой недели",
        "инженер внедрения",
    ),
    (
        "Р10",
        "Персональные данные и модель для устава",
        2,
        4,
        "требование вывезти данные",
        "работа только в контуре, модель выключена по умолчанию, псевдонимизация",
        "инженер качества и ИБ",
    ),
    (
        "Р11",
        "Операторы ЭДО добавят похожую функцию",
        2,
        3,
        "анонс функции проверки полномочий",
        "фокус на решении банка, партнёрство и встройка как компонент",
        "капитан",
    ),
]


# ---------------------------------------------------------------- типографика


LETTER = "А-Яа-яЁёA-Za-z"


def typo(s: str) -> str:
    """Неразрывные пробелы и дефисы, как в презентации (build_deck.js, typo)."""
    # Просмотр назад, а не захват: цепочка коротких слов («и с доказательством») связывается за один проход.
    s = re.sub(rf"(?:^|(?<=[\s«„(/]))([{LETTER}]{{1,2}})\s+(?=[{LETTER}0-9«„(№])", rf"\1{NB}", s)
    s = re.sub(r"[ \t]+—", f"{NB}—", s)
    s = re.sub(r"(?<![\d.])(\d+(?:,\d+)?)[ \t]+(?=[а-яё₽%]|ФЗ|МЧД)", rf"\1{NB}", s)
    s = re.sub(r"[ \t]+(?=₽)", NB, s)
    s = re.sub(r"([≈≤≥×÷])[ \t]+(?=\d)", rf"\1{NB}", s)
    s = re.sub(r"(\d)[ \t]+/[ \t]+(?=\d)", rf"\1{NB}/{NB}", s)
    s = re.sub(r"[ \t]+(?=[№§])", NB, s)
    s = re.sub(r"([№§])[ \t]+", rf"\1{NB}", s)
    s = re.sub(r"(RFC|ISO|ГОСТ Р)[ \t]+(?=\d)", rf"\1{NB}", s)

    def hyphen(m: re.Match[str]) -> str:
        pre, left = m.group(1), m.group(2)
        right = re.match(r"[А-ЯЁа-яё0-9]+", s[m.end() :])
        if right and re.search("[А-ЯЁа-яё]", left + right.group(0)):
            return f"{pre}{left}‑"
        return m.group(0)

    s = re.sub(r"(^|[\s«(„])([А-ЯЁа-яёA-Z0-9]{1,6})-(?=[А-ЯЁа-яё0-9]+(?=[\s,.;:»)“]|$))", hyphen, s)
    s = re.sub(rf"(?<![{LETTER}])(ст|п|подп|ч|гл|г|кв)\.[ \t]+(?=[0-9IVX])", rf"\1.{NB}", s)
    s = re.sub(r"([А-ЯЁ]\.)[ \t]+(?=[А-ЯЁ]\.)", rf"\1{NB}", s)
    s = re.sub(r"ГК[ \t]+РФ", f"ГК{NB}РФ", s)
    # Коды строк реестра («M-07», «C-15»), номера судебных дел и стандартов не рвутся на дефисе.
    s = re.sub(r"(?<![\w-])([MCRS])-(\d{2,3})(?![\w-])", "\\1‑\\2", s)
    s = re.sub(r"(\d{3})-([А-ЯЁ]{2}\d{2})-(\d+)", "\\1‑\\2‑\\3", s)
    s = re.sub(r"((?:ISO|ГОСТ Р)\s\d+)-(\d+)", "\\1‑\\2", s)
    s = re.sub(r"(?<![A-Za-zА-Яа-яЁё])([IVX]{1,4})[ \t]+(?=кв\.|квартал|полугод)", rf"\1{NB}", s)
    return s


SKIP = {"code", "pre", "svg", "style", "script", "title"}


def typo_html(fragment: str) -> str:
    parts = re.split(r"(<[^>]+>)", fragment)
    depth: list[str] = []
    for i, part in enumerate(parts):
        if part.startswith("<"):
            m = re.match(r"</?\s*([a-zA-Z0-9]+)", part)
            if m:
                tag_name = m.group(1).lower()
                if tag_name in SKIP and not part.endswith("/>"):
                    if part.startswith("</"):
                        if depth and depth[-1] == tag_name:
                            depth.pop()
                    else:
                        depth.append(tag_name)
            continue
        if not depth and part.strip():
            parts[i] = typo(part)
    return "".join(parts)


# ---------------------------------------------------------------- сборка HTML


def pandoc(md: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["pandoc", "-f", "markdown-smart-citations", "-t", "html5", "--wrap=none"],  # noqa: S607
        input=md,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def toc(body: str) -> str:
    items = re.findall(r'<h1 id="([^"]+)">(.*?)</h1>', body)
    lis = "".join(
        f'<li><a href="#{hid}"><span class="n">{k}</span>{text}</a></li>'
        for k, (hid, text) in enumerate(items, start=1)
    )
    return f'<nav class="toc"><h2>Содержание</h2><ul>{lis}</ul></nav>'


def cover(doc: tuple, ctx: dict) -> str:
    _, _, _, title, subtitle, num = doc
    svg = DECK / "assets" / "svg"
    kicker = "Пакет аналитики · читать первым" if num == "00" else f"Пакет аналитики · документ {num}"
    return f"""<section class="cover">
<img class="bg" src="{(svg / "background-dark.svg").as_uri()}" alt="">
<img class="graph" src="{(svg / "authority-graph.svg").as_uri()}" alt="">
<div class="brand"><img src="{(svg / "mandat-mark-on-dark.svg").as_uri()}" alt="Знак «Мандата»">Мандат</div>
<div class="kicker">{kicker}</div>
<div class="title">{html.escape(title)}</div>
<div class="subtitle">{html.escape(subtitle)}</div>
<div class="meta">
<div><b>Версия {ctx["version"]}</b>{ctx["as_of"]}<br>к этапу «Спринт 1»</div>
<div><b>Трек ВТБ «Финтех без границ»</b>«Академия Финтеха 2026»,<br>задача «Онбординг и проверка полномочий»</div>
<div><b>{AUTHOR}</b>Коньков Дмитрий<br>Конькова Елена<br>github.com/KonkovDV/Mandat</div>
</div>
</section>"""


def page_css(doc: tuple, ctx: dict) -> str:
    footer = f"Мандат · {doc[3]} · версия {ctx['version']} от {ctx['as_of']}"
    font = "font-family: 'Golos Text', Arial, sans-serif; font-size: 7.4pt; color: #5E6D8C;"
    faces = "".join(
        f'@font-face {{ font-family: "Golos Text"; src: url("{(DECK / "fonts" / f"GolosText-{name}.ttf").as_uri()}"); '
        f"font-weight: {weight}; font-style: normal; }}\n"
        for name, weight in (("Regular", 400), ("Medium", 500), ("SemiBold", 600), ("Bold", 700))
    )
    return (
        f'{faces}@page {{ @bottom-left {{ content: "{footer}"; {font} }} '
        f'@bottom-right {{ content: counter(page) " из " counter(pages); {font} }} }}'
    )


def render(doc: tuple, ctx: dict, env: jinja2.Environment) -> tuple[str, str]:
    figs = Figures(ctx)
    env.globals.update(fig=figs.fig, raw=figs.html, kpis=figs.kpis)
    md = env.get_template(f"{doc[0]}.md").render(**ctx)
    body = pandoc(md)
    for name, (title, sub, src, svg) in figs.items.items():
        caption = (
            f'<figcaption><span class="t">{html.escape(title)}</span>'
            f'<span class="d">{html.escape(sub)}</span></figcaption>'
        )
        source = f'<div class="src">{html.escape(src)}</div>' if src else ""
        body = body.replace(f"<p>@@FIG:{name}@@</p>", f"<figure>{caption}{svg}{source}</figure>")
    for k, fragment in enumerate(figs.raw):
        body = body.replace(f"<p>@@RAW:{k}@@</p>", fragment)
    # Короткие латинские якоря: в PDF из них получаются имена назначений, кириллица в них длинная.
    for k, hid in enumerate(re.findall(r'<h[1-3] id="([^"]+)"', body), start=1):
        body = body.replace(f'id="{hid}"', f'id="s{k}"').replace(f'href="#{hid}"', f'href="#s{k}"')
    body = body.replace("<p>@@TOC@@</p>", toc(body))
    left = re.findall(r"@@[A-Z]+[^@<]*@@", re.sub(r"<[^>]+>", "", body))
    if left:
        raise SystemExit(f"{doc[0]}: не заменены метки {left}")
    # Короткие таблицы (до 14 строк) не разрываются между страницами, длинные переносятся с повтором шапки.
    body = re.sub(
        r"<table>(.*?)</table>",
        lambda m: ('<table class="keep">' if m.group(1).count("<tr") <= 14 else "<table>") + m.group(1) + "</table>",
        body,
        flags=re.S,
    )
    body = typo_html(body)
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Мандат — {html.escape(doc[3])}</title>
<meta name="author" content="{AUTHOR}">
<meta name="description" content="{html.escape(doc[4])}">
<link rel="stylesheet" href="{(HERE / "style.css").as_uri()}">
<style>{page_css(doc, ctx)}</style>
</head><body>{cover(doc, ctx)}<main>{body}</main></body></html>"""
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", body, flags=re.S))
    return page, html.unescape(text)


# ---------------------------------------------------------------- проверки текста


def check_text(name: str, text: str, ev: dict) -> list[str]:
    """Те же проверки, что lint_claims.py делает для .md и колоды, но для готового текста документа."""
    t = re.sub(r"\s+", " ", text.replace(NB, " ").replace("‑", "-"))
    banned = [
        line.strip()
        for line in (ROOT / "BANNED_CLAIMS.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    hits = [f"{name}: запрещённая формулировка «{p}»" for p in banned if p.casefold() in t.casefold()]
    expected = {"автотест": ev["tests"], "демо-кейс": ev["demo"], "мутаци": ev["fuzz"]}
    for key, pattern in lint_claims.EVIDENCE_COUNTS.items():
        for m in pattern.finditer(t):
            if int(re.sub(r"\D", "", m.group(1))) != expected[key]:
                hits.append(f"{name}: «{m.group(0)}», а в источнике {expected[key]}")
    for m in lint_claims.RULE_COUNT.finditer(t):
        if int(m.group(1) or m.group(2)) != ev["rules"]:
            hits.append(f"{name}: «{m.group(0)}», а правил {ev['rules']}")
    hits += [f"{name}: {h}" for h in lint_claims.form_mismatches(t)]
    return hits


# ---------------------------------------------------------------- PDF


def finish_pdf(path: Path, title: str, subject: str) -> None:
    reader = PdfReader(path)
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata(
        {
            "/Title": f"Мандат — {title}",
            "/Author": AUTHOR,
            "/Subject": subject,
            "/Keywords": "Мандат; МЧД; полномочия; ВТБ; Академия Финтеха 2026",
            "/Creator": "Мандат",
            "/Producer": "Мандат",
        }
    )
    root = writer._root_object  # noqa: SLF001
    if "/Metadata" in root:
        del root["/Metadata"]
    from pypdf.generic import NameObject, TextStringObject

    root[NameObject("/Lang")] = TextStringObject("ru-RU")
    with path.open("wb") as fh:
        writer.write(fh)


def merge(parts: list[tuple[Path, str]], out: Path) -> None:
    writer = PdfWriter()
    for path, title in parts:
        writer.append(str(path), outline_item=title, import_outline=True)
    writer.add_metadata(
        {
            "/Title": "Мандат — пакет аналитики",
            "/Author": AUTHOR,
            "/Subject": "Описание проекта, финансовая модель, оценка рынка, план внедрения и развития",
            "/Creator": "Мандат",
            "/Producer": "Мандат",
        }
    )
    from pypdf.generic import NameObject, TextStringObject

    writer._root_object[NameObject("/Lang")] = TextStringObject("ru-RU")  # noqa: SLF001
    with out.open("wb") as fh:
        writer.write(fh)


def pdf_fonts(path: Path) -> set[str]:
    fonts = set()
    for page in PdfReader(path).pages:
        res = page.get("/Resources")
        if res and "/Font" in res:
            for f in res["/Font"].values():
                fonts.add(str(f.get_object().get("/BaseFont", "")).split("+")[-1])
    return fonts


# ---------------------------------------------------------------- сборка


# Банк России, операции без согласия у юрлиц (M-07, M-15); те же числа — в колоде (build_deck.js, CBR).
CBR = {
    "q1_ops": 818,
    "q1_loss": 573.0,
    "q2_ops": 780,
    "q2_accounts": 707,
    "q2_sbp": 73,
    "q2_loss": 831.4,
    "q2_refund_accounts": 0.195,
    "q2_refund_sbp": 0.005,
    "q2_all_ops": 458655,
    "q2_all_loss": 7350.7,
    "share_q1": 0.078,
    "share_q2": 0.113,
}


def cbr(inp: dict) -> SimpleNamespace:
    """Производные числа обзоров Банка России; база эффекта в модели — среднее за квартал I полугодия."""
    c = dict(CBR)
    c["h1_ops"] = c["q1_ops"] + c["q2_ops"]
    c["h1_loss"] = c["q1_loss"] + c["q2_loss"]
    c["q2_avg"] = c["q2_loss"] / c["q2_ops"]
    c["h1_avg"] = c["h1_loss"] / c["h1_ops"]
    c["q2_ratio"] = round(c["q2_avg"] * 1000 / (c["q2_all_loss"] * 1000 / c["q2_all_ops"]))
    c["growth"] = (c["q2_loss"] - c["q1_loss"]) / c["q1_loss"]
    if c["h1_ops"] / 2 != inp["incidents_q"]["values"][0] or abs(c["h1_loss"] / 2 - inp["loss_q"]["values"][0]) > 0.05:
        raise SystemExit("effect_scenarios.json расходится с данными Банка России за I полугодие 2026")
    return SimpleNamespace(**c)


def context() -> dict:
    m = model()
    ev = evidence()
    S = m["scenarios"]
    sc = [{"s": m["calc"][s]["series"], "c": m["calc"][s]["const"]} for s in range(3)]
    tornado = sorted(m["tornado"], key=lambda r: -abs((r["high"] if r["high"] is not None else r["base"]) - r["low"]))
    inp = {k: SimpleNamespace(**v) for k, v in m["inputs"].items()}
    return {
        "ev": ev,
        "cbr": cbr(m["inputs"]),
        "S": S,
        "sc": sc,
        "cons": sc[0],
        "base": sc[1],
        "opt": sc[2],
        "bank": m["bank"],
        "mk": m["market"],
        "inp": inp,
        "plan": m["plan"],
        "years": m["years"],
        "tornado": tornado,
        "sources": m["sources"],
        "checks": m["checks"],
        "c14": case14(),
        "version": m["version"],
        "as_of": m["as_of"],
        "risks": RISKS,
        "formulas": formulas_count(),
        "sheets": len(load_workbook(OUT / "Mandat_financial_model.xlsx").sheetnames),
        "deck_slides": deck_slides(),
        "pages": {},
        "repo": REPO,
        "n": n,
        "pct": pct,
        "pl": pl,
        "cp": cp,
        "months": months,
        "yr": yr,
        "bln": bln,
        "tag": tag,
        "ru_date": ru_date,
    }


def build(package: Path | None, only: list[str] | None = None) -> int:
    for tool in ("pandoc", "node"):
        if shutil.which(tool) is None:
            raise SystemExit(f"нужен {tool}")
    ctx = context()
    env = jinja2.Environment(  # noqa: S701 — шаблоны свои, HTML собирается намеренно
        loader=jinja2.FileSystemLoader(HERE / "templates"),
        undefined=jinja2.StrictUndefined,
        block_start_string="[%",
        block_end_string="%]",
        variable_start_string="[[",
        variable_end_string="]]",
        comment_start_string="[#",
        comment_end_string="#]",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    problems: list[str] = []
    rendered: dict[str, Path] = {}
    # Обзор собирается последним: в нём число страниц остальных документов.
    for doc in DOCS:
        if only and doc[0] not in only:
            continue
        ctx["pages"].setdefault(doc[0], 3)
        for _ in range(3):
            # Обзор называет и своё число страниц: пересобираем, пока число не сойдётся.
            page, text = render(doc, ctx, env)
            html_path = BUILD / f"{doc[1]}.html"
            html_path.write_text(page, encoding="utf-8")
            pdf_path = OUT / f"{doc[1]}.pdf"
            subprocess.run(  # noqa: S603
                ["node", str(HERE / "render_pdf.js"), str(html_path), str(pdf_path)],  # noqa: S607
                check=True,
                capture_output=True,
            )
            finish_pdf(pdf_path, doc[3], doc[4])
            pages = len(PdfReader(pdf_path).pages)
            stable = ctx["pages"][doc[0]] == pages
            ctx["pages"][doc[0]] = pages
            if stable or doc[0] != "00_overview":
                break
        problems += check_text(doc[0], text, ctx["ev"])
        rendered[doc[0]] = pdf_path
        fonts = pdf_fonts(pdf_path)
        if not all(f.startswith(("GolosText", "DejaVuSansMono")) for f in fonts):
            problems.append(f"{doc[0]}: в PDF не только Golos Text: {sorted(fonts)}")
    if problems:
        sys.stderr.write("проблемы в тексте документов:\n" + "\n".join(problems) + "\n")
        return 1
    if only:
        for k, path in rendered.items():
            print(f"{path.relative_to(ROOT)}: {ctx['pages'][k]} стр.")
        return 0
    order = ["00_overview", "01_project", "02_model", "03_market", "04_plan"]
    titles = {d[0]: f"{d[5]}. {d[3]}" for d in DOCS}
    merge([(rendered[k], titles[k]) for k in order], OUT / "Mandat_analytics_all.pdf")
    for k in order:
        print(f"{rendered[k].relative_to(ROOT)}: {ctx['pages'][k]} стр.")
    if package:
        make_package(package, rendered)
    return 0


def make_package(target: Path, rendered: dict[str, Path]) -> None:
    folder = target / f"Мандат_пакет_аналитики_{model()['as_of']}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    names = {d[0]: d[2] for d in DOCS}
    for key, path in rendered.items():
        shutil.copyfile(path, folder / f"{names[key]}.pdf")
    shutil.copyfile(OUT / "Mandat_financial_model.xlsx", folder / "02_Финансовая_модель.xlsx")
    slides = ROOT / "submission" / "slides"
    shutil.copyfile(slides / "Mandat_VTB.pdf", folder / "05_Презентация_Мандат_ВТБ.pdf")
    shutil.copyfile(slides / "Mandat_VTB.pptx", folder / "05_Презентация_Мандат_ВТБ.pptx")
    shutil.copyfile(OUT / "Mandat_analytics_all.pdf", folder / "Весь_пакет_одним_файлом.pdf")
    # Видео-демо собирает submission/video/build_video.py; в git его нет, поэтому оно добавляется, если собрано.
    video = ROOT / "submission" / "video" / "out" / "Mandat_demo.mp4"
    if video.exists():
        shutil.copyfile(video, folder / "06_Видео-демо_Мандат.mp4")
    else:
        print("видео-демо не собрано: node submission/video/build_video.js — пакет без 06_Видео-демо_Мандат.mp4")
    archive = target / f"{folder.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.iterdir()):
            z.write(f, f"{folder.name}/{f.name}")
    print(f"папка для диска: {folder}\nархив: {archive}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--package", type=Path, help="собрать папку для облачного диска в этом каталоге")
    parser.add_argument("--only", nargs="*", help="собрать только эти документы (для правки шаблона)")
    args = parser.parse_args()
    return build(args.package, args.only)


if __name__ == "__main__":
    raise SystemExit(main())
