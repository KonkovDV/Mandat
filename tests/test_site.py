"""Сайт проекта: страница собирается из evidence.json и пакета правил, без внешних запросов и без ключей стенда."""

from __future__ import annotations

import json
import re
import sys

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import build_site  # noqa: E402

from mandat.rules.pack import default_pack  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "_site"
    build_site.build(out)
    return out


def _page(site) -> str:
    return (site / "index.html").read_text(encoding="utf-8")


def test_page_carries_the_numbers_of_evidence(site):
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    page = _page(site)
    assert "$" not in page
    if evidence["tests"] is not None:  # во время build_evidence итога тестов ещё нет
        assert build_site._num(evidence["tests"]["passed"]) in page
    corpus = evidence["synthetic_corpus"]
    negatives = corpus["cases"] - corpus["confusion"].get("confirmed->confirmed", 0)
    assert f"{corpus['false_confirmed']} из {build_site._num(negatives)}" in page
    assert evidence["generated_at"][:10] in page
    assert '<html lang="ru">' in page and "<title>" in page


def test_every_demo_case_has_a_report_and_no_key_material_is_published(site):
    page = _page(site)
    cases = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))["demo_cases"]["results"]
    for case_id in cases:
        assert f'href="reports/{case_id}.html"' in page
        assert (site / "reports" / f"{case_id}.html").is_file()
    published = [p for p in site.rglob("*") if p.is_file()]
    assert not [p for p in published if p.suffix in {".pem", ".key", ".jsonl"}]
    assert not any(b"PRIVATE KEY" in p.read_bytes() for p in published if p.suffix in {".html", ".json", ".txt"})


def test_page_makes_no_external_requests(site):
    """Ссылки наружу — только переходы по <a>; шрифт, стили и картинки лежат рядом."""
    page = _page(site)
    css = (site / "style.css").read_text(encoding="utf-8")
    assert "<script" not in page
    for tag, attr in (("link", "href"), ("img", "src")):
        for value in re.findall(rf"<{tag}[^>]*\s{attr}=\"([^\"]+)\"", page):
            assert not value.startswith(("http:", "https:", "//")), value
    for value in re.findall(r"url\(\"?([^)\"]+)\"?\)", css):
        assert not value.startswith(("http:", "https:", "//")), value
        assert (site / value).is_file(), value


def test_page_builds_while_evidence_is_being_rebuilt(tmp_path, monkeypatch):
    """build_evidence пишет «tests»: null на время прогона: страница не падает, вместо чисел — прочерк."""
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    evidence["tests"] = None
    values = build_site.values(evidence)
    assert values["tests"] == "—" and values["coverage"] == "—"


def test_law_calendar_on_the_page_is_the_rule_pack_catalogue(site):
    page = _page(site)
    for norm in default_pack().norms:
        assert norm.url in page
        if norm.in_force_until is not None:
            assert f"{norm.in_force_until:%d.%m.%Y}" in page
        for rule in norm.rules:
            assert f'<span class="rule">{rule}</span>' in page, rule  # код правила не рвётся на дефисе
