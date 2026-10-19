"""R12: внешний JSON читается строго. Повтор ключа, NaN и лишняя глубина — ошибка входа."""

from __future__ import annotations

import argparse
import json

import pytest
from fastapi.testclient import TestClient

from mandat.advisory.explainer import _statements
from mandat.advisory.llm import parse_facts
from mandat.api.app import create_app
from mandat.api.principals import load_users
from mandat.intake.case_loader import CaseError, load_case_dir
from mandat.intake.egrul import EgrulError, parse_egrul
from mandat.rules.classifier import import_esnsi, import_kpmp
from mandat.strictjson import MAX_JSON_DEPTH, StrictJsonError, strict_loads

AUTH = {"Authorization": "Bearer t", "Content-Type": "application/json"}


def test_duplicate_key_nan_infinity_and_depth_are_rejected() -> None:
    with pytest.raises(StrictJsonError, match="ключ"):
        strict_loads('{"a": 1, "a": 2}')
    with pytest.raises(StrictJsonError, match="NaN"):
        strict_loads('{"a": NaN}')
    with pytest.raises(StrictJsonError, match="NaN"):
        strict_loads('{"a": Infinity}')
    with pytest.raises(StrictJsonError, match="NaN"):
        strict_loads('{"a": -Infinity}')
    deep = "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1)
    with pytest.raises(StrictJsonError, match="вложенност"):
        strict_loads(deep)
    assert isinstance(strict_loads("[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH), list)
    assert strict_loads('{"note": "NaN [ ]"}') == {"note": "NaN [ ]"}


def test_manifest_with_duplicate_registry_is_an_input_error(tmp_path) -> None:
    (tmp_path / "case.json").write_text('{"registry": "a", "registry": "b"}', encoding="utf-8")
    with pytest.raises(CaseError, match="строгий JSON"):
        load_case_dir(tmp_path)


def test_api_duplicate_key_is_422() -> None:
    client = TestClient(create_app("t", rate_per_min=0))
    manifest = b'{"registry": "a", "registry": "b"}'
    assert client.post("/v1/cases", content=manifest, headers=AUTH).status_code == 422
    events = b'{"events": [], "events": [1]}'
    assert client.post("/v1/events", content=events, headers=AUTH).status_code == 422


def test_nan_in_egrul_is_an_input_error() -> None:
    with pytest.raises(EgrulError, match="JSON"):
        parse_egrul('{"status": NaN, "executives": []}', doc_id="egrul")


def test_model_answer_with_duplicate_key_yields_no_facts() -> None:
    assert parse_facts('{"facts": [{"quote": "a", "quote": "b"}]}') == []
    assert _statements('{"statements": [{"text": "a", "text": "b"}]}') == []


def test_attestation_with_duplicate_subject_is_rejected(tmp_path) -> None:
    from mandat.cli import cmd_attest_verify

    path = tmp_path / "cred.json"
    path.write_text(
        '{"credentialSubject": {"verdict": "refuted"}, "credentialSubject": {"verdict": "confirmed"}}',
        encoding="utf-8",
    )
    assert cmd_attest_verify(argparse.Namespace(path=str(path))) != 0


def test_users_and_classifier_imports_reject_duplicate_keys() -> None:
    with pytest.raises(json.JSONDecodeError):
        load_users('[{"name": "a", "name": "b"}]')
    with pytest.raises(json.JSONDecodeError):
        import_esnsi(b'[{"CODE": "A", "CODE": "B"}]', source="j")
    with pytest.raises(json.JSONDecodeError):
        import_kpmp(
            b'{"date": "2026-01-01T00:00:00Z", "classRecords": [], "classRecords": []}',
            source="j",
        )


def test_status_list_with_duplicate_key_is_rejected(tmp_path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from mandat.audit.status_list import check_status

    key = Ed25519PrivateKey.generate().public_key()
    cred = {"credentialStatus": [{"statusPurpose": "revocation", "statusListCredential": "list"}]}
    (tmp_path / "revocation.json").write_text('{"id": "a", "id": "b"}', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError, match="ключ"):
        check_status(cred, tmp_path, key)
