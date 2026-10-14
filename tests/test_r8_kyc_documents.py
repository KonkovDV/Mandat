"""Сведения о документах полномочий представителя (115-ФЗ, ст. 7, п. 1, пп. 1) в отчёте и карточке."""

from __future__ import annotations

from conftest import ROOT, run

from mandat.intake.case_loader import load_case_dir
from mandat.report.html import render
from mandat.services.verify import verify_case


def test_report_lists_every_document_of_the_chain() -> None:
    report = run("ok_subdelegation").as_dict()
    docs = report["chain"]["documents"]
    assert [d["number"] for d in docs] == report["chain"]["poa_numbers"]
    root, child = docs
    assert child["parent_number"] == root["number"]
    for d in docs:
        assert d["issue_date"] and d["effective_until"]
        assert d["principal_inn"] == report["organization"]["inn"]
        assert d["signer"] and d["representatives"]


def test_term_by_law_is_marked_when_poa_has_no_term() -> None:
    report = verify_case(load_case_dir(ROOT / "examples" / "cases" / "12_no_term")).as_dict()
    docs = report["chain"]["documents"]
    assert docs, "в кейсе есть МЧД"
    no_term = [d for d in docs if d["valid_until"] is None]
    assert no_term and all(d["term_by_law"] == (d["effective_until"] is not None) for d in no_term)


def test_card_shows_documents_table_with_escaping() -> None:
    report = run("ok").as_dict()
    report["chain"]["documents"][0]["signer"] = "<script>x</script>"
    html = render(report)
    assert "Документы, подтверждающие полномочия" in html
    assert "115-ФЗ, ст. 7, п. 1, пп. 1" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_card_without_poa_has_no_documents_table() -> None:
    report = run("ok").as_dict()
    report["chain"]["documents"] = []
    assert "Документы, подтверждающие полномочия" not in render(report)
