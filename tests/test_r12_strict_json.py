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


@pytest.mark.parametrize("text", ["1e309", "-1e309", '{"n": 1e309}', "[1, 2e400]"])
def test_overflow_to_infinity_is_rejected(text: str) -> None:
    """#122, RFC 7493 разд. 2.2: число вне IEEE 754 — ошибка входа, а не бесконечность."""
    with pytest.raises(StrictJsonError, match="NaN"):
        strict_loads(text)
    assert strict_loads("1.7976931348623157e308") == 1.7976931348623157e308


@pytest.mark.parametrize("text", ['"\\ud800"', '{"\\udead": 1}', '{"a": ["ok", "\\udc00"]}', '"\\ude00\\ud83d"'])
def test_lone_surrogates_are_rejected(text: str) -> None:
    """RFC 7493 разд. 2.1: одиночный суррогат запрещён и в экранированном виде, в UTF-8 его не записать."""
    with pytest.raises(StrictJsonError, match="суррогат"):
        strict_loads(text)


def test_noncharacters_are_kept_because_the_journal_is_read_strictly(tmp_path) -> None:
    """U+FFFE в основании решения приходит через API разбора; строгий читатель не должен ломать журнал."""
    from mandat.audit import journal

    assert strict_loads('"\\ufffe \\udbff\\udfff"') == "￾ \U0010ffff"
    log = tmp_path / "j.jsonl"
    journal.append(log, action="review_open", actor="rv", role="reviewer", case_id="c1", reason="x ￾ y")
    assert journal.verify_journal(log).ok


def test_valid_pairs_and_line_separator_are_kept() -> None:
    # Пара суррогатов — один символ U+1F600. U+2028 разрешён I-JSON, а JCS оставляет его без экранирования.
    assert strict_loads('"\\ud83d\\ude00"') == "\U0001f600"
    assert strict_loads('"a b"') == "a b"


def test_lone_surrogate_in_python_text_and_huge_integer_are_input_errors() -> None:
    with pytest.raises(StrictJsonError, match="суррогат"):
        strict_loads('"\ud800"')
    with pytest.raises(StrictJsonError, match="цифр"):
        strict_loads("1" * 5000)
    assert strict_loads("1" * 400) == int("1" * 400)


@pytest.mark.parametrize("idem", [None, "surrogate-1"])
def test_api_manifest_with_lone_surrogate_is_422_not_500(idem: str | None) -> None:
    client = TestClient(create_app("t", rate_per_min=0), raise_server_exceptions=False)
    body = b'{"case_id": "\\ud800", "operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20"}}'
    headers = {**AUTH, **({"Idempotency-Key": idem} if idem else {})}
    assert client.post("/v1/cases", content=body, headers=headers).status_code == 422
