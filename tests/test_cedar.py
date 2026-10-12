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
            },
        }
        allowed = cedarpy.is_authorized(request, policies, []).allowed
        confirmed = verify_scenario(chain_scenario(parent, child, flag, longer)).verdict.value == "confirmed"
        if allowed != confirmed:
            disagreements.append((sorted(parent), sorted(child), flag, longer, allowed, confirmed))
    assert not disagreements, disagreements[:5]
