"""Несколько цепочек, подпись и полномочия ЕИО, передоверие, снимки реестров."""

from __future__ import annotations

import copy
import random
import re
from datetime import timedelta

import pytest

from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case

W = synth.World.make(1)
SECOND = "dddddddd-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)


def base(kind: str = "ok_charter_cleared") -> dict:
    m = copy.deepcopy(synth.build(kind, 1).inline_manifest())
    m["charter_disposition"] = "no_applicable_restriction"
    return m


def doc(m: dict, doc_id: str) -> dict:
    return next(d for d in m["documents"] if d["id"] == doc_id)


def report(m: dict, profile: str = "demo"):
    return verify_case(load_case_inline(m), profile=profile)


def verdict(m: dict, profile: str = "demo") -> str:
    return report(m, profile).verdict.value


# --------------------------------------------------------------------------- несколько доверенностей


def _second_poa(m: dict, *, issued: str, status: str, pay: bool = True) -> None:
    xml = doc(m, "poa-root")["content"]
    xml = re.sub(r'НомДовер="[^"]*"', f'НомДовер="{SECOND}"', xml)
    xml = re.sub(r'ДатаВыдДовер="[^"]*"', f'ДатаВыдДовер="{issued}"', xml)
    if not pay:
        xml = re.sub(r'<МашПолн КодПолн="SYN-BANK-PAY-SIGN"[^>]*/>', "", xml)
    m["documents"].append({"id": "poa-second", "kind": "emchd_xml", "content": xml})
    m["registry"][SECOND] = status


@pytest.mark.parametrize(
    ("issued", "status", "pay"),
    [
        ("2026-05-01", "active", False),  # новая не покрывает, старая покрывает
        ("2026-05-01", "revoked", True),  # новая отозвана, старая действует
        ("2026-05-01", "active", True),  # обе действуют и покрывают
        ("2026-01-01", "revoked", True),  # отозвана старая
    ],
)
def test_several_poas_are_not_decided_by_newest_leaf(issued, status, pay):
    m = base()
    _second_poa(m, issued=issued, status=status, pay=pay)
    results = set()
    for seed in range(4):
        shuffled = copy.deepcopy(m)
        random.Random(seed).shuffle(shuffled["documents"])
        r = report(shuffled)
        results.add(r.verdict.value)
        assert any("несколько разных МЧД" in w for w in r.warnings)
    assert results == {"insufficient_data"} or results == {"needs_review"}


def test_identical_copy_is_not_a_second_poa():
    m = base()
    m["documents"].append({**copy.deepcopy(doc(m, "poa-root")), "id": "poa-copy"})
    assert verdict(m) == "confirmed"


# --------------------------------------------------------------------------- ЕИО без доверенности


def eio_base() -> dict:
    m = base("ok_director")
    record = doc(m, "egrul")["content"]["executives"][0]
    record["snils"] = m["requester"]["snils"]
    return m


def test_eio_direct_with_corroborated_snils_confirms_in_demo():
    r = report(eio_base())
    assert r.verdict.value == "confirmed"
    assert r.checks["signature"] == "waived"


def test_eio_direct_needs_instruction_signature_in_strict():
    r = report(eio_base(), "strict")
    assert r.checks["signature"] == "not_verified"
    assert any(f.rule_id == "R-SIG-002" for f in r.findings)
    assert r.verdict.value != "confirmed"


def test_eio_direct_uncorroborated_snils_goes_to_review():
    m = base("ok_director")
    m["requester"]["snils"] = W.stranger.snils
    assert verdict(m) == "needs_review"


def test_eio_direct_snils_conflict_with_egrul_is_not_confirmed():
    m = eio_base()
    m["requester"]["snils"] = W.stranger.snils
    assert verdict(m) != "confirmed"


def test_several_current_executives_go_to_review():
    m = eio_base()
    doc(m, "egrul")["content"]["executives"].append(
        {
            "full_name": W.stranger.full_name,
            "inn": W.stranger.inn,
            "position": "Генеральный директор",
            "grn": "2247700999999",
            "record_date": "2024-05-01",
            "valid_from": "2024-05-01",
            "valid_to": None,
        }
    )
    r = report(m)
    assert r.verdict.value == "needs_review"
    assert any("совместных" in f.message for f in r.findings)


def test_paper_path_requires_bankruptcy_check():
    from mandat.domain.graph import build_chain

    case = load_case_inline(copy.deepcopy(synth.build("paper_poa", 1).inline_manifest()))
    chain = build_chain(case)
    assert chain.kind == "paper"
    assert "bankruptcy" in chain.applicable_checks(case)


# --------------------------------------------------------------------------- форма передоверия


def _child_numbers(m: dict) -> list[str]:
    case = load_case_inline(m)
    return [p.number for p in case.poas if p.parent_number]


def _sub010(r) -> list:
    return [f for f in r.findings if f.rule_id == "R-SUB-010"]


def test_subdelegation_without_form_evidence_goes_to_review():
    m = base("ok_subdelegation")
    m.pop("subdelegation_forms", None)
    r = report(m)
    assert {f.effect.value for f in _sub010(r)} == {"needs_review"}
    assert any("п. 3 ст. 187" in f.message for f in _sub010(r))
    assert r.verdict.value != "confirmed"


def test_notarized_mark_is_accepted_only_in_demo(monkeypatch):
    m = base("ok_subdelegation")
    m["subdelegation_forms"] = {n: "notarized" for n in _child_numbers(m)}
    assert {f.effect.value for f in _sub010(report(m))} == {"info"}
    assert {f.effect.value for f in _sub010(report(m, "strict"))} == {"needs_review"}
    monkeypatch.setenv("MANDAT_ENV", "prod")
    assert {f.effect.value for f in _sub010(report(m, "strict"))} == {"needs_review"}


@pytest.mark.parametrize("form", ["legal_entity", "branch_head"])
def test_subdelegator_kind_is_not_taken_from_the_client(form):
    m = base("ok_subdelegation")
    m["subdelegation_forms"] = {n: form for n in _child_numbers(m)}
    found = _sub010(report(m))
    assert {f.effect.value for f in found} == {"needs_review"}
    assert all("по МЧД передоверитель — физическое лицо" in f.message for f in found)


@pytest.mark.parametrize(("tag", "words"), [("СведОрг", "юридическим лицом"), ("СведФилиал", "филиалом")])
def test_subdelegator_kind_comes_from_the_parent_poa(tag, words):
    m = base("ok_subdelegation")
    m.pop("subdelegation_forms", None)
    root = doc(m, "poa-root")
    root["content"] = root["content"].replace("<СведФизЛ", f"<{tag}").replace("</СведФизЛ>", f"</{tag}>")
    found = _sub010(report(m, "strict"))
    assert {f.effect.value for f in found} == {"info"}
    assert all(words in f.message and "по родительской МЧД" in f.message for f in found)


NOTARY = (
    '<СвНотУд МестоДовер="г. Москва" УплНотДейст="1000"><СвНотДейств Должность="1" РегНомНот="77/000-н/77"/>'
    "<ПодпРукопис>Образцов</ПодпРукопис></СвНотУд>"
)


@pytest.mark.parametrize("profile", ["demo", "strict"])
def test_notarization_is_read_from_the_child_poa(profile):
    m = base("ok_subdelegation")
    m.pop("subdelegation_forms", None)
    child = doc(m, "poa-child")
    assert "</Довер>" in child["content"]
    child["content"] = child["content"].replace("</Довер>", NOTARY + "</Довер>")
    found = _sub010(report(m, profile))
    assert {f.effect.value for f in found} == {"info"}
    assert all("удостоверена нотариально" in f.message for f in found)


@pytest.mark.parametrize("profile", ["demo", "strict"])
@pytest.mark.parametrize(
    "element",
    [
        "<СвНотУд/>",
        NOTARY.replace(' РегНомНот="77/000-н/77"', ""),
        NOTARY.replace(' МестоДовер="г. Москва"', ""),
        NOTARY.replace(' УплНотДейст="1000"', ""),
    ],
)
def test_notarization_without_details_is_not_accepted(profile, element):
    m = base("ok_subdelegation")
    m.pop("subdelegation_forms", None)
    child = doc(m, "poa-child")
    child["content"] = child["content"].replace("</Довер>", element + "</Довер>")
    r = report(m, profile)
    assert {f.effect.value for f in _sub010(r)} == {"needs_review"}
    assert any("notarization.details" in f.message for f in r.findings if f.rule_id == "R-FMT-001")
    assert r.verdict.value != "confirmed"


def test_subdelegated_poa_without_signer_position_is_an_open_question():
    m = base("ok_subdelegation")
    child = doc(m, "poa-child")
    child["content"] = re.sub(r' Должность="[^"]*"', "", child["content"], count=1)
    r = report(m)
    fmt = [f for f in r.findings if f.rule_id == "R-FMT-001" and f.effect.value == "needs_review"]
    assert fmt and all("открытый вопрос к юристу" in f.message for f in fmt)
    assert r.verdict.value != "confirmed"


def test_subdelegation_form_mark_for_other_number_does_not_count():
    m = base("ok_subdelegation")
    m["subdelegation_forms"] = {SECOND: "notarized"}
    assert {f.effect.value for f in _sub010(report(m))} == {"needs_review"}


@pytest.mark.parametrize("bad", [{"x": "signed"}, ["notarized"], {"x": None}])
def test_subdelegation_form_mark_is_validated(bad):
    from mandat.intake.case_loader import CaseError

    m = base("ok_subdelegation")
    m["subdelegation_forms"] = bad
    with pytest.raises(CaseError):
        load_case_inline(m)


# --------------------------------------------------------------------------- снимок реестра МЧД

OP_DAY = synth.OP_DATE


def _root_number(m: dict) -> str:
    return next(p.number for p in load_case_inline(m).poas if not p.parent_number)


def _reg(r) -> list:
    return [f for f in r.findings if f.rule_id == "R-REG-001"]


def _observe(m: dict, number: str, **entry) -> None:
    m["registry"] = {k: v for k, v in m["registry"].items() if k != number}
    m["registry"][number] = entry


def test_registry_plain_status_still_works():
    m = base()
    assert verdict(m) == "confirmed"


def test_registry_revoked_before_operation_refutes():
    m = base()
    n = _root_number(m)
    _observe(m, n, status="revoked", event_date=(OP_DAY - timedelta(days=1)).isoformat())
    assert verdict(m) == "refuted"


def test_registry_revoked_after_historical_operation_goes_to_review():
    m = base()
    n = _root_number(m)
    _observe(m, n, status="revoked", event_date=(OP_DAY + timedelta(days=3)).isoformat())
    r = report(m)
    assert {f.effect.value for f in _reg(r)} == {"needs_review"}
    assert r.verdict.value == "needs_review"


def test_registry_revoked_without_event_date_still_refutes():
    m = base()
    _observe(m, _root_number(m), status="revoked")
    assert verdict(m) == "refuted"


def test_registry_event_after_observation_is_not_trusted():
    m = base()
    _observe(
        m,
        _root_number(m),
        status="revoked",
        event_date=(OP_DAY + timedelta(days=30)).isoformat(),
        observed_at=f"{OP_DAY.isoformat()}T10:00:00+03:00",
    )
    assert {f.effect.value for f in _reg(report(m))} == {"not_verified"}


def test_registry_active_snapshot_older_than_operation_is_stale():
    m = base()
    _observe(
        m, _root_number(m), status="active", observed_at=f"{(OP_DAY - timedelta(days=5)).isoformat()}T10:00:00+03:00"
    )
    r = report(m)
    assert {f.effect.value for f in _reg(r)} == {"not_verified"}
    assert r.verdict.value != "confirmed"


def _strict_registry(m: dict) -> set[str]:
    r = verify_case(load_case_inline(m), profile="strict", trusted_sources=frozenset({"registry"}))
    return {f.effect.value for f in _reg(r)}


@pytest.mark.parametrize("entry", [{"status": "active"}, "active"])
def test_registry_snapshot_without_query_time_is_not_verified_in_strict(entry):
    m = base()
    n = _root_number(m)
    m["registry"][n] = entry
    assert _strict_registry(m) == {"not_verified"}
    assert {f.effect.value for f in _reg(report(m))} == {"supports"}


def test_registry_snapshot_with_query_time_is_accepted_in_strict():
    m = base()
    for n in list(m["registry"]):
        _observe(m, n, status="active", observed_at=f"{OP_DAY.isoformat()}T09:00:00+03:00")
    assert _strict_registry(m) == {"supports"}


def test_registry_fresh_snapshot_confirms():
    m = base()
    _observe(m, _root_number(m), status="active", observed_at=f"{OP_DAY.isoformat()}T09:00:00+03:00")
    assert verdict(m) == "confirmed"


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "revoked", "event_date": "вчера"},
        {"status": "active", "observed_at": "2026-10-20"},
        {"event_date": "2026-10-01"},
        {"status": "active", "extra": 1},
    ],
)
def test_registry_entry_is_validated(bad):
    from mandat.intake.case_loader import CaseError

    m = base()
    _observe(m, _root_number(m), **bad)
    with pytest.raises(CaseError):
        load_case_inline(m)


def test_registry_is_asked_once_per_number():
    from mandat.ports import FixtureRegistry, NotConnectedSignature, Ports, RegistryStatus

    class Flaky(FixtureRegistry):
        calls = 0

        def status(self, number):  # первая выдача «действует», потом «отменена»
            Flaky.calls += 1
            return RegistryStatus.ACTIVE if Flaky.calls == 1 else RegistryStatus.REVOKED

        observe = None  # только старый интерфейс

    case = load_case_inline(base("ok_subdelegation"))
    r = verify_case(case, profile="demo", ports=Ports(registry=Flaky({}), signature=NotConnectedSignature()))
    # Один ответ на номер на всю проверку: правила реестра, каскада и длины цепочки читают один снимок.
    assert Flaky.calls == len({p.number for p in case.poas})
    assert r.verdict.value != "confirmed"


def _chain_with_forms() -> dict:
    m = base("ok_subdelegation")
    m["subdelegation_forms"] = {n: "notarized" for n in _child_numbers(m)}
    assert verdict(m) == "confirmed"
    return m


def test_parent_revoked_after_operation_is_review_not_cascade():
    m = _chain_with_forms()
    _observe(m, _root_number(m), status="revoked", event_date=(OP_DAY + timedelta(days=2)).isoformat())
    r = report(m)
    assert not [f for f in r.findings if f.rule_id == "R-SUB-006"]
    assert r.verdict.value == "needs_review"


def test_parent_revoked_before_child_issue_refutes():
    m = _chain_with_forms()
    case = load_case_inline(m)
    child = next(p for p in case.poas if p.parent_number)
    _observe(m, _root_number(m), status="revoked", event_date=(child.issue_date - timedelta(days=1)).isoformat())
    r = report(m)
    assert r.verdict.value == "refuted"
    assert any(f.rule_id == "R-SUB-006" and f.effect.value == "refutes" for f in r.findings)
