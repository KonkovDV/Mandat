"""R5: публичная JSON Schema манифеста кейса и её согласованность с загрузчиком."""

from __future__ import annotations

import copy
import json

import jsonschema
import pytest
from conftest import ROOT

from mandat.intake.case_loader import CaseError, load_case_dir, load_case_inline
from mandat.services.verify import verify_case

SCHEMA = json.loads((ROOT / "docs" / "api" / "case-manifest.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())
CASES = sorted((ROOT / "examples" / "cases").glob("*/case.json"))


def test_schema_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.parent.name)
def test_every_example_case_matches_schema(path):
    errors = [e.message for e in VALIDATOR.iter_errors(json.loads(path.read_text(encoding="utf-8")))]
    assert not errors, errors


def _inline(name="01_ok"):
    base = ROOT / "examples" / "cases" / name
    manifest = json.loads((base / "case.json").read_text(encoding="utf-8"))
    for doc in manifest["documents"]:
        doc["content"] = (base / doc.pop("path")).read_text(encoding="utf-8")
    return manifest


def test_inline_api_manifest_matches_schema_and_loads():
    manifest = _inline()
    assert not list(VALIDATOR.iter_errors(manifest))
    load_case_inline(manifest)


def _set(path, value):
    def change(m):
        target = m
        for key in path[:-1]:
            target = target[key]
        if value is _DELETE:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return m

    return change


_DELETE = object()

# Что загрузчик отвергает (CaseError), то отвергает и схема: интегратор узнаёт об ошибке до запроса.
INVALID = {
    "operation missing": _set(("operation",), _DELETE),
    "operation.date missing": _set(("operation", "date"), _DELETE),
    "operation.date not ISO": _set(("operation", "date"), "20.10.2026"),
    "amount_rub fractional": _set(("operation", "amount_rub"), 10.5),
    "amount_rub boolean": _set(("operation", "amount_rub"), True),
    "amount_rub zero": _set(("operation", "amount_rub"), 0),
    "inn as number": _set(("organization", "inn"), 7729985006),
    "requester.snils as number": _set(("requester", "snils"), 82977196170),
    "documents not list": _set(("documents",), {"id": "x"}),
    "document without id": lambda m: (m["documents"][0].pop("id"), m)[1],
    "document blank id": _set(("documents", 0, "id"), "  "),
    "duplicate document id": lambda m: (m["documents"].append(dict(m["documents"][0])), m)[1],
    "registry not object": _set(("registry",), ["N-1"]),
    "registry extra field": _set(("registry", "N-1"), {"status": "active", "source": "x"}),
    "registry bad event_date": _set(("registry", "N-1"), {"status": "active", "event_date": "01.03.2026"}),
    "registry observed_at without tz": _set(
        ("registry", "N-1"), {"status": "active", "observed_at": "2026-10-01T10:00:00"}
    ),
    "registry principal_inn short": _set(("registry", "N-1"), {"status": "active", "principal_inn": "123"}),
    "charter_disposition unknown": _set(("charter_disposition",), "maybe"),
    "subdelegation form unknown": _set(("subdelegation_forms",), {"N-1": "oral"}),
}

# Дубли id схема JSON выразить не может (uniqueItems сравнивает объекты целиком): их ловит только загрузчик.
SCHEMA_CANNOT = {"duplicate document id"}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_loader_and_schema_reject_the_same_defects(name):
    manifest = INVALID[name](copy.deepcopy(_inline()))
    with pytest.raises(CaseError):
        load_case_inline(manifest)
    if name not in SCHEMA_CANNOT:
        assert list(VALIDATOR.iter_errors(manifest)), f"схема пропустила: {name}"


def test_unknown_top_level_key_is_a_schema_error_but_not_a_loader_error():
    """Опечатка «registy» в схеме видна сразу; загрузчик её игнорирует, и реестр считается недоступным."""
    manifest = _inline()
    manifest["registy"] = manifest.pop("registry")
    assert list(VALIDATOR.iter_errors(manifest))
    case = load_case_inline(manifest)
    assert case.registry == {}


CONFIRMED = "14_old_director_charter_cleared"  # единственный «подтверждено» в демо


def _bound(representative_inn):
    case = load_case_dir(ROOT / "examples" / "cases" / CONFIRMED)
    number = next(iter(case.registry))
    manifest = _inline(CONFIRMED)
    manifest["registry"] = {
        number: {"status": "active", "principal_inn": case.organization.inn, "representative_inn": representative_inn}
    }
    assert not list(VALIDATOR.iter_errors(manifest))
    return load_case_inline(manifest)


def test_registry_inn_binding_from_manifest_reaches_the_verdict():
    """МЧД в реестре зарегистрирована на заявителя — «подтверждено»; на другого — реестр её не находит."""
    requester_inn = load_case_dir(ROOT / "examples" / "cases" / CONFIRMED).requester.inn
    assert verify_case(_bound(requester_inn)).as_dict()["verdict"] == "confirmed"
    other = verify_case(_bound("500100732259")).as_dict()
    assert other["verdict"] != "confirmed"
