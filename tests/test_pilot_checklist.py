"""Чек-лист перед первым пилотом держится тестами, а не обещанием (воркшоп 20.11.2026: «правило работает»).

* каждый сетевой модуль из ``HTTP_ALLOWED`` объявлен маршрутом карты с данными и получателем;
* у каждого поля ПДн манифеста названа цель, правила из списка существуют;
* поле без цели в отчёт не попадает — проверено прогоном кейса с особыми значениями;
* тесты, на которые ссылается матрица готовности, существуют; пункты без тестов — с владельцем и условием;
* docs/PILOT_CHECKLIST.md собран из данных и evidence.json, а не написан руками.
"""

from __future__ import annotations

import ast
import importlib.util
import json

from conftest import ROOT
from test_llm_chokepoint import HTTP_ALLOWED

from mandat.data import load_json
from mandat.eval.synth import build
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case

CHECKLIST = load_json("pilot_checklist.json")
SCHEMA = json.loads((ROOT / "docs" / "api" / "case-manifest.schema.json").read_text(encoding="utf-8"))
# Поля манифеста с ПДн людей: заявитель, контрагент, сведения о банкротстве (ИНН людей) и тексты документов.
PERSONAL = {
    "requester.full_name",
    "requester.inn",
    "requester.snils",
    "requester.position",
    "operation.counterparty_inn",
}
DOCUMENT_KINDS = {"emchd_xml", "egrul_json", "charter_text", "paper_poa_text"}


def _generator():
    spec = importlib.util.spec_from_file_location("gen_pilot_checklist", ROOT / "scripts" / "gen_pilot_checklist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_network_module_is_a_declared_route():
    declared = {module for route in CHECKLIST["routes"] for module in route["modules"]}
    network = {path for path in HTTP_ALLOWED if path.startswith("src/")}
    assert network <= declared, f"сетевой модуль без маршрута в карте: {sorted(network - declared)}"
    assert all((ROOT / module).exists() for module in declared), "в карте модуль, которого нет"
    for route in CHECKLIST["routes"]:
        assert all(route[key].strip() for key in ("from", "to", "data", "purpose", "control")), route["id"]


def test_every_personal_field_has_a_purpose():
    schema_fields = {
        f"{group}.{name}"
        for group in ("requester", "operation")
        for name in (SCHEMA["properties"][group].get("properties") or {})
    }
    assert PERSONAL <= schema_fields, "поле ПДн исчезло из схемы манифеста: обновите карту"
    assert "bankruptcy" in SCHEMA["properties"]
    listed = {f["field"] for f in CHECKLIST["fields"]}
    expected = PERSONAL | {"bankruptcy"} | {f"documents: {kind}" for kind in DOCUMENT_KINDS}
    assert expected <= listed, f"поле без цели: {sorted(expected - listed)}"
    rules = {r["id"] for r in load_json("rules.json")["rules"]}
    for field in CHECKLIST["fields"]:
        assert field["purpose"].strip() and set(field["rules"]) <= rules, field["field"]


def test_fields_without_a_purpose_do_not_reach_the_report():
    """Поля с ``in_report: false`` проверяются прогоном: их особые значения не встречаются в отчёте."""
    manifest = build("ok_charter_cleared", seed=3).inline_manifest()
    manifest["requester"] = {**manifest["requester"], "position": "Особая-Должность-Маркер"}
    manifest["operation"] = {**manifest["operation"], "counterparty_inn": "500100732259"}
    report = json.dumps(verify_case(load_case_inline(manifest)).as_dict(), ensure_ascii=False)
    hidden = {f["field"] for f in CHECKLIST["fields"] if not f["in_report"]}
    values = {
        "requester.position": "Особая-Должность-Маркер",
        "operation.counterparty_inn": "500100732259",
        "requester.snils": manifest["requester"]["snils"],
    }
    assert set(values) == hidden, "карта и проверка должны говорить об одних и тех же полях"
    for field, value in values.items():
        assert value not in report, f"{field} попал в отчёт, хотя цели у него в отчёте нет"


def _test_exists(reference: str) -> bool:
    path, _, name = reference.partition("::")
    file = ROOT / path
    if not file.exists():
        return False
    if not name:
        return True
    tree = ast.parse(file.read_text(encoding="utf-8"))
    return any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body)


def test_readiness_items_point_to_real_tests_or_name_an_owner_and_a_condition():
    ids = [item["id"] for item in CHECKLIST["readiness"]]
    assert len(ids) == len(set(ids))
    for item in CHECKLIST["readiness"]:
        assert item["owner"].strip() and item["block"] in _generator().BLOCKS, item["id"]
        if item["tests"]:
            missing = [t for t in item["tests"] if not _test_exists(t)]
            assert not missing, f"{item['id']}: нет тестов {missing}"
            assert not item["condition"], f"{item['id']}: у проверенного кодом пункта нет условия допуска"
        else:
            assert item["condition"].strip(), f"{item['id']}: без тестов нужен владелец и условие допуска"


def test_copies_cover_every_kind_of_record():
    kinds = {copy["id"] for copy in CHECKLIST["copies"]}
    required = {"reports", "pending", "reviews", "idempotency", "nonces", "rate-limit", "audit-journal", "backups"}
    assert required <= kinds
    for copy in CHECKLIST["copies"]:
        assert all(copy[key].strip() for key in ("where", "data", "retention", "deletion")), copy["id"]


def test_document_is_built_from_the_data_and_evidence():
    module = _generator()
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    expected = module.render(CHECKLIST, evidence)
    assert (ROOT / "docs" / "PILOT_CHECKLIST.md").read_text(encoding="utf-8") == expected, (
        "python scripts/gen_pilot_checklist.py"
    )


def test_bank_items_are_never_marked_verified_by_the_stand():
    module = _generator()
    evidence = {
        "pilot_readiness": {"checks": {i["id"]: {"status": "не проверено", "tests": 0} for i in CHECKLIST["readiness"]}}
    }
    text = module.render(CHECKLIST, evidence)
    for item in CHECKLIST["readiness"]:
        if not item["tests"]:
            line = next(line for line in text.splitlines() if f"| {item['check']} |" in line)
            assert "проверено**" not in line.replace("не проверено", ""), item["id"]


def test_readiness_status_is_set_by_the_test_run(monkeypatch):
    """«Проверено» ставит только прошедший прогон; упавший тест — «нарушено»."""
    module = _generator()

    class Done:
        def __init__(self, stdout: str, code: int) -> None:
            self.stdout, self.returncode = stdout, code

    data = {"source": "s", "readiness": [{"id": "x", "tests": ["tests/a.py"]}, {"id": "y", "tests": []}]}
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Done("PASSED tests/a.py::test_one\n", 0))
    assert module.readiness(data)["checks"] == {
        "x": {"status": "проверено", "tests": 1, "failed": 0},
        "y": {"status": "не проверено", "tests": 0, "failed": 0},
    }
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: Done("PASSED tests/a.py::test_one\nFAILED tests/a.py::test_two\n", 1)
    )
    assert module.readiness(data)["checks"]["x"]["status"] == "нарушено"
