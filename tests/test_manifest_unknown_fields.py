"""Неизвестные поля манифеста.

Опечатка ``registy`` раньше молча делала реестр недоступным. В защищённом режиме загрузчик отвергает
манифест так же, как JSON Schema; на стенде кейс проверяется, а отчёт называет непрочитанное поле.
"""

from __future__ import annotations

import copy
import json
import re

import jsonschema
import pytest
from conftest import ROOT
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.storage import SQLiteStore
from mandat.cli import main
from mandat.eval.synth import DEFECTS, build
from mandat.intake import case_loader
from mandat.intake.case_loader import CaseError, load_case_dir, load_case_inline, unknown_fields
from mandat.report.html import render
from mandat.services.verify import verify_case

SCHEMA = json.loads((ROOT / "docs" / "api" / "case-manifest.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())
TOKEN = "t" * 40


def _inline(name: str = "01_ok") -> dict:
    base = ROOT / "examples" / "cases" / name
    manifest = json.loads((base / "case.json").read_text(encoding="utf-8"))
    for doc in manifest["documents"]:
        doc["content"] = (base / doc.pop("path")).read_text(encoding="utf-8")
    return manifest


def _typo_top(m: dict) -> dict:
    m["registy"] = m.pop("registry")
    return m


def _typo_operation(m: dict) -> dict:
    m["operation"]["amout_rub"] = m["operation"].pop("amount_rub", 100)
    return m


def _typo_organization(m: dict) -> dict:
    m["organization"]["orgn"] = m["organization"].pop("ogrn")
    return m


def _typo_requester(m: dict) -> dict:
    m["requester"]["snlis"] = m["requester"].pop("snils", "00000000000")
    return m


def _typo_document(m: dict) -> dict:
    m["documents"][0]["sha256"] = "0" * 64
    return m


TYPOS = {
    "top level": (_typo_top, "registy"),
    "operation": (_typo_operation, "operation.amout_rub"),
    "organization": (_typo_organization, "organization.orgn"),
    "requester": (_typo_requester, "requester.snlis"),
    "document": (_typo_document, "documents[0].sha256"),
}


@pytest.fixture
def pilot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDAT_ENV", "pilot")


@pytest.mark.parametrize("name", sorted(TYPOS))
def test_unknown_field_is_named_with_its_path(name: str) -> None:
    change, path = TYPOS[name]
    assert unknown_fields(change(_inline())) == (path,)


@pytest.mark.parametrize("name", sorted(TYPOS))
def test_protected_mode_rejects_what_the_schema_rejects(name: str, pilot: None) -> None:
    change, path = TYPOS[name]
    manifest = change(_inline())
    assert list(VALIDATOR.iter_errors(manifest)), "схема должна видеть то же поле"
    with pytest.raises(CaseError, match=re.escape(repr(path))):
        load_case_inline(manifest)


@pytest.mark.parametrize("mode", ["pilot", "prod", "staging-unknown"])
def test_every_protected_mode_rejects_the_typo(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDAT_ENV", mode)
    with pytest.raises(CaseError, match="registy"):
        load_case_inline(_typo_top(_inline()))


@pytest.mark.parametrize("mode", ["demo", "dev", "test"])
def test_stand_keeps_the_verdict_and_names_the_field_in_the_report(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDAT_ENV", mode)
    clean = verify_case(load_case_inline(_inline())).as_dict()
    case = load_case_inline(_typo_top(_inline()))
    assert case.registry == {}
    report = verify_case(case).as_dict()
    named = [w for w in report["warnings"] if "registy" in w]
    assert len(named) == 1, report["warnings"]
    assert "не прочитано" in named[0]
    assert not [w for w in clean["warnings"] if "не прочитано" in w]
    assert "registy" in render(report)


def test_known_manifest_is_untouched_in_protected_mode(pilot: None) -> None:
    for name in ("01_ok", "03_subdelegation_ok", "14_old_director_charter_cleared"):
        assert load_case_inline(_inline(name)).manifest_notes == ()


def test_demo_cases_and_corpus_carry_no_unknown_fields() -> None:
    for path in sorted((ROOT / "examples" / "cases").glob("*/case.json")):
        assert unknown_fields(json.loads(path.read_text(encoding="utf-8"))) == (), path.parent.name
    for defect in DEFECTS:
        assert unknown_fields(build(defect, seed=11).inline_manifest()) == (), defect


def test_field_sets_are_the_schema_properties() -> None:
    """Списки полей в загрузчике совпадают со схемой: правка одного без другого валит тест."""
    props = SCHEMA["properties"]
    assert case_loader.MANIFEST_FIELDS == frozenset(props)
    for section in ("organization", "requester", "operation"):
        assert case_loader.SECTION_FIELDS[section] == frozenset(props[section]["properties"])
    assert case_loader.DOCUMENT_FIELDS == frozenset(props["documents"]["items"]["properties"])
    registry_object = props["registry"]["additionalProperties"]["oneOf"][1]
    assert frozenset(registry_object["properties"]) == case_loader._REGISTRY_KEYS


def test_hostile_field_name_is_bounded_and_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Имя поля приходит от клиента: в отчёт оно попадает укороченным, в карточку — экранированным."""
    monkeypatch.setenv("MANDAT_ENV", "demo")
    manifest = _inline()
    manifest["<script>alert(1)</script>" + "x" * 5000] = 1
    for i in range(40):
        manifest[f"extra_{i:02d}"] = i
    case = load_case_inline(manifest)
    assert len(case.manifest_notes) == 1
    note = case.manifest_notes[0]
    assert len(note) < 1200
    assert "ещё" in note
    html = render(verify_case(case).as_dict())
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_non_object_sections_still_fail_as_before(pilot: None) -> None:
    manifest = _inline()
    manifest["operation"] = ["PAYMENT_SIGN"]
    with pytest.raises(CaseError, match="operation"):
        load_case_inline(manifest)
    manifest = _inline()
    manifest["documents"] = [["not", "an", "object"]]
    with pytest.raises(CaseError, match="документ"):
        load_case_inline(manifest)


def test_cli_exits_with_case_error_in_protected_mode(tmp_path, pilot: None, capsys: pytest.CaptureFixture[str]) -> None:
    src = ROOT / "examples" / "cases" / "01_ok"
    for item in src.iterdir():
        (tmp_path / item.name).write_bytes(item.read_bytes())
    manifest = json.loads((tmp_path / "case.json").read_text(encoding="utf-8"))
    manifest["registy"] = manifest.pop("registry")
    (tmp_path / "case.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CaseError, match="registy"):
        load_case_dir(tmp_path)
    assert main(["verify", str(tmp_path)]) == 2
    assert "registy" in capsys.readouterr().err


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path, mode: str) -> TestClient:
    monkeypatch.setenv("MANDAT_ENV", mode)
    if mode == "pilot":
        monkeypatch.setenv("MANDAT_JOURNAL_KEY", "j" * 40)
        monkeypatch.setenv("MANDAT_PII_KEY", "p" * 40)
    app = create_app(token=TOKEN, store=SQLiteStore(tmp_path / "store.db"), journal_path=tmp_path / "journal.jsonl")
    return TestClient(app)


def test_api_answers_422_in_protected_mode(monkeypatch: pytest.MonkeyPatch, tmp_path, caplog) -> None:
    """Клиент получает 422 и request_id; имя поля — в журнале сервера, как у остальных ошибок входа."""
    client = _client(monkeypatch, tmp_path, "pilot")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with caplog.at_level("WARNING", logger="mandat.api"):
        bad = client.post("/v1/cases", json=_typo_top(_inline()), headers=headers)
        nested = client.post("/v1/cases", json=_typo_operation(_inline()), headers=headers)
    assert bad.status_code == 422, bad.text
    assert nested.status_code == 422, nested.text
    assert set(bad.json()) == {"detail", "request_id"}
    assert "registy" in caplog.text
    assert "amout_rub" in caplog.text


def test_api_on_the_stand_returns_the_report_with_a_warning(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, "demo")
    answer = client.post("/v1/cases", json=_typo_top(_inline()), headers={"Authorization": f"Bearer {TOKEN}"})
    assert answer.status_code == 200, answer.text
    assert any("registy" in w for w in answer.json()["warnings"])


def test_manifest_is_not_mutated() -> None:
    manifest = _typo_top(_inline())
    before = copy.deepcopy(manifest)
    unknown_fields(manifest)
    assert manifest == before
