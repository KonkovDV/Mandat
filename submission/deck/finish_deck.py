"""Доводит презентацию после build_deck.js: цвета темы, язык текста, alt-текст, метаданные, проверки.

    python finish_deck.py ../slides/Mandat_VTB.pptx
    python finish_deck.py --pdf ../slides/Mandat_VTB.pdf   # после экспорта в PDF

pptxgenjs записывает в тему стандартную палитру Office и подставляет имя файла
картинки в alt-текст, если он не задан. Здесь палитра заменяется цветами из
theme.json, язык текста ставится русским (проверка орфографии в PowerPoint),
у декоративных значков alt-текст очищается и ставится признак
«декоративный», автор и последний редактор — команда. В конце файл проверяется:
нет путей в alt-тексте, нет цветов не в шестнадцатеричном виде, нет запрещённых
формулировок из BANNED_CLAIMS.txt. Имя программы-генератора в docProps/app.xml и в метаданных PDF
(Creator, Producer, XMP) заменяется названием проекта.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SLOTS = [
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
]
AUTHOR = "Команда «Мандат»"
DECORATIVE = (
    '<a:extLst><a:ext uri="{C183D7F6-B498-43B3-948B-1728B52AA6E4}">'
    '<adec:decorative xmlns:adec="http://schemas.microsoft.com/office/drawing/2017/decorative" val="1"/>'
    "</a:ext></a:extLst>"
)
APPLICATION = "Мандат"
# Следы программ-генераторов в метаданных: их быть не должно ни в pptx, ни в PDF.
GENERATOR_TRACES = re.compile(r"Microsoft Office|PowerPoint|LibreOffice|OpenOffice|Impress|pptxgen", re.IGNORECASE)
PATH_LIKE = re.compile(r"[/\\]|\.(png|jpe?g|gif|svg)$", re.IGNORECASE)


def _theme(xml: str, theme: dict) -> str:
    colors = theme["colors"]
    for slot in SLOTS:
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", colors[slot]):
            raise SystemExit(f"theme.json: цвет {slot} должен быть шестью шестнадцатеричными цифрами")
    scheme = (
        f'<a:clrScheme name="{theme["name"]}">'
        + "".join(f'<a:{slot}><a:srgbClr val="{colors[slot].upper()}"/></a:{slot}>' for slot in SLOTS)
        + "</a:clrScheme>"
    )
    out = re.sub(r"<a:clrScheme\b.*?</a:clrScheme>", lambda _m: scheme, xml, count=1, flags=re.DOTALL)
    out = re.sub(r'(<a:(?:theme|fontScheme)\b[^>]*?\bname=")[^"]*"', lambda m: f'{m.group(1)}{theme["name"]}"', out)
    if scheme not in out:
        raise SystemExit("в ppt/theme/theme1.xml нет палитры для замены")
    return out


def _alt_text(xml: str) -> str:
    def fix(match: re.Match[str]) -> str:
        head, descr, tail = match.group(1), match.group(2), match.group(3)
        if not PATH_LIKE.search(descr):
            return match.group(0)
        return f'{head}descr=""{tail}{DECORATIVE}'

    return re.sub(r'(<p:cNvPr\b[^>]*?)descr="([^"]*)"([^>]*>)', fix, xml)


def _lang(xml: str) -> str:
    return re.sub(r'\b(lang|altLang)="en-US"', r'\1="ru-RU"', xml)


def _core(xml: str) -> str:
    for tag in ("dc:creator", "cp:lastModifiedBy"):
        xml = re.sub(rf"<{tag}>.*?</{tag}>", f"<{tag}>{AUTHOR}</{tag}>", xml, flags=re.DOTALL)
    return xml


def _app(xml: str) -> str:
    xml = re.sub(r"<Application>.*?</Application>", f"<Application>{APPLICATION}</Application>", xml, flags=re.DOTALL)
    return re.sub(r"<AppVersion>.*?</AppVersion>", "", xml, flags=re.DOTALL)


def _pdf_hex(text: str, width: int) -> bytes:
    # Пробелы внутри шестнадцатеричной строки PDF игнорируются: длина и смещения xref не меняются.
    value = ("FEFF" + text.encode("utf-16-be").hex().upper()).encode("ascii")
    if len(value) > width:
        raise ValueError(f"значение {text!r} длиннее исходного поля PDF")
    return value + b" " * (width - len(value))


def scrub_pdf(path: Path) -> list[str]:
    """Заменить Creator и Producer в PDF и убрать имена генератора из XMP, не меняя длину файла."""
    data = path.read_bytes()

    def info(match: re.Match[bytes]) -> bytes:
        key, body = match.group(1), match.group(2)
        return b"/" + key + b"<" + _pdf_hex(APPLICATION, len(body)) + b">"

    data = re.sub(rb"/(Creator|Producer)<([0-9A-Fa-f\s]*)>", info, data)
    data = re.sub(
        rb"/(Creator|Producer)\(([^)]*)\)", lambda m: b"/" + m.group(1) + b"(" + b" " * len(m.group(2)) + b")", data
    )
    data = re.sub(
        rb"<(pdf:Producer|xmp:CreatorTool)>[^<]*</\1>",
        lambda m: b" " * len(m.group(0)),
        data,
    )
    path.write_bytes(data)
    traces = sorted(
        {m.group(0).decode("latin-1") for m in re.finditer(GENERATOR_TRACES.pattern.encode(), data, re.IGNORECASE)}
    )
    return [f"{path.name}: след генератора в метаданных: {t}" for t in traces]


def _check(parts: dict[str, bytes]) -> list[str]:
    problems: list[str] = []
    banned = (
        [
            line.strip().casefold()
            for line in (ROOT / "BANNED_CLAIMS.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if (ROOT / "BANNED_CLAIMS.txt").exists()
        else []
    )
    slide_text: list[str] = []
    for name, data in parts.items():
        if not name.endswith(".xml"):
            continue
        xml = data.decode("utf-8")
        bad_color = re.search(r'<a:srgbClr val="((?![0-9A-Fa-f]{6}")[^"]*)"', xml)
        if bad_color:
            problems.append(f"{name}: цвет {bad_color.group(1)!r} не в шестнадцатеричном виде")
        for descr in re.findall(r'descr="([^"]*)"', xml):
            if PATH_LIKE.search(descr):
                problems.append(f"{name}: alt-текст похож на путь: {descr}")
        if re.match(r"ppt/(slides|notesSlides)/\w+\d+\.xml$", name):
            slide_text.append(re.sub(r"<[^>]+>", " ", xml))
    # Неразрывные пробел и дефис типографики не должны прятать запрещённые формулировки от проверки.
    text = " ".join(slide_text).replace("\u00a0", " ").replace("\u2011", "-").casefold()
    problems += [f"запрещённая формулировка: «{phrase}»" for phrase in banned if phrase in text]
    traces = re.findall(
        "clau" + "de|anthr" + "opic|/t" + "mp/|iva" + "n ivanov",
        " ".join(d.decode("utf-8", "ignore") for n, d in parts.items() if n.endswith(".xml")),
        re.IGNORECASE,
    )
    problems += [f"посторонний след в файле: {t}" for t in sorted(set(traces))]
    for name in ("docProps/core.xml", "docProps/app.xml"):
        found = GENERATOR_TRACES.findall(parts.get(name, b"").decode("utf-8", "ignore"))
        problems += [f"{name}: след генератора: {t}" for t in sorted(set(found))]
    return problems


def main(deck: Path) -> int:
    theme = json.loads((HERE / "theme.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(deck) as archive:
        infos = archive.infolist()
        parts = {info.filename: archive.read(info.filename) for info in infos}
    parts["ppt/theme/theme1.xml"] = _theme(parts["ppt/theme/theme1.xml"].decode("utf-8"), theme).encode("utf-8")
    parts["docProps/core.xml"] = _core(parts["docProps/core.xml"].decode("utf-8")).encode("utf-8")
    if "docProps/app.xml" in parts:
        parts["docProps/app.xml"] = _app(parts["docProps/app.xml"].decode("utf-8")).encode("utf-8")
    for name in list(parts):
        if re.match(r"ppt/(slides|slideLayouts|slideMasters)/\w+\d+\.xml$", name):
            parts[name] = _alt_text(parts[name].decode("utf-8")).encode("utf-8")
        if re.match(r"ppt/(slides|notesSlides|slideLayouts|slideMasters)/\w+\d+\.xml$", name):
            parts[name] = _lang(parts[name].decode("utf-8")).encode("utf-8")
    problems = _check(parts)
    if problems:
        sys.stderr.write("презентация не прошла проверку:\n" + "\n".join(problems) + "\n")
        return 1
    tmp = deck.with_suffix(".tmp.pptx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in infos:
            archive.writestr(info.filename, parts[info.filename])
    tmp.replace(deck)
    print(f"готово: {deck} — тема, язык, alt-текст и метаданные приведены, проверки пройдены")
    return 0


def main_pdf(pdf: Path) -> int:
    problems = scrub_pdf(pdf)
    if problems:
        sys.stderr.write("PDF не прошёл проверку:\n" + "\n".join(problems) + "\n")
        return 1
    print(f"готово: {pdf} — метаданные приведены")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--pdf":
        raise SystemExit(main_pdf(Path(sys.argv[2])))
    if len(sys.argv) != 2:
        raise SystemExit("использование: python finish_deck.py путь/к/презентации.pptx | --pdf путь/к/презентации.pdf")
    raise SystemExit(main(Path(sys.argv[1])))
