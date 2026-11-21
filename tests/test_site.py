"""Сайт проекта: девять страниц из evidence.json, модели, пакета правил и прогона ядра.

Без внешних запросов, встроенного кода и ключей стенда."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import build_site  # noqa: E402

from mandat.audit import attestation, merkle, status_list  # noqa: E402
from mandat.data import canonical_json  # noqa: E402
from mandat.rules.pack import default_pack  # noqa: E402

PAGES = [name for name, _ in build_site.PAGES]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "_site"
    build_site.build(out)
    return out


def _page(site, name: str = "index") -> str:
    """Текст страницы; неразрывные пробелы типографики (_typo) читаются как обычные."""
    return (site / f"{name}.html").read_text(encoding="utf-8").replace(" ", " ")


def _data(site, name: str):
    block = re.search(r'<script type="application/json" id="page-data">(.*?)</script>', _page(site, name), re.S)
    assert block is not None, name
    return json.loads(block.group(1))


def test_page_carries_the_numbers_of_evidence(site):
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    page = _page(site)
    if evidence["tests"] is not None:  # во время build_evidence итога тестов ещё нет
        assert build_site._num(evidence["tests"]["passed"]) in page
    corpus = evidence["synthetic_corpus"]
    negatives = corpus["cases"] - corpus["confusion"].get("confirmed->confirmed", 0)
    assert f"{corpus['false_confirmed']} из {build_site._num(negatives)}" in page
    assert evidence["generated_at"][:10] in page
    assert '<html lang="ru">' in page and "<title>" in page


def test_every_page_has_csp_menu_footer_and_no_template_leftovers(site):
    """Каждая страница: политика CSP, меню с текущей страницей, подвал с коммитом сборки, подстановки закрыты."""
    for name in PAGES:
        page = _page(site, name)
        assert "$" not in re.sub(r'<script type="application/json"[^>]*>.*?</script>', "", page, flags=re.S), name
        assert f'<meta http-equiv="Content-Security-Policy" content="{build_site.CSP}">' in page, name
        assert page.count('aria-current="page"') == 1, name
        assert "Сборка" in page and "Код на GitHub" in page, name
        assert 'name="viewport" content="width=device-width' in page, name


def test_every_demo_case_has_a_report_and_no_key_material_is_published(site):
    page = _page(site, "cases")
    cases = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))["demo_cases"]["results"]
    for case_id in cases:
        assert f'href="reports/{case_id}.html"' in page
        assert (site / "reports" / f"{case_id}.html").is_file()
    published = [p for p in site.rglob("*") if p.is_file()]
    assert not [p for p in published if p.suffix in {".pem", ".key", ".jsonl"}]
    texts = [p for p in published if p.suffix in {".html", ".json", ".txt", ".js"}]
    assert not any(b"PRIVATE KEY" in p.read_bytes() for p in texts)


def test_pages_make_no_external_requests_and_run_no_inline_scripts(site):
    """Ссылки наружу — только переходы по <a>; скрипты, стили, шрифт и картинки лежат рядом, встроенного кода нет."""
    css = (site / "style.css").read_text(encoding="utf-8")
    for name in PAGES:
        page = _page(site, name)
        for tag in re.findall(r"<script\b[^>]*>", page):
            if 'type="application/json"' in tag:
                continue
            src = re.search(r'\ssrc="([^"]+)"', tag)
            assert src is not None, f"{name}: встроенный скрипт {tag}"
            assert not src.group(1).startswith(("http:", "https:", "//")), src.group(1)
            assert (site / src.group(1).split("?")[0]).is_file(), src.group(1)
        for tag, attr in (("link", "href"), ("img", "src"), ("video", "src")):
            for element in re.findall(rf"<{tag}\b[^>]*>", page):
                if 'rel="canonical"' in element:
                    continue  # адрес страницы для поисковиков, браузер по нему ничего не грузит
                value = re.search(rf'\s{attr}="([^"]+)"', element)
                if value:
                    assert not value.group(1).startswith(("http:", "https:", "//")), value.group(1)
        assert ' style="' not in page, f"{name}: встроенный стиль запрещён политикой CSP"
    for value in re.findall(r"url\(\"?([^)\"]+)\"?\)", css):
        assert not value.startswith(("http:", "https:", "//")), value
        assert (site / value).is_file(), value
    for script in (site / "js").glob("*.js"):
        text = script.read_text(encoding="utf-8")
        for target in re.findall(r"""\bimport\b[^"']*["']([^"']+)["']""", text):
            assert target.startswith("./"), f"{script.name}: импорт {target}"
        assert "fetch(" not in text and "XMLHttpRequest" not in text, script.name


def test_styles_and_scripts_carry_the_version_of_their_content(site):
    """Как в rightrack: адрес стиля, скрипта и модуля несёт ?v= с хешем файла, старый кэш не подменит новую страницу."""
    for name in [*PAGES, "404"]:
        page = _page(site, name)
        urls = re.findall(r'(?:href|src)="((?:/Mandat/)?(?:style\.css|js/[\w-]+\.js)[^"]*)"', page)
        assert urls, name
        for url in urls:
            path, _, version = url.partition("?v=")
            assert version == build_site._version(site / path.removeprefix("/Mandat/")), url
    for script in (site / "js").glob("*.js"):
        for target, version in re.findall(
            r"""from\s+["']\./([\w-]+\.js)\?v=([0-9a-f]{8})["']""", script.read_text(encoding="utf-8")
        ):
            assert version == build_site._version(site / "js" / target), (script.name, target)


def test_page_builds_while_evidence_is_being_rebuilt(tmp_path, monkeypatch):
    """build_evidence пишет «tests»: null на время прогона: страница не падает, вместо чисел — прочерк."""
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    evidence["tests"] = None
    values = build_site.values(evidence)
    assert values["tests"] == "—" and values["coverage"] == "—"


def test_law_calendar_on_the_page_is_the_rule_pack_catalogue(site):
    page = _page(site, "law")
    data = _data(site, "law")
    assert [n["url"] for n in data["norms"]] == [n.url for n in default_pack().norms]
    for norm in default_pack().norms:
        assert norm.url in page
        if norm.in_force_until is not None:
            assert f"{norm.in_force_until:%d.%m.%Y}" in page
        for rule in norm.rules:
            assert f'<span class="rule">{rule}</span>' in page, rule  # код правила не рвётся на дефисе


def test_page_is_ready_for_phones_and_sharing(site):
    """Телефон, планшет и компьютер: viewport, цвет панели браузера, иконки, manifest и картинка для мессенджеров."""
    page = _page(site)
    assert 'name="viewport" content="width=device-width' in page
    assert page.count('name="theme-color"') == 2
    manifest = json.loads((site / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["lang"] == "ru" and manifest["start_url"] == "./"
    for icon in manifest["icons"]:
        assert (site / icon["src"]).is_file(), icon["src"]
    assert (site / "apple-touch-icon.png").is_file()
    image = re.search(r'property="og:image" content="([^"]+)"', page)
    assert image is not None and image.group(1).startswith(build_site.SITE_URL)
    assert (site / image.group(1).removeprefix(build_site.SITE_URL)).is_file()
    assert (site / "404.html").is_file()


def test_images_are_local_sized_and_described(site):
    """Картинки лежат рядом, у каждой есть alt, у фото и 3D-значков заданы размеры: страница не прыгает при загрузке."""
    for name in [*PAGES, "404"]:
        page = _page(site, name)
        for tag in re.findall(r"<img\b[^>]*>", page):
            assert re.search(r'\salt="[^"]*"', tag), f"{name}: {tag}"
            src = re.search(r'\ssrc="([^"]+)"', tag)
            if src is None:
                continue  # кадр просмотрщика слайдов: адрес ставит скрипт
            srcset = re.search(r'\ssrcset="([^"]+)"', tag)
            for url in [src.group(1), *(re.findall(r"(\S+)\s+\d+w", srcset.group(1)) if srcset else [])]:
                assert (site / url.removeprefix("/Mandat/").split("?")[0]).is_file(), f"{name}: {url}"
            if "img/" in src.group(1):
                assert re.search(r'\swidth="\d+"', tag) and re.search(r'\sheight="\d+"', tag), f"{name}: {tag}"
    assert "img/anna-hero" in _page(site) and "img/anna-office" in _page(site, "pilot")


def test_fonts_are_woff2_and_tables_scroll_inside_their_frame(site):
    """Шрифты — WOFF2 рядом со страницей: Golos Text для текста, Manrope для заголовков и чисел, курсив Cormorant
    Garamond для акцентов; у каждого семейства лицензия OFL рядом. Вместе меньше 110 КБ, браузер берёт только нужные
    подмножества. Таблица на узком экране листается в своей рамке, страница вбок не уезжает."""
    css = (site / "style.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("(assets/fonts/[^"]+)"\)', css)
    assert fonts and all(f.endswith(".woff2") and (site / f).is_file() for f in fonts)
    assert sum((site / f).stat().st_size for f in fonts) < 110_000
    for family in ("Golos Text", "Manrope", "Cormorant Garamond"):
        assert f'font-family: "{family}"' in css, family
    for license_file in ("OFL.txt", "OFL-Manrope.txt", "OFL-CormorantGaramond.txt"):
        assert "SIL Open Font License" in (site / "assets" / "fonts" / license_file).read_text(encoding="utf-8")
    for name in PAGES:
        page = _page(site, name)
        wrapped = len(re.findall(r'<div class="table-wrap[^"]*"[^>]*>\s*<table', page))
        assert page.count("<table") == wrapped, name
    assert "prefers-reduced-motion" in css and "@media print" in css


def test_sprint_2_criteria_and_value_for_the_bank_come_from_the_model(site):
    """Спринт 2 на первом экране после вопроса: шесть критериев на сто баллов; экономика — из модели, как в форме."""
    page = _page(site)
    points = [int(p.split("–")[1]) for p in re.findall(r'<span class="pts">([^<]+)</span>', page)]
    assert points == [18, 22, 22, 16, 12, 10] and sum(points) == 100
    assert page.index('id="sprint"') < page.index('id="pain"') < page.index('id="value"') < page.index('id="pilot"')
    values = build_site.economics()
    unit = build_site.effect_model.unit_economics()[1]
    assert values["unit_manual"] == build_site._num(round(float(unit["manual_check_rub"]) / 10) * 10)
    for key in ("unit_manual", "unit_mandat", "payback", "prevented", "incidents_h1", "loss_h1_bln"):
        assert values[key] in page, key
    form = (ROOT / "submission" / "FORM_SPRINT2.md").read_text(encoding="utf-8")
    for phrase in (
        f"около {values['unit_manual']} ₽",
        f"около {values['unit_mandat']} ₽",
        f"за {values['payback']}",
        f"{values['fte']} {values['fte_word']}",
        f"{values['prevented']} млн ₽",
    ):
        assert phrase in form, phrase


def test_date_scrubber_is_the_engine_on_every_day(site):
    """Шкала даты на главной: отрезки идут подряд без дыр, на дату кейса — вердикт кейса 14 из evidence.json."""
    data = _data(site, "index")
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    segments = data["segments"]
    assert segments[0]["from"] == data["start"] and segments[-1]["to"] == data["end"]
    for left, right in zip(segments, segments[1:]):
        gap = date.fromisoformat(right["from"]) - date.fromisoformat(left["to"])
        assert gap == timedelta(days=1), (left["to"], right["from"])
    at_op = next(s for s in segments if s["from"] <= data["op"] <= s["to"])
    assert at_op["verdict"] == evidence["demo_cases"]["results"][build_site.LAB_CASE]["verdict"]
    assert {s["verdict"] for s in segments} == {"confirmed", "refuted", "needs_review", "insufficient_data"}
    assert any(w["rule"] == "R-EIO-002" for s in segments if s["verdict"] == "needs_review" for w in s["why"])


def test_what_if_answers_are_the_engine(site):
    """«Что если» на странице кейсов: 48 сочетаний; опровержение сильнее нехватки, нехватка сильнее разбора."""
    answers = _data(site, "cases")["whatif"]["answers"]
    assert len(answers) == 48
    assert answers["active-0000"]["verdict"] == "confirmed"
    assert answers["revoked-0000"]["verdict"] == "refuted"
    assert answers["silent-0000"]["verdict"] == "insufficient_data"
    assert answers["active-1000"]["verdict"] == "needs_review"
    assert answers["active-0010"]["verdict"] == "refuted"
    assert answers["active-1001"]["verdict"] == "insufficient_data"
    rank = {"confirmed": 0, "needs_review": 1, "insufficient_data": 2, "refuted": 3}
    for key, answer in answers.items():
        registry, bits = key.split("-")
        for i, bit in enumerate(bits):
            if bit == "0":
                more = answers[f"{registry}-{bits[:i]}1{bits[i + 1 :]}"]
                assert rank[more["verdict"]] >= rank[answer["verdict"]], (key, i)


def test_verify_page_publishes_a_genuine_credential_status_and_journal_proof(site):
    """То, что браузер проверяет на странице «Проверка», проверяется и ядром: подпись, статусы, путь до корня."""
    data = _data(site, "verify")
    key = attestation.public_key_from_multibase(data["public_key"])
    assert attestation.verify(data["attestation"], key)
    index = int(data["attestation"]["credentialStatus"][0]["statusListIndex"])
    for moment, suspended in (("before", False), ("after", True)):
        for purpose in ("revocation", "suspension"):
            credential = data["lists"][moment][purpose]
            assert attestation.verify(credential, key), (moment, purpose)
            bits = status_list.decode_bits(credential["credentialSubject"]["encodedList"])
            assert status_list.get_bit(bits, index) is (suspended and purpose == "suspension"), (moment, purpose)
    j = data["journal"]
    leaf = merkle.leaf_hash(canonical_json(j["record"]).encode("utf-8"))
    assert leaf.hex() == j["leaf"]
    proof = [bytes.fromhex(h) for h in j["proof"]]
    assert merkle.verify_inclusion(leaf, j["seq"] - 1, j["size"], proof, bytes.fromhex(j["root"]))
    tampered = {**j["record"], "details": {**j["record"]["details"], "verdict": "refuted"}}
    other = merkle.leaf_hash(canonical_json(tampered).encode("utf-8"))
    assert not merkle.verify_inclusion(other, j["seq"] - 1, j["size"], proof, bytes.fromhex(j["root"]))


def test_showcase_page_is_the_command_and_its_journal(site):
    data = _data(site, "showcase")
    outcomes = [s["outcome"] for s in data["steps"]]
    assert outcomes == ["ПОДТВЕРЖДЕНО", "ВЫПУЩЕНО", "ДОПУЩЕНО", "ОТКАЗ", "ОТКАЗ", "ПРИЗНАКИ ЗАХВАТА", "ОТКАЗ"]
    assert sum(len(s["records"]) for s in data["steps"]) == data["journal"]["records"]
    assert [r["action"] for r in data["steps"][5]["records"]] == ["status_change"] * len(data["steps"][5]["records"])


def test_calculator_reads_the_model_of_the_analytics_package(site):
    data = _data(site, "economics")
    bank = json.loads((ROOT / "submission" / "analytics" / "out" / "model_values.json").read_text(encoding="utf-8"))[
        "bank"
    ]
    for key in build_site.ECON_INPUTS + build_site.ECON_OUTPUTS:
        source = data["inputs"] if key in build_site.ECON_INPUTS else data["outputs"]
        assert source[key] == bank[key], key
    page = _page(site, "economics")
    for value in bank["fte_round"]:
        assert f'<td class="n">{build_site._num(value)}</td>' in page


def test_criteria_page_is_the_submission_plan(site):
    """Критерии трека на сайте собраны из таблиц submission/README.md и README.md: расходиться им негде."""
    criteria = build_site._criteria()
    assert len(criteria["sprint1"]) == 8 and sum(r["max"] for r in criteria["sprint1"]) == 100
    assert [r["max"] for r in criteria["sprint2"]] == [18, 22, 22, 16, 12, 10]
    assert criteria["final"] and criteria["conditions"]
    page = _page(site, "criteria")
    for row in criteria["sprint1"] + criteria["sprint2"]:
        assert row["name"] in page, row["name"]


def test_pitch_page_shows_every_slide_of_the_deck(site):
    import zipfile

    deck = ROOT / "submission" / "slides" / "Mandat_VTB.pptx"
    with zipfile.ZipFile(deck) as z:
        count = len([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)])
    data = _data(site, "pitch")
    assert len(data["slides"]) == count and 0 < data["pitch"] < count
    for slide in data["slides"]:
        assert (site / "slides" / slide["image"]).is_file(), slide["image"]
    page = _page(site, "pitch")
    assert page.count('<li id="slide-') == count
