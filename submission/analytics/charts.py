"""Графики и схемы пакета аналитики: SVG без сторонних библиотек, в палитре колоды и шрифте Golos Text.

Правила (PROMPT.md, раздел 7): тонкие метки со скруглённым концом данных и квадратным основанием, промежуток
2 px между соседними метками, сетка — волосяные линии, легенда при двух рядах и больше, подписи данных
выборочно, текст — цветом текста, а не ряда. Сценарии — порядковая шкала одного тона (светлый — консервативный,
тёмный — оптимистичный), проверена validate_palette.js --ordinal. Ширина подписей считается по метрикам шрифта,
поэтому подписи не наезжают друг на друга. У каждого графика есть <title> и <desc> для экранных чтецов, рядом
в документе — таблица с теми же числами.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

FONTS = Path(__file__).resolve().parents[1] / "deck" / "fonts"
FAMILY = "'Golos Text', Arial, sans-serif"

TEXT, TEXT2, MUTED = "#0B1F44", "#42557A", "#5E6D8C"
GRID, AXIS, SURFACE, SOFT, LINE = "#E3E9F2", "#9AA8C0", "#FFFFFF", "#F3F6FB", "#DCE4F0"
NAVY, COBALT, SKY, LIGHT = "#0A2973", "#004CBC", "#00AAFF", "#6E98DE"
SCENARIO = ("#6E98DE", "#004CBC", "#0A2973")
CONTEXT = "#A9B6CC"
VERDICT = {"confirmed": "#1A9F68", "needs_review": "#F5A300", "refuted": "#E5484D", "insufficient_data": "#6B7A99"}
RISK = {"low": ("#E2F4EB", "#0B6B44"), "mid": ("#FFF1D4", "#7A4E00"), "high": ("#FDE6E7", "#A3161B")}
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

NBSP = " "


# ---------------------------------------------------------------- числа и текст


def ru(x: float | int | None, d: int = 1, sign: bool = False) -> str:
    """Число по-русски: запятая, неразрывные пробелы в группах от 10 000, знак минуса «−»."""
    if x is None:
        return "—"
    v = round(float(x), d)
    neg = v < 0 and abs(v) > 0
    whole, _, frac = f"{abs(v):.{d}f}".partition(".")
    if len(whole) > 4:
        groups = []
        while whole:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        whole = NBSP.join(groups)
    s = whole + ("," + frac if d > 0 else "")
    if neg:
        return "−" + s
    return ("+" + s) if sign and v > 0 else s


@lru_cache(maxsize=4)
def _metrics(weight: str) -> tuple[dict[int, str], dict[str, tuple[int, int]], int] | None:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    font = TTFont(FONTS / f"GolosText-{weight}.ttf")
    return font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm


def tw(text: str, size: float, bold: bool = False) -> float:
    """Ширина строки в пикселях по метрикам Golos Text (без fontTools — оценка 0,56 em на знак)."""
    m = _metrics("SemiBold" if bold else "Regular")
    if m is None:
        return len(text) * size * 0.56
    cmap, hmtx, upm = m
    total = 0
    for ch in text:
        glyph = cmap.get(ord(ch))
        total += hmtx[glyph][0] if glyph else upm * 0.56
    return total * size / upm


def _t(
    x: float,
    y: float,
    s: str,
    size: float = 11,
    color: str = TEXT,
    anchor: str = "start",
    bold: bool = False,
    extra: str = "",
) -> str:
    weight = ' font-weight="600"' if bold else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}"{weight}{extra}>'
        f"{escape(s)}</text>"
    )


def _svg(cid: str, w: float, h: float, title: str, desc: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" role="img" class="chart" '
        f'aria-labelledby="{cid}-t {cid}-d" font-family="{FAMILY}"><title id="{cid}-t">{escape(title)}</title>'
        f'<desc id="{cid}-d">{escape(desc)}</desc>{body}</svg>'
    )


def _bar_v(x: float, w: float, base: float, end: float, color: str, r: float = 4) -> str:
    """Столбец от основания base до конца данных end: скруглён конец данных, основание квадратное."""
    h = abs(base - end)
    if h < 0.5:
        return ""
    r = min(r, w / 2, h)
    if end < base:
        d = (
            f"M{x:.1f},{base:.1f}V{end + r:.1f}Q{x:.1f},{end:.1f} {x + r:.1f},{end:.1f}H{x + w - r:.1f}"
            f"Q{x + w:.1f},{end:.1f} {x + w:.1f},{end + r:.1f}V{base:.1f}Z"
        )
    else:
        d = (
            f"M{x:.1f},{base:.1f}V{end - r:.1f}Q{x:.1f},{end:.1f} {x + r:.1f},{end:.1f}H{x + w - r:.1f}"
            f"Q{x + w:.1f},{end:.1f} {x + w:.1f},{end - r:.1f}V{base:.1f}Z"
        )
    return f'<path d="{d}" fill="{color}"/>'


def _bar_h(base: float, end: float, y: float, h: float, color: str, r: float = 4) -> str:
    """Горизонтальная полоса от base до end: скруглён конец данных."""
    w = abs(end - base)
    if w < 0.5:
        return ""
    r = min(r, h / 2, w)
    if end > base:
        d = (
            f"M{base:.1f},{y:.1f}H{end - r:.1f}Q{end:.1f},{y:.1f} {end:.1f},{y + r:.1f}V{y + h - r:.1f}"
            f"Q{end:.1f},{y + h:.1f} {end - r:.1f},{y + h:.1f}H{base:.1f}Z"
        )
    else:
        d = (
            f"M{base:.1f},{y:.1f}H{end + r:.1f}Q{end:.1f},{y:.1f} {end:.1f},{y + r:.1f}V{y + h - r:.1f}"
            f"Q{end:.1f},{y + h:.1f} {end + r:.1f},{y + h:.1f}H{base:.1f}Z"
        )
    return f'<path d="{d}" fill="{color}"/>'


def ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Круглые деления оси, включая ноль."""
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if (hi - lo) / step <= n:
            break
    start = math.floor(lo / step + 1e-9) * step
    end = math.ceil(hi / step - 1e-9) * step
    count = int(round((end - start) / step))
    return [round(start + i * step, 10) for i in range(count + 1)]


def _legend(items: Sequence[tuple[str, str]], x: float, y: float, size: float = 10.5) -> str:
    out, cx = [], x
    for name, color in items:
        out.append(f'<rect x="{cx:.1f}" y="{y - 8.5:.1f}" width="10" height="10" rx="2.5" fill="{color}"/>')
        out.append(_t(cx + 14, y, name, size, TEXT2))
        cx += 14 + tw(name, size) + 16
    return "".join(out)


# ---------------------------------------------------------------- графики


def grouped_columns(
    cid: str,
    cats: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float]]],
    *,
    title: str,
    desc: str,
    unit: str = "млн ₽",
    label_last: bool = True,
    digits: int = 1,
    w: int = 640,
    h: int = 250,
) -> str:
    """Сгруппированные столбцы: категории по оси X, ряды — цветом; подписаны значения последней группы."""
    left, right, top, bottom = 46, 10, 34, 24
    pw, ph = w - left - right, h - top - bottom
    values = [v for _, _, vals in series for v in vals]
    tk = ticks(min(0, min(values)), max(values))
    lo, hi = tk[0], tk[-1]

    def y(v: float) -> float:
        return top + ph * (hi - v) / (hi - lo)

    out = [_legend([(n, c) for n, c, _ in series], left, 14)]
    for t in tk:
        out.append(
            f'<line x1="{left}" x2="{w - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{GRID if t else AXIS}" '
            f'stroke-width="1"/>'
        )
        out.append(_t(left - 6, y(t) + 3.5, ru(t, 0 if float(t).is_integer() else 1), 9.5, MUTED, "end"))
    out.append(_t(left - 6, top - 10, unit, 9.5, MUTED, "end"))
    n = len(series)
    gw = pw / len(cats)
    bar = min(24.0, (gw * 0.72 - (n - 1) * 2) / n)
    for i, cat in enumerate(cats):
        x0 = left + gw * i + (gw - (n * bar + (n - 1) * 2)) / 2
        for j, (_, color, vals) in enumerate(series):
            x = x0 + j * (bar + 2)
            out.append(_bar_v(x, bar, y(0), y(vals[i]), color))
            if label_last and i == len(cats) - 1:
                ly = y(vals[i]) - 4 if vals[i] >= 0 else y(vals[i]) + 11
                out.append(_t(x + bar / 2, ly, ru(vals[i], digits), 9.5, TEXT, "middle"))
        out.append(_t(left + gw * i + gw / 2, h - 6, cat, 10.5, TEXT2, "middle"))
    return _svg(cid, w, h, title, desc, "".join(out))


def lines(
    cid: str,
    cats: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float]]],
    *,
    title: str,
    desc: str,
    unit: str = "млн ₽",
    digits: int = 1,
    w: int = 640,
    h: int = 250,
) -> str:
    """Линии по годам с нулевой линией; у концов — маркер с кольцом и значение."""
    left, top, bottom = 46, 34, 24
    end_labels = [f"{ru(vals[-1], digits)}" for _, _, vals in series]
    right = 14 + max(tw(s, 10.5, True) for s in end_labels) + 6
    pw, ph = w - left - right, h - top - bottom
    values = [v for _, _, vals in series for v in vals]
    tk = ticks(min(0, min(values)), max(0, max(values)))
    lo, hi = tk[0], tk[-1]

    def y(v: float) -> float:
        return top + ph * (hi - v) / (hi - lo)

    def x(i: int) -> float:
        return left + pw * (i + 0.5) / len(cats)

    out = [_legend([(n, c) for n, c, _ in series], left, 14)]
    for t in tk:
        out.append(
            f'<line x1="{left}" x2="{left + pw}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{AXIS if t == 0 else GRID}" '
            f'stroke-width="1"/>'
        )
        out.append(_t(left - 6, y(t) + 3.5, ru(t, 0 if float(t).is_integer() else 1), 9.5, MUTED, "end"))
    out.append(_t(left - 6, top - 10, unit, 9.5, MUTED, "end"))
    for i, cat in enumerate(cats):
        out.append(_t(x(i), h - 6, cat, 10.5, TEXT2, "middle"))
    ends = []
    for _, color, vals in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
        )
        ends.append((y(vals[-1]), color))
    # Подписи концов раздвигаются по вертикали только при наложении, с выносной линией.
    order = sorted(range(len(series)), key=lambda k: ends[k][0])
    placed: list[float] = []
    for k in order:
        ly = ends[k][0]
        if placed and ly - placed[-1] < 13:
            ly = placed[-1] + 13
        placed.append(ly)
        ex = x(len(cats) - 1)
        if abs(ly - ends[k][0]) > 0.5:
            out.append(
                f'<line x1="{ex + 5:.1f}" y1="{ends[k][0]:.1f}" x2="{ex + 12:.1f}" y2="{ly:.1f}" stroke="{AXIS}" '
                f'stroke-width="1"/>'
            )
        out.append(_t(ex + 14, ly + 4, end_labels[k], 10.5, TEXT, bold=True))
    for _, color, vals in series:
        out.append(
            f'<circle cx="{x(len(cats) - 1):.1f}" cy="{y(vals[-1]):.1f}" r="4" fill="{color}" stroke="{SURFACE}" '
            f'stroke-width="2"/>'
        )
    return _svg(cid, w, h, title, desc, "".join(out))


def hbars(
    cid: str,
    rows: Sequence[tuple[str, float, str]],
    *,
    title: str,
    desc: str,
    unit: str = "",
    digits: int = 1,
    w: int = 640,
    bar: float = 16,
    gap: float = 10,
    value_suffix: str = "",
) -> str:
    """Горизонтальные полосы с подписью слева и значением у конца полосы."""
    label_w = max(tw(r[0], 10.5) for r in rows) + 12
    vals = [r[1] for r in rows]
    texts = [ru(v, digits) + value_suffix for v in vals]
    right = max(tw(s, 10.5, True) for s in texts) + 10
    left = label_w
    pw = w - left - right
    vmax = max(vals) or 1
    h = len(rows) * (bar + gap) + gap + (14 if unit else 0)
    top = gap + (14 if unit else 0)
    out = []
    if unit:
        out.append(_t(left, 10, unit, 9.5, MUTED))
    out.append(
        f'<line x1="{left:.1f}" x2="{left:.1f}" y1="{top - 4:.1f}" y2="{h - gap + 4:.1f}" '
        f'stroke="{AXIS}" stroke-width="1"/>'
    )
    for i, (label, v, color) in enumerate(rows):
        yy = top + i * (bar + gap)
        end = left + pw * max(0.0, v) / vmax
        out.append(_t(left - 8, yy + bar / 2 + 4, label, 10.5, TEXT2, "end"))
        out.append(_bar_h(left, end, yy, bar, color))
        out.append(_t(end + 6, yy + bar / 2 + 4, texts[i], 10.5, TEXT, bold=True))
    return _svg(cid, w, h, title, desc, "".join(out))


def tornado(
    cid: str,
    rows: Sequence[tuple[str, float, float | None]],
    base: float,
    *,
    title: str,
    desc: str,
    step_label: str = "20 %",
    w: int = 640,
) -> str:
    """«Торнадо»: от базового NPV к значениям при −20 % и +20 % каждого допущения; сдвиг — отдельным цветом."""
    bar, gap = 13, 9
    label_w = max(tw(r[0], 10.5) for r in rows) + 14
    vals = [v for _, lo, hi in rows for v in (lo, hi) if v is not None] + [base]
    tk = ticks(min(vals), max(vals), 5)
    lo_v, hi_v = tk[0], tk[-1]
    left, right, top = label_w, 46, 46
    pw = w - left - right
    h = top + len(rows) * (bar + gap) + 26

    def x(v: float) -> float:
        return left + pw * (v - lo_v) / (hi_v - lo_v)

    out = [
        _legend(
            [
                (f"допущение −{step_label}", LIGHT),
                (f"допущение +{step_label}", COBALT),
                ("договор с ВТБ на год позже", CONTEXT),
            ],
            left,
            14,
        )
    ]
    for t in tk:
        out.append(
            f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 6}" y2="{h - 22}" stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(_t(x(t), h - 8, ru(t, 0), 9.5, MUTED, "middle"))
    out.append(
        f'<line x1="{x(base):.1f}" x2="{x(base):.1f}" y1="{top - 12}" y2="{h - 22}" stroke="{TEXT2}" stroke-width="1"/>'
    )
    out.append(_t(x(base), top - 16, f"базовый NPV {ru(base, 1)} млн ₽", 9.5, TEXT2, "middle"))
    for i, (label, low, high) in enumerate(rows):
        yy = top + i * (bar + gap)
        out.append(_t(left - 10, yy + bar / 2 + 4, label, 10.5, TEXT2, "end"))
        pairs = [(low, LIGHT if high is not None else CONTEXT)] + ([(high, COBALT)] if high is not None else [])
        for v, color in pairs:
            out.append(_bar_h(x(base), x(v), yy, bar, color, 3))
            tx = x(v) + (5 if v >= base else -5)
            out.append(_t(tx, yy + bar / 2 + 4, ru(v, 1), 9.5, TEXT, "start" if v >= base else "end"))
    out.append(_t(left + pw, h - 8 - 12, "млн ₽", 9.5, MUTED, "end"))
    return _svg(cid, w, h, title, desc, "".join(out))


def stacked_bar(cid: str, parts: Sequence[tuple[str, float, str, str]], *, title: str, desc: str, w: int = 640) -> str:
    """Одна полоса из частей (доля целого) с промежутками 2 px; подписи внутри, если помещаются, и легенда ниже."""
    total = sum(p[1] for p in parts)
    h, top, bar = 78, 4, 26
    out = []
    x = 0.0
    for i, (_label, v, color, ink) in enumerate(parts):
        width = w * v / total - (2 if i < len(parts) - 1 else 0)
        out.append(
            f'<rect x="{x:.1f}" y="{top}" width="{max(width, 0):.1f}" height="{bar}" fill="{color}" '
            f'rx="{4 if i in (0, len(parts) - 1) else 0}"/>'
        )
        txt = ru(v, 0)
        if tw(txt, 11, True) + 12 < width:
            out.append(_t(x + width / 2, top + bar / 2 + 4, txt, 11, ink, "middle", True))
        x += width + 2
    lx = 0.0
    for label, v, color, _ in parts:
        item = f"{label} — {ru(v, 0)}"
        out.append(f'<rect x="{lx:.1f}" y="{top + bar + 18:.1f}" width="10" height="10" rx="2.5" fill="{color}"/>')
        out.append(_t(lx + 14, top + bar + 27, item, 10.5, TEXT2))
        lx += 14 + tw(item, 10.5) + 18
    return _svg(cid, w, h, title, desc, "".join(out))


def _month_x(d: date, start: date, months: int, left: float, pw: float) -> float:
    k = (d.year - start.year) * 12 + d.month - start.month + (d.day - 1) / 31
    return left + pw * k / months


def gantt(
    cid: str,
    rows: Sequence[tuple[str, date, date, str]],
    marks: Sequence[tuple[date, str]],
    start: date,
    end: date,
    *,
    title: str,
    desc: str,
    w: int = 640,
) -> str:
    """Диаграмма Ганта: шкала по кварталам, полосы работ, нумерованные ромбы вех и легенда вех под диаграммой."""
    bar, gap = 11, 7
    label_w = max(tw(r[0], 10) for r in rows) + 12
    months = (end.year - start.year) * 12 + end.month - start.month
    left, right, top = label_w, 8, 38
    pw = w - left - right
    body_h = len(rows) * (bar + gap)
    colors = {"work": COBALT, "pilot": NAVY, "ext": CONTEXT, "prep": LIGHT}
    roman = {1: "I", 4: "II", 7: "III", 10: "IV"}
    out = []
    for k in range(months + 1):
        d = date(start.year + (start.month - 1 + k) // 12, (start.month - 1 + k) % 12 + 1, 1)
        xx = left + pw * k / months
        quarter = d.month in roman
        out.append(
            f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{top - (14 if quarter else 4)}" y2="{top + body_h}" '
            f'stroke="{AXIS if quarter else GRID}" stroke-width="1"/>'
        )
        if quarter and k + 3 <= months:
            out.append(_t(xx + pw * 1.5 / months, top - 6, f"{roman[d.month]} кв.", 9, MUTED, "middle"))
        if (d.month == 1 or k == 0) and k < months:
            out.append(_t(xx + 2, top - 22, str(d.year), 9.5, TEXT2, bold=True))
    for i, (label, a, b, kind) in enumerate(rows):
        yy = top + i * (bar + gap)
        out.append(_t(left - 8, yy + bar - 1.5, label, 10, TEXT2, "end"))
        x1, x2 = _month_x(a, start, months, left, pw), _month_x(b, start, months, left, pw)
        out.append(_bar_h(x1, max(x2, x1 + 3), yy, bar, colors[kind], 3))
    lane = top + body_h + 12
    out.append(f'<line x1="{left}" x2="{left + pw}" y1="{lane}" y2="{lane}" stroke="{AXIS}" stroke-width="1"/>')
    out.append(_t(left - 8, lane + 4, "вехи", 10, TEXT2, "end"))
    for j, (d, _) in enumerate(marks, start=1):
        xx = _month_x(d, start, months, left, pw)
        out.append(
            f'<path d="M{xx:.1f},{lane - 9:.1f}L{xx + 9:.1f},{lane:.1f}L{xx:.1f},{lane + 9:.1f}'
            f'L{xx - 9:.1f},{lane:.1f}Z" '
            f'fill="{NAVY}" stroke="{SURFACE}" stroke-width="1.5"/>'
        )
        out.append(_t(xx, lane + 3.5, str(j), 9, SURFACE, "middle", True))
    ly = lane + 30
    col_w = (w - 0) / 2
    for j, (_, label) in enumerate(marks):
        cx = (j % 2) * col_w
        cy = ly + (j // 2) * 15
        out.append(
            f'<path d="M{cx + 7:.1f},{cy - 11:.1f}L{cx + 14:.1f},{cy - 4:.1f}L{cx + 7:.1f},{cy + 3:.1f}'
            f'L{cx:.1f},{cy - 4:.1f}Z" '
            f'fill="{NAVY}"/>'
        )
        out.append(_t(cx + 7, cy - 0.5, str(j + 1), 7.5, SURFACE, "middle", True))
        out.append(_t(cx + 20, cy, label, 9.5, TEXT2))
    h = ly + ((len(marks) + 1) // 2) * 15 + 4
    out.append(
        _legend([("работы", COBALT), ("подготовка и решения", LIGHT), ("пилот и эксплуатация", NAVY)], left, h + 12)
    )
    h += 20
    return _svg(cid, w, h, title, desc, "".join(out))


def heatmap(cid: str, risks: Sequence[tuple[str, int, int]], *, title: str, desc: str, w: int = 640) -> str:
    """Тепловая карта рисков 5 × 5: вероятность по X, влияние по Y; цвет клетки — уровень, внутри — номера рисков."""
    cell, left, top = 62, 70, 10
    h = top + 5 * cell + 52
    out = []
    for p in range(1, 6):
        for i in range(1, 6):
            score = p * i
            level = "low" if score <= 4 else ("mid" if score <= 9 else "high")
            fillc, ink = RISK[level]
            x, y = left + (p - 1) * cell, top + (5 - i) * cell
            out.append(f'<rect x="{x + 1}" y="{y + 1}" width="{cell - 2}" height="{cell - 2}" rx="4" fill="{fillc}"/>')
            ids = [r for r, rp, ri in risks if rp == p and ri == i]
            for k, rid in enumerate(ids):
                out.append(_t(x + cell / 2, y + 22 + 14 * k - 7 * (len(ids) - 1), rid, 11, ink, "middle", True))
    for k in range(1, 6):
        out.append(_t(left + (k - 0.5) * cell, top + 5 * cell + 16, str(k), 10, MUTED, "middle"))
        out.append(_t(left - 10, top + (5 - k + 0.5) * cell + 4, str(k), 10, MUTED, "end"))
    out.append(_t(left + 2.5 * cell, top + 5 * cell + 34, "вероятность →", 10.5, TEXT2, "middle"))
    out.append(
        _t(
            18,
            top + 2.5 * cell,
            "влияние →",
            10.5,
            TEXT2,
            "middle",
            extra=f' transform="rotate(-90 18 {top + 2.5 * cell:.0f})"',
        )
    )
    lx = left + 5 * cell + 30
    for k, (level, label) in enumerate([("high", "высокий: 10–25"), ("mid", "средний: 5–9"), ("low", "низкий: 1–4")]):
        fillc, ink = RISK[level]
        out.append(f'<rect x="{lx}" y="{top + 8 + 26 * k}" width="18" height="18" rx="4" fill="{fillc}"/>')
        out.append(_t(lx + 26, top + 21 + 26 * k, label, 10.5, TEXT2))
    out.append(_t(lx, top + 110, "уровень = вероятность × влияние", 9.5, MUTED))
    return _svg(cid, w, h, title, desc, "".join(out))


def intervals(
    cid: str,
    lanes: Sequence[tuple[str, Sequence[tuple[date, date, str, str]]]],
    marker: tuple[date, str],
    start: date,
    end: date,
    *,
    title: str,
    desc: str,
    w: int = 640,
) -> str:
    """Интервалы на оси времени (полномочие — пересечение интервалов на дату) и вертикальная отметка даты операции."""
    bar, gap = 18, 26
    label_w = max(tw(name, 10.5) for name, _ in lanes) + 14
    left, right, top = label_w, 10, 30
    pw = w - left - right
    h = top + len(lanes) * (bar + gap) + 26
    span = (end - start).days

    def x(d: date) -> float:
        return left + pw * (d - start).days / span

    out = []
    for yv in range(start.year, end.year + 1):
        d = date(yv, 1, 1)
        if start <= d <= end:
            out.append(
                f'<line x1="{x(d):.1f}" x2="{x(d):.1f}" y1="{top - 8}" y2="{h - 22}" stroke="{GRID}" stroke-width="1"/>'
            )
            out.append(_t(x(d) + 3, h - 8, str(yv), 9.5, MUTED))
    for i, (name, segs) in enumerate(lanes):
        yy = top + i * (bar + gap)
        out.append(_t(left - 10, yy + bar / 2 + 4, name, 10.5, TEXT2, "end"))
        for a, b, color, label in segs:
            out.append(_bar_h(x(a), x(b), yy, bar, color, 4))
            ink = SURFACE if color in (COBALT, NAVY, "#1A9F68") else TEXT
            if tw(label, 9.5) + 10 < x(b) - x(a):
                out.append(_t(x(a) + 6, yy + bar / 2 + 3.5, label, 9.5, ink))
            else:
                out.append(_t(x(a), yy - 4, label, 9.5, TEXT2))
    md, mlabel = marker
    out.append(
        f'<line x1="{x(md):.1f}" x2="{x(md):.1f}" y1="{top - 14}" y2="{h - 22}" stroke="{TEXT}" stroke-width="1.5"/>'
    )
    anchor = "end" if x(md) > left + pw * 0.6 else "start"
    out.append(_t(x(md) + (-5 if anchor == "end" else 5), top - 16, mlabel, 10, TEXT, anchor, True))
    return _svg(cid, w, h, title, desc, "".join(out))


def calendar(
    cid: str, events: Sequence[tuple[date, str, str]], start: date, end: date, *, title: str, desc: str, w: int = 640
) -> str:
    """Календарь дат на оси: подписи поочерёдно сверху и снизу; при наложении подпись уходит на следующий ярус."""
    left, right = 14, 14
    pw = w - left - right
    span = (end - start).days
    line_h, block = 12, 46

    def x(d: date) -> float:
        return left + pw * (d - start).days / span

    items = []
    for k, (d, label, region) in enumerate(events):
        lines_ = label.split("\n")
        widest = max(tw(s_, 9.5, j == 0) for j, s_ in enumerate(lines_))
        xx = x(d)
        anchor, tx, x0 = "middle", xx, xx - widest / 2
        if xx - widest / 2 < left:
            anchor, tx, x0 = "start", xx - 4, xx - 4
        elif xx + widest / 2 > w - right:
            anchor, tx, x0 = "end", xx + 4, xx + 4 - widest
        items.append(
            {
                "x": xx,
                "tx": tx,
                "anchor": anchor,
                "x0": x0,
                "x1": x0 + widest,
                "lines": lines_,
                "side": "up" if k % 2 == 0 else "down",
                "color": COBALT if region == "ru" else LIGHT,
            }
        )
    placed: dict[str, list[dict]] = {"up": [], "down": []}
    for it in items:
        level = 0
        while any(
            p["level"] == level and not (it["x1"] + 8 < p["x0"] or it["x0"] > p["x1"] + 8) for p in placed[it["side"]]
        ):
            level += 1
        it["level"] = level
        placed[it["side"]].append(it)
    up = 1 + max((it["level"] for it in placed["up"]), default=0)
    down = 1 + max((it["level"] for it in placed["down"]), default=0)
    axis_y = 30 + up * block
    h = axis_y + 22 + down * block
    out = [_legend([("Россия", COBALT), ("ЕС и FATF", LIGHT)], left, 14)]
    out.append(f'<line x1="{left}" x2="{w - right}" y1="{axis_y}" y2="{axis_y}" stroke="{AXIS}" stroke-width="1"/>')
    for yv in range(start.year + 1, end.year + 1):
        d = date(yv, 1, 1)
        out.append(
            f'<line x1="{x(d):.1f}" x2="{x(d):.1f}" y1="{axis_y - 4}" y2="{axis_y + 4}" '
            f'stroke="{AXIS}" stroke-width="1"/>'
        )
        out.append(_t(x(d), axis_y + 16, str(yv), 9.5, MUTED, "middle"))
    for it in items:
        n_lines = len(it["lines"])
        if it["side"] == "up":
            last = axis_y - 16 - it["level"] * block
            first = last - line_h * (n_lines - 1)
            tip = last + 4
        else:
            first = axis_y + 34 + it["level"] * block
            tip = first - 11
        out.append(
            f'<line x1="{it["x"]:.1f}" x2="{it["x"]:.1f}" y1="{axis_y}" y2="{tip:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<circle cx="{it["x"]:.1f}" cy="{axis_y}" r="5" fill="{it["color"]}" stroke="{SURFACE}" stroke-width="2"/>'
        )
        for j, s_ in enumerate(it["lines"]):
            out.append(_t(it["tx"], first + line_h * j, s_, 9.5, TEXT if j == 0 else TEXT2, it["anchor"], j == 0))
    return _svg(cid, w, h, title, desc, "".join(out))


def positioning(
    cid: str,
    players: Sequence[tuple[str, float, float, bool]],
    *,
    x_axis: tuple[str, str],
    y_axis: tuple[str, str],
    title: str,
    desc: str,
    w: int = 640,
    h: int = 330,
) -> str:
    """Качественная карта позиционирования: две оси, игроки точками; «Мандат» выделен, остальные — фоновым цветом."""
    left, right, top, bottom = 24, 14, 12, 40
    pw, ph = w - left - right, h - top - bottom
    out = [
        f'<rect x="{left}" y="{top}" width="{pw}" height="{ph}" fill="{SOFT}" rx="6"/>',
        f'<line x1="{left + pw / 2}" x2="{left + pw / 2}" y1="{top + 8}" y2="{top + ph - 8}" '
        f'stroke="{LINE}" stroke-width="1"/>',
        f'<line x1="{left + 8}" x2="{left + pw - 8}" y1="{top + ph / 2}" y2="{top + ph / 2}" '
        f'stroke="{LINE}" stroke-width="1"/>',
    ]
    out.append(_t(left, h - 22, x_axis[0], 10, MUTED))
    out.append(_t(left + pw, h - 22, x_axis[1] + " →", 10, MUTED, "end"))
    out.append(_t(left + 8, top + ph - 10, y_axis[0], 10, MUTED))
    out.append(_t(left + 8, top + 18, "↑ " + y_axis[1], 10, MUTED))
    for name, px, py, hl in players:
        cx, cy = left + pw * px, top + ph * (1 - py)
        color = COBALT if hl else CONTEXT
        r = 7 if hl else 5
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>')
        anchor = "end" if px > 0.72 else "start"
        out.append(
            _t(cx + (-11 if anchor == "end" else 11), cy + 4, name, 11 if hl else 10, TEXT if hl else TEXT2, anchor, hl)
        )
    return _svg(cid, w, h, title, desc, "".join(out))


def pipeline(cid: str, steps: Sequence[tuple[str, str]], *, title: str, desc: str, w: int = 640, cols: int = 3) -> str:
    """Конвейер шагов сеткой: карточки с номером, заголовком и подписью; стрелки по порядку шагов."""
    n = len(steps)
    arrow, bh, vgap = 18, 74, 26
    rows = (n + cols - 1) // cols
    bw = (w - arrow * (cols - 1)) / cols
    h = rows * bh + (rows - 1) * vgap + 1
    out = []
    for i, (head, sub) in enumerate(steps):
        r, c = divmod(i, cols)
        x, y = c * (bw + arrow), r * (bh + vgap)
        out.append(
            f'<rect x="{x + 0.5:.1f}" y="{y + 0.5:.1f}" width="{bw - 1:.1f}" height="{bh - 1}" rx="8" fill="{SOFT}" '
            f'stroke="{LINE}"/>'
        )
        out.append(_t(x + 12, y + 20, f"{i + 1:02d}", 10, COBALT, bold=True))
        out.append(_t(x + 34, y + 20, head, 11, TEXT, bold=True))
        for j, line in enumerate(_wrap(sub, bw - 24, 9.5)[:3]):
            out.append(_t(x + 12, y + 39 + 12.5 * j, line, 9.5, TEXT2))
        if i < n - 1 and c < cols - 1:
            ax, ay = x + bw + 3, y + bh / 2
            out.append(
                f'<path d="M{ax:.1f},{ay - 5:.1f}L{ax + arrow - 6:.1f},{ay:.1f}L{ax:.1f},{ay + 5:.1f}Z" '
                f'fill="{COBALT}"/>'
            )
        elif i < n - 1:
            x1, y1 = x + bw / 2, y + bh
            x2, y2 = bw / 2, y + bh + vgap
            mid = y1 + vgap / 2
            out.append(
                f'<path d="M{x1:.1f},{y1 + 1:.1f}V{mid:.1f}H{x2:.1f}V{y2 - 7:.1f}" fill="none" stroke="{COBALT}" '
                f'stroke-width="1.5"/>'
            )
            out.append(
                f'<path d="M{x2 - 5:.1f},{y2 - 8:.1f}L{x2:.1f},{y2 - 1:.1f}L{x2 + 5:.1f},{y2 - 8:.1f}Z" '
                f'fill="{COBALT}"/>'
            )
    return _svg(cid, w, h, title, desc, "".join(out))


def _wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    words, lines_, cur = text.split(" "), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if tw(trial, size, bold) <= width or not cur:
            cur = trial
        else:
            lines_.append(cur)
            cur = word
    if cur:
        lines_.append(cur)
    return lines_
