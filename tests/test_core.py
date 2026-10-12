"""Ядро: идентификаторы, разбор МЧД, свёртка вердикта, трассировка правил, отчёт."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from conftest import run

from mandat.data import load_json
from mandat.domain.identifiers import (
    inn_valid,
    kpp_valid,
    make_inn10,
    make_inn12,
    make_ogrn,
    make_snils,
    ogrn_valid,
    snils_valid,
)
from mandat.domain.model import EvidenceRef, Finding, Organization, Person
from mandat.domain.verdict import LegalEffect, Verdict, fold, get_profile
from mandat.intake.emchd import EmchdError, build_emchd_xml, parse_emchd
from mandat.intake.xml_safety import XmlRejected, safe_fromstring
from mandat.report.html import render
from mandat.rules.checks import RULES
from mandat.rules.pack import RulePackError, default_pack, validate_pack


def test_identifier_checksums():
    inn = make_inn10("771234567")
    assert inn_valid(inn) and not inn_valid(inn[:-1] + str((int(inn[-1]) + 1) % 10))
    person_inn = make_inn12("5001007322")
    assert inn_valid(person_inn) and not inn_valid(person_inn[:-1] + str((int(person_inn[-1]) + 1) % 10))
    ogrn = make_ogrn("125770012345")
    assert ogrn_valid(ogrn) and not ogrn_valid(ogrn[:-1] + str((int(ogrn[-1]) + 1) % 10))
    assert not inn_valid("12345") and not ogrn_valid(None)
    assert snils_valid("112-233-445 95") and not snils_valid("112-233-445 96")
    assert snils_valid(make_snils("123456789"))
    assert kpp_valid("770101001") and not kpp_valid("77010100")


def test_emchd_roundtrip_keeps_fields_and_xpath_refs():
    org = Organization(inn=make_inn10("770123456"), ogrn=make_ogrn("125770012345"), kpp="770101001", name="ООО «А»")
    signer = Person("Иванов Иван Иванович", make_inn12("7712345678"), position="Генеральный директор")
    rep = Person("Петрова Анна Сергеевна", make_inn12("7787654321"))
    xml = build_emchd_xml(
        number="n-1",
        issue_date=date(2026, 3, 1),
        valid_until=date(2027, 3, 1),
        principal=org,
        signer=signer,
        representatives=[rep],
        machine_codes=[("C1", "M1", "Платёжки")],
        text_authorities=["Представлять интересы в банке"],
        subdelegation_code="3",
    )
    poa = parse_emchd(xml, doc_id="poa")
    assert poa.number == "n-1" and poa.valid_until == date(2027, 3, 1)
    assert poa.principal.inn == org.inn and poa.signer.full_name == signer.full_name
    assert poa.representatives[0].inn == rep.inn
    assert {a.kind for a in poa.authorities} == {"machine", "text"}
    assert poa.subdelegation.value == "chain"
    assert poa.ref("valid_until").locator.endswith("СвДов/@СрокДейст")
    assert len(poa.sha256) == 64


def test_emchd_rejects_wrong_root_and_missing_fields():
    with pytest.raises(EmchdError):
        parse_emchd("<Файл/>", doc_id="x")
    with pytest.raises(EmchdError):
        parse_emchd("<Доверенность/>", doc_id="x")


def test_xml_safety_rejects_entities_and_depth():
    with pytest.raises(XmlRejected):
        safe_fromstring('<!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>')
    deep = "<a>" * 60 + "</a>" * 60
    with pytest.raises(XmlRejected):
        safe_fromstring(deep)


def test_mapping_marks_unverified_fields():
    spec = load_json("emchd_003_mapping.json")
    assert spec["fields"]["number"]["verified"] is True
    assert spec["lists"]["representatives"]["verified"] is False
    assert spec["subdelegation_values_verified"] is True
    assert spec["fields"]["parent_number"]["verified"] is False
    assert spec["irrevocable_subdelegation_allowed_verified"] is False


def _f(check: str, effect: LegalEffect, status: str = "draft", origin: str = "deterministic") -> Finding:
    return Finding("R-X", check, effect, "m", "n", (EvidenceRef("d", "0" * 64, "x"),), status, origin=origin)


def test_fold_silence_is_never_success():
    result = fold([], get_profile("demo"), {"format", "identity", "eio", "coverage"})
    assert result.verdict is Verdict.INSUFFICIENT_DATA


def test_fold_precedence_and_profiles():
    demo, strict = get_profile("demo"), get_profile("strict")
    base = [_f(c, LegalEffect.SUPPORTS) for c in ("format", "identity", "eio", "coverage")]
    applicable = {"format", "identity", "eio", "coverage"}
    assert fold(base, demo, applicable).verdict is Verdict.CONFIRMED
    assert fold([*base, _f("text", LegalEffect.NEEDS_REVIEW)], demo, applicable).verdict is Verdict.NEEDS_REVIEW
    assert fold([*base, _f("eio", LegalEffect.REFUTES)], demo, applicable).verdict is Verdict.REFUTED
    assert fold(base, strict, applicable).verdict is Verdict.INSUFFICIENT_DATA
    approved = [_f(c, LegalEffect.SUPPORTS, "approved") for c in applicable]
    assert fold(approved, strict, applicable).verdict is Verdict.CONFIRMED
    assert fold([*base, _f("eio", LegalEffect.REFUTES)], strict, applicable).verdict is Verdict.INSUFFICIENT_DATA
    assert fold([*base, _f("eio", LegalEffect.REFUTES, "approved")], strict, applicable).verdict is Verdict.REFUTED
    assert (
        fold([*base, _f("coverage", LegalEffect.NOT_VERIFIED)], demo, applicable).verdict is Verdict.INSUFFICIENT_DATA
    )


def test_advisory_finding_cannot_change_verdict():
    applicable = {"format", "identity", "eio", "coverage"}
    base = [_f(c, LegalEffect.SUPPORTS) for c in applicable]
    advisory = _f("eio", LegalEffect.REFUTES, origin="advisory")
    assert fold([*base, advisory], get_profile("demo"), applicable).verdict is Verdict.CONFIRMED


def test_finding_requires_evidence():
    with pytest.raises(ValueError):
        Finding("R-X", "format", LegalEffect.INFO, "m", "n", ())


def test_rule_pack_and_implementations_match():
    pack = default_pack()
    assert set(pack.rules) == set(RULES)
    assert len(pack.sha256) == 64


def test_every_rule_traces_to_an_existing_test(root: Path):
    tests_text = "\n".join(p.read_text(encoding="utf-8") for p in (root / "tests").glob("test_*.py"))
    defined = set(re.findall(r"^def (test_\w+)", tests_text, flags=re.M))
    for spec in default_pack().rules.values():
        assert spec.tests, f"{spec.id}: нет ссылок на тесты"
        missing = [t for t in spec.tests if t not in defined]
        assert not missing, f"{spec.id}: тесты не найдены: {missing}"


def test_rule_pack_validation_rejects_bad_rules():
    bad = {
        "schema": 1,
        "rules": [{"id": "R-1", "check": "nope", "status": "draft", "effects": ["info"], "norm_ref": "x"}],
    }
    with pytest.raises(RulePackError):
        validate_pack(bad)
    no_norm = {
        "schema": 1,
        "rules": [{"id": "R-1", "check": "format", "status": "draft", "effects": ["info"], "norm_ref": " "}],
    }
    with pytest.raises(RulePackError):
        validate_pack(no_norm)


def test_verdict_oracle_agrees_with_fold():
    from mandat.domain.verdict_oracle import oracle_verdict

    ref = EvidenceRef("d", "a" * 64, "x")
    profiles = [get_profile("demo"), get_profile("strict")]
    applicable = {"format", "identity", "registry", "term", "eio", "coverage", "subdelegation", "signature"}
    for profile in profiles:
        for effect in LegalEffect:
            for check in ("format", "signature", "charter"):
                findings = [Finding("R-X", check, effect, "сообщение", "норма", (ref,), rule_status="draft")]
                assert oracle_verdict(findings, profile, applicable) == fold(findings, profile, applicable).verdict
    supported = [
        Finding("R-A", check, LegalEffect.SUPPORTS, "сообщение", "норма", (ref,))
        for check in ("format", "identity", "registry", "term", "eio", "coverage", "subdelegation")
    ]
    supported.append(Finding("R-H", "signature", LegalEffect.NOT_VERIFIED, "сообщение", "норма", (ref,)))
    for profile in profiles:
        assert oracle_verdict(supported, profile, applicable) == fold(supported, profile, applicable).verdict
    for defect in ("ok", "revoked", "registry_unavailable", "charter_limit", "joint_powers"):
        report = run(defect)
        live = {name for name, status in report.checks.items() if status != "not_applicable"}
        assert oracle_verdict(report.findings, get_profile("demo"), live) == report.verdict


def test_confirmed_report_names_the_strongest_objection():
    report = run("ok").as_dict()
    assert report["objection"].startswith("R-SIG-001")
    assert "Сильнейшее возражение" in render(report)


def test_report_is_reproducible_and_fully_sourced():
    a, b = run("ok").as_dict(), run("ok").as_dict()
    assert a["reproducibility_hash"] == b["reproducibility_hash"]
    assert all(f["refs"] for f in a["findings"])
    assert a["proposed_right"]["mode"] == "shadow_recommendation"


def test_html_report_escapes_document_content():
    def mutate(sc):
        sc.manifest["requester"]["full_name"] = "<script>alert(1)</script>"

    html = render(run("ok", mutate=mutate).as_dict())
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_confirmed_case_has_no_next_steps_and_refuted_has_steps():
    def reviewed(sc):
        sc.manifest["charter_disposition"] = "no_applicable_restriction"

    assert run("ok", mutate=reviewed).next_steps == ()
    steps = run("revoked").next_steps
    assert steps and steps[0]["because"]
