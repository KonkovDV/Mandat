"""Дифференциальная проверка: решения Cedar по экспортированным политикам и вердикты «Мандата».

Нужен extra ``cedar`` (pip install -e ".[cedar]"); без него тест пропускается.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import chain_matrix, chain_scenario, verify_scenario

from mandat.eval.synth import OP_DATE
from mandat.rules.cedar_export import day_number, export_policies, export_schema

cedarpy = pytest.importorskip("cedarpy")


def _link(codes: frozenset[str], until: date, issue: date, flag: int) -> dict:
    return {
        "codes": sorted(codes),
        "registryActive": True,
        "validFrom": day_number(issue),
        "validUntil": day_number(until),
        "subdelegation": flag,
    }


def test_exported_policies_validate_against_schema():
    policies, schema = export_policies(on=OP_DATE), export_schema()
    assert cedarpy.validate_policies(policies, schema).validation_passed
    broken = policies.replace("context.leaf.codes.containsAny", "context.leaf.kodes.containsAny", 1)
    assert not cedarpy.validate_policies(broken, schema).validation_passed


def test_cedar_and_engine_agree_on_every_chain():
    policies = export_policies(on=OP_DATE)
    root_issue, root_until = date(2026, 3, 1), date(2027, 2, 28)
    disagreements = []
    for parent, child, flag, longer in chain_matrix():
        child_until = root_until + timedelta(days=30) if longer else root_until - timedelta(days=30)
        request = {
            "principal": 'Mandat::Representative::"accountant"',
            "action": 'Mandat::Action::"PAYMENT_SIGN"',
            "resource": 'Mandat::Organization::"client"',
            "context": {
                "day": day_number(OP_DATE),
                "hasParent": True,
                "leaf": _link(child, child_until, date(2026, 4, 1), 1),
                "parent": _link(parent, root_until, root_issue, int(flag)),
                "signerIsParentRepresentative": True,
                "joint": False,
            },
        }
        allowed = cedarpy.is_authorized(request, policies, []).allowed
        confirmed = verify_scenario(chain_scenario(parent, child, flag, longer)).verdict.value == "confirmed"
        if allowed != confirmed:
            disagreements.append((sorted(parent), sorted(child), flag, longer, allowed, confirmed))
    assert not disagreements, disagreements[:5]


def _codes(poa) -> frozenset[str]:
    return frozenset(a.code for a in poa.authorities if a.kind == "machine" and a.code)


def _link_scenario(*, bad_signer: bool, early: bool, joint: bool):
    from mandat.data import load_json
    from mandat.eval.synth import build
    from mandat.intake.emchd import build_emchd_xml, parse_emchd

    entries = load_json("authority_classifier.json")["entries"]
    names = {e["code"]: (e["code"], e["mnemonic"], e["name"]) for e in entries}
    sc = build("ok_subdelegation", seed=17)
    sc.manifest["charter_disposition"] = "no_applicable_restriction"
    root = parse_emchd(sc.files["poa-root.xml"], doc_id="poa-root")
    leaf = parse_emchd(sc.files["poa-child.xml"], doc_id="poa-child")
    issue = root.issue_date - timedelta(days=10) if early else leaf.issue_date
    sc.files["poa-child.xml"] = build_emchd_xml(
        number=leaf.number,
        issue_date=issue,
        valid_until=leaf.valid_until,
        principal=leaf.principal,
        signer=leaf.representatives[0] if bad_signer else leaf.signer,
        representatives=list(leaf.representatives),
        machine_codes=[names[c] for c in sorted(_codes(leaf))],
        subdelegation_code="1",
        parent_number=root.number,
        original_number=root.number,
        loses_authority_code="1",
        joint_code="2" if joint else "1",
    ).encode()
    return sc, root, leaf, issue


@pytest.mark.parametrize("bad_signer", [False, True])
@pytest.mark.parametrize("early", [False, True])
@pytest.mark.parametrize("joint", [False, True])
def test_cedar_and_engine_agree_on_link_rules(bad_signer, early, joint):
    """Подписант звена, порядок дат и совместные полномочия: Cedar и «Мандат» решают одинаково."""
    sc, root, leaf, issue = _link_scenario(bad_signer=bad_signer, early=early, joint=joint)
    request = {
        "principal": 'Mandat::Representative::"accountant"',
        "action": 'Mandat::Action::"PAYMENT_SIGN"',
        "resource": 'Mandat::Organization::"client"',
        "context": {
            "day": day_number(OP_DATE),
            "hasParent": True,
            "leaf": _link(_codes(leaf), leaf.valid_until, issue, 1),
            "parent": _link(_codes(root), root.valid_until, root.issue_date, 2),
            "signerIsParentRepresentative": not bad_signer,
            "joint": joint,
        },
    }
    allowed = cedarpy.is_authorized(request, export_policies(on=OP_DATE), []).allowed
    confirmed = verify_scenario(sc).verdict.value == "confirmed"
    assert allowed == confirmed, (bad_signer, early, joint, allowed, confirmed)
    assert allowed == (not bad_signer and not early and not joint)
