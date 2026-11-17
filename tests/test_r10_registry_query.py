"""R4: запрос к реестру МЧД по трём параметрам — номер, ИНН доверителя, ИНН представителя (ФНС с 01.03.2026)."""

from __future__ import annotations

import dataclasses

from conftest import ROOT

from mandat.intake.case_loader import load_case_dir
from mandat.ports import FixtureRegistry, NotConnectedSignature, Ports, RegistryStatus
from mandat.services.verify import default_ports, verify_case


def _case():
    return load_case_dir(ROOT / "examples" / "cases" / "01_ok")


def test_registry_is_asked_with_principal_and_representative_inn():
    case = _case()
    ports = default_ports(case)
    verify_case(case, ports=ports)
    queries = ports.registry.queries
    assert queries, "реестр опрошен"
    number, principal, representative = queries[0]
    assert principal == case.organization.inn == "7729985006"
    assert representative == case.requester.inn == "777588807322"
    assert len({q[0] for q in queries}) == len(queries)  # каждый номер — один запрос


def test_fixture_registry_does_not_find_poa_for_other_representative():
    registry = FixtureRegistry({"N-1": {"status": "active", "principal_inn": "1", "representative_inn": "2"}})
    assert registry.observe("N-1", principal_inn="1", representative_inn="2").status == RegistryStatus.ACTIVE
    assert registry.observe("N-1", principal_inn="1", representative_inn="3").status == RegistryStatus.UNAVAILABLE
    assert registry.observe("N-1").status == RegistryStatus.ACTIVE  # без ИНН — прежний поиск по номеру


def test_legacy_port_without_query_parameters_still_works():
    case = _case()
    base = FixtureRegistry(case.registry)

    class Legacy:
        name, is_stub = "legacy", True

        def observe(self, number):
            return base.observe(number)

    report = verify_case(
        case,
        ports=Ports(registry=Legacy(), signature=NotConnectedSignature(), bankruptcy=default_ports(case).bankruptcy),
    )
    assert report.as_dict()["verdict"] == verify_case(case).as_dict()["verdict"]
    assert dataclasses.is_dataclass(case)
