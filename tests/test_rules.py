"""По тесту на каждое правило пакета. Имена тестов перечислены в data/rules.json (трассировка)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import rule_effects, run

from mandat.domain.identifiers import make_inn10, make_ogrn
from mandat.domain.model import Organization, Person
from mandat.intake.emchd import build_emchd_xml, parse_emchd


def test_r_fmt_001_bad_xml_is_insufficient():
    report = run("malformed_xml")
    assert report.verdict.value == "insufficient_data"
    assert "not_verified" in rule_effects(report, "R-FMT-001")


def test_r_fmt_001_xml_bomb_rejected():
    report = run("xml_bomb")
    assert report.verdict.value == "insufficient_data"
    messages = [f.message for f in report.findings if f.rule_id == "R-FMT-001"]
    assert any("защитой" in m for m in messages)


def test_r_fmt_002_foreign_format_needs_review():
    def mutate(sc):
        sc.files["poa-root.xml"] = sc.files["poa-root.xml"].replace(b"EMCHD_1", b"5.03")

    report = run("ok", mutate=mutate)
    assert "needs_review" in rule_effects(report, "R-FMT-002")
    assert report.verdict.value == "needs_review"


def test_r_fmt_003_order_1001_requisites():
    report = run("missing_1001")
    assert rule_effects(report, "R-FMT-003") == {"needs_review"}
    assert any("наименование полномочия" in f.message for f in report.findings if f.rule_id == "R-FMT-003")
    assert report.verdict.value == "needs_review"
    assert rule_effects(run("ok"), "R-FMT-003") == {"supports"}
    # МЧД, выданная до 01.02.2026, по новым требованиям не проверяется.
    assert not rule_effects(run("no_term_expired"), "R-FMT-003")


def test_r_fmt_004_irrevocable_mismatch_needs_review():
    def mutate(sc):
        root = parse_emchd(sc.files["poa-root.xml"], doc_id="poa-root")
        sc.files["poa-root.xml"] = build_emchd_xml(
            number=root.number,
            issue_date=root.issue_date,
            valid_until=root.valid_until,
            principal=root.principal,
            signer=root.signer,
            representatives=list(root.representatives),
            machine_codes=[(a.code, a.mnemonic, a.name) for a in root.authorities if a.kind == "machine"],
            subdelegation_code="1",
            irrevocable_subdelegation_code="2",
        ).encode()

    report = run("ok", mutate=mutate)
    assert rule_effects(report, "R-FMT-004") == {"needs_review"}
    assert report.verdict.value == "needs_review"


def test_r_id_004_name_mismatch_needs_review():
    report = run("name_mismatch")
    assert rule_effects(report, "R-ID-004") == {"needs_review"}
    assert report.verdict.value == "needs_review"
    assert rule_effects(run("ok"), "R-ID-004") == {"supports"}


def test_r_id_001_bad_checksum_refutes():
    report = run("bad_checksum")
    assert "refutes" in rule_effects(report, "R-ID-001")
    assert report.verdict.value == "refuted"


def test_r_id_002_org_mismatch_refutes():
    report = run("org_mismatch")
    assert "refutes" in rule_effects(report, "R-ID-002")
    assert report.verdict.value == "refuted"


def test_r_id_003_wrong_representative_refutes():
    report = run("wrong_representative")
    assert "refutes" in rule_effects(report, "R-ID-003")
    assert report.verdict.value == "refuted"


def test_r_reg_001_revoked_refutes():
    report = run("revoked")
    assert "refutes" in rule_effects(report, "R-REG-001")
    assert report.proposed_right is None


def test_r_reg_001_unavailable_is_insufficient():
    report = run("registry_unavailable")
    assert "not_verified" in rule_effects(report, "R-REG-001")
    assert report.verdict.value == "insufficient_data"


def test_r_term_001_no_term_defaults_to_one_year():
    report = run("no_term")
    # Без срока МЧД действует год со дня совершения (п. 1 ст. 186 ГК РФ): не опровержение, а разбор.
    assert rule_effects(report, "R-TERM-001") == {"needs_review"}
    assert rule_effects(report, "R-TERM-002") == {"supports"}
    assert report.verdict.value == "needs_review"
    assert any("01.03.2027" in f.message for f in report.findings if f.rule_id == "R-TERM-001")


def test_r_term_002_expired_refutes():
    report = run("expired")
    assert "refutes" in rule_effects(report, "R-TERM-002")
    strict = run("expired", profile="strict")
    # В strict неутверждённое правило не опровергает, а отправляет на разбор; подпись не проверена.
    assert strict.verdict.value == "insufficient_data"
    assert any("понижено до разбора" in w for w in strict.warnings)


def test_r_term_002_no_term_after_a_year_refutes():
    report = run("no_term_expired")
    assert rule_effects(report, "R-TERM-002") == {"refutes"}
    assert any("год со дня совершения" in f.message for f in report.findings if f.rule_id == "R-TERM-002")
    assert report.verdict.value == "refuted"


def test_r_eio_001_signer_not_eio_refutes():
    report = run("signer_not_eio")
    assert "refutes" in rule_effects(report, "R-EIO-001")


def test_r_eio_002_fresh_change_needs_review():
    report = run("director_changed")
    assert rule_effects(report, "R-EIO-002") == {"needs_review"}
    assert report.verdict.value == "needs_review"
    assert report.routing["priority"] == "P1"
    assert all(f.effect.value != "refutes" for f in report.findings)


def test_r_eio_002_old_change_is_signal_and_confirms():
    """МЧД прежнего руководителя, не отменённая, действует: подтверждено с сигналом (п. 1 ст. 188 ГК РФ)."""
    report = run("director_changed_long_ago")
    assert rule_effects(report, "R-EIO-002") == {"info"}
    assert report.verdict.value == "needs_review"
    assert any("не прекращает" in f.message for f in report.findings if f.rule_id == "R-EIO-002")
    assert report.as_dict()["signals"], "сигнал должен быть виден в отчёте отдельно от находок"


def test_r_eio_003_unreliable_needs_review():
    def mutate(sc):
        egrul = json.loads(sc.files["egrul.json"])
        egrul["executives"][0]["unreliable"] = True
        sc.files["egrul.json"] = json.dumps(egrul, ensure_ascii=False).encode()

    report = run("ok", mutate=mutate)
    assert rule_effects(report, "R-EIO-003") == {"needs_review"}


def test_r_eio_004_liquidated_refutes():
    report = run("liquidated")
    assert "refutes" in rule_effects(report, "R-EIO-004")


def test_r_eio_004_bankruptcy_terminating_refutes():
    def mutate(sc):
        sc.manifest["bankruptcy"] = {sc.manifest["organization"]["inn"]: "bankruptcy_proceedings"}

    report = run("ok", mutate=mutate)
    assert "refutes" in rule_effects(report, "R-EIO-004")
    assert report.verdict.value == "refuted"


def test_r_eio_004_observation_needs_review():
    def mutate(sc):
        sc.manifest["bankruptcy"] = {sc.manifest["organization"]["inn"]: "observation"}

    report = run("ok", mutate=mutate)
    assert rule_effects(report, "R-EIO-004") == {"needs_review"}
    assert report.verdict.value != "confirmed"


@pytest.mark.parametrize(
    ("who", "status", "effect"),
    [
        ("organization", "observation", "needs_review"),
        ("organization", "financial_recovery", "needs_review"),
        ("organization", "external_management", "refutes"),
        ("organization", "bankruptcy_proceedings", "refutes"),
        ("organization", "settlement", "needs_review"),
        ("organization", "property_sale", "needs_review"),
        ("requester", "debt_restructuring", "needs_review"),
        ("requester", "property_sale", "needs_review"),
        ("requester", "bankruptcy_proceedings", "needs_review"),
        ("organization", "none", None),
    ],
)
def test_r_eio_004_bankruptcy_mapping(who, status, effect):
    def mutate(sc):
        sc.manifest["bankruptcy"] = {sc.manifest[who]["inn"]: status}

    effects = rule_effects(run("ok", mutate=mutate), "R-EIO-004")
    assert effects == ({effect} if effect else set())


def test_r_eio_004_unknown_bankruptcy_status_is_silent():
    def mutate(sc):
        sc.manifest["bankruptcy"] = {sc.manifest["organization"]["inn"]: "что-то странное"}

    assert "R-EIO-004" not in {f.rule_id for f in run("ok", mutate=mutate).findings}


def test_r_eio_005_director_acts_without_poa():
    report = run("ok_director")
    assert report.chain.kind == "eio_direct"
    assert rule_effects(report, "R-EIO-005") == {"supports"}
    assert report.verdict.value == "needs_review"


def test_r_cov_001_not_covered_refutes():
    report = run("not_covered")
    assert "refutes" in rule_effects(report, "R-COV-001")


def test_r_cov_001_text_only_needs_review():
    report = run("text_authority")
    assert rule_effects(report, "R-COV-001") == {"needs_review"}
    assert rule_effects(report, "R-TXT-001") == {"needs_review"}
    assert report.verdict.value == "needs_review"


def test_r_cov_002_revoked_code_needs_review():
    report = run("code_revoked")
    assert rule_effects(report, "R-COV-001") == {"supports"}
    assert rule_effects(report, "R-COV-002") == {"needs_review"}
    assert report.verdict.value == "needs_review"
    assert rule_effects(run("ok"), "R-COV-002") == {"supports"}


def test_r_jnt_001_joint_powers_needs_review():
    report = run("joint_powers")
    assert rule_effects(report, "R-JNT-001") == {"needs_review"}
    assert report.verdict.value == "needs_review"
    assert not rule_effects(run("ok"), "R-JNT-001")


def test_r_sub_001_forbidden_refutes():
    report = run("subdelegation_forbidden")
    assert "refutes" in rule_effects(report, "R-SUB-001")


def test_r_sub_002_wider_refutes():
    report = run("child_wider")
    assert "refutes" in rule_effects(report, "R-SUB-002")


def test_r_sub_003_longer_term_refutes():
    report = run("child_longer")
    assert "refutes" in rule_effects(report, "R-SUB-003")


def test_r_sub_004_missing_parent_is_insufficient():
    report = run("missing_parent")
    assert rule_effects(report, "R-SUB-004") == {"not_verified"}
    assert rule_effects(report, "R-EIO-001") == {"not_verified"}
    assert report.verdict.value == "insufficient_data"


def test_r_sub_005_principal_mismatch_refutes():
    other = Organization(
        inn=make_inn10("770123456"), ogrn=make_ogrn("125770012345"), kpp="770101001", name="ООО «Иное»"
    )

    def mutate(sc):
        child = parse_emchd(sc.files["poa-child.xml"], doc_id="poa-child")
        sc.files["poa-child.xml"] = build_emchd_xml(
            number=child.number,
            issue_date=child.issue_date,
            valid_until=child.valid_until,
            principal=other,
            signer=child.signer,
            representatives=list(child.representatives),
            machine_codes=[(a.code, a.mnemonic, a.name) for a in child.authorities if a.kind == "machine"],
            parent_number=child.parent_number,
        ).encode()

    report = run("ok_subdelegation", mutate=mutate)
    assert "refutes" in rule_effects(report, "R-SUB-005")


def test_r_sub_006_parent_revoked_cascades():
    report = run("parent_revoked")
    assert rule_effects(report, "R-SUB-006") == {"refutes"}
    assert any("теряет силу передоверие" in f.message for f in report.findings)
    assert report.verdict.value == "refuted"


def test_r_sub_007_lost_on_subdelegation_refutes():
    report = run("lost_on_subdelegation")
    assert rule_effects(report, "R-SUB-007") == {"refutes"}
    assert report.verdict.value == "refuted"


def test_r_sub_008_long_chain_needs_review():
    report = run("long_chain_unregistered")
    assert len(report.chain.links) == 4
    assert rule_effects(report, "R-SUB-008") == {"needs_review"}
    assert report.verdict.value == "needs_review"


def test_r_sub_009_child_signer_must_match_parent_representative():
    from mandat.domain.identifiers import make_inn12
    from mandat.eval.synth import build
    from mandat.intake.case_loader import load_case_inline
    from mandat.intake.emchd import parse_emchd
    from mandat.services.verify import verify_case

    report = run("ok_subdelegation", seed=2)
    assert report.verdict.value == "needs_review"
    assert rule_effects(report, "R-SUB-009") == {"supports"}
    manifest = build("ok_subdelegation", seed=2).inline_manifest()
    child = next(doc for doc in manifest["documents"] if doc["id"] == "poa-child")
    signer_inn = parse_emchd(child["content"], doc_id="poa-child").signer.inn
    assert signer_inn
    child["content"] = child["content"].replace(signer_inn, make_inn12("7700000001"))
    broken = verify_case(load_case_inline(manifest))
    assert broken.verdict.value == "refuted"
    assert rule_effects(broken, "R-SUB-009") == {"refutes"}


def test_missing_charter_blocks_confirmation():
    from mandat.eval.synth import build
    from mandat.intake.case_loader import load_case_inline
    from mandat.services.verify import verify_case

    manifest = build("ok", seed=4).inline_manifest()
    manifest["documents"] = [doc for doc in manifest["documents"] if doc["kind"] != "charter_text"]
    report = verify_case(load_case_inline(manifest))
    assert report.verdict.value != "confirmed"
    assert rule_effects(report, "R-CHR-002") == {"not_verified"}


def test_large_deal_without_threshold_needs_review():
    from mandat.eval.synth import build
    from mandat.intake.case_loader import load_case_inline
    from mandat.services.verify import verify_case

    manifest = build("ok", seed=5).inline_manifest()
    for doc in manifest["documents"]:
        if doc["kind"] == "charter_text":
            doc["content"] += " Крупные сделки требуют предварительного одобрения общего собрания участников."
    report = verify_case(load_case_inline(manifest))
    assert report.verdict.value == "needs_review"
    assert "needs_review" in rule_effects(report, "R-CHR-001")


def test_r_sub_008_long_chain_in_registry_is_info():
    report = run("long_chain")
    assert rule_effects(report, "R-SUB-008") == {"info"}
    assert report.verdict.value == "needs_review"


def test_r_chr_001_limit_needs_review():
    report = run("charter_limit")
    assert rule_effects(report, "R-CHR-001") == {"needs_review"}
    assert any(s["unit"] == "Юридическая служба" for s in report.next_steps)


def test_r_chr_002_clean_charter_is_info():
    report = run("ok")
    assert rule_effects(report, "R-CHR-002") == {"needs_review"}
    assert report.verdict.value == "needs_review"


def test_r_pap_001_paper_poa_needs_review():
    report = run("paper_poa")
    assert report.chain.kind == "paper"
    assert rule_effects(report, "R-PAP-001") == {"needs_review"}
    assert report.verdict.value == "needs_review"


def test_r_sig_001_strict_profile_is_insufficient():
    demo, strict = run("ok"), run("ok", profile="strict")
    assert demo.verdict.value == "needs_review"
    assert demo.checks["signature"] == "waived"
    assert strict.verdict.value == "insufficient_data"
    assert strict.checks["signature"] == "not_verified"


def test_once_subdelegation_cannot_be_used_twice():
    """Однократное передоверие: третье звено цепочки опровергается."""
    from mandat.domain.identifiers import make_inn12

    org = Organization(inn=make_inn10("770987654"), ogrn=make_ogrn("125770098765"), kpp="770101001", name="ООО «Тест»")
    eio = Person("Иванов Иван Иванович", make_inn12("7799999999"))
    a, b, c = (
        Person(name, make_inn12(f"77{n:08d}")) for name, n in (("Агеев А А", 1), ("Белов Б Б", 2), ("Волков В В", 3))
    )
    pay = [("SYN-BANK-PAY-SIGN", "m", "n")]
    xmls = {
        "p1": build_emchd_xml(
            number="p1",
            issue_date=date(2026, 1, 1),
            valid_until=date(2027, 1, 1),
            principal=org,
            signer=eio,
            representatives=[a],
            machine_codes=pay,
            subdelegation_code="2",
        ),
        "p2": build_emchd_xml(
            number="p2",
            issue_date=date(2026, 2, 1),
            valid_until=date(2026, 12, 1),
            principal=org,
            signer=a,
            representatives=[b],
            machine_codes=pay,
            subdelegation_code="3",
            parent_number="p1",
        ),
        "p3": build_emchd_xml(
            number="p3",
            issue_date=date(2026, 3, 1),
            valid_until=date(2026, 11, 1),
            principal=org,
            signer=b,
            representatives=[c],
            machine_codes=pay,
            subdelegation_code="1",
            parent_number="p2",
        ),
    }
    from mandat.intake.case_loader import load_case_inline
    from mandat.services.verify import verify_case

    manifest = {
        "case_id": "once",
        "organization": {"inn": org.inn, "ogrn": org.ogrn},
        "requester": {"inn": c.inn},
        "operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20"},
        "registry": dict.fromkeys(xmls, "active"),
        "documents": [{"id": k, "kind": "emchd_xml", "content": v} for k, v in xmls.items()]
        + [
            {
                "id": "egrul",
                "kind": "egrul_json",
                "content": {
                    "inn": org.inn,
                    "ogrn": org.ogrn,
                    "status": "active",
                    "executives": [
                        {"full_name": eio.full_name, "inn": eio.inn, "grn": "1", "valid_from": "2020-01-01"}
                    ],
                },
            }
        ],
    }
    report = verify_case(load_case_inline(manifest))
    assert [p.number for p in report.chain.links] == ["p1", "p2", "p3"]
    assert any("Однократное" in f.message and f.effect.value == "refutes" for f in report.findings)
    assert report.verdict.value == "refuted"
