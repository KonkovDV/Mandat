"""Удостоверение с полями не того типа — отказ с записью в журнал, а не 503 «хранилище недоступно».

``proof`` или ``credentialSubject`` строкой или списком роняли проверку на ``.get``: API отвечало 503, ДБО
считал хранилище недоступным и повторял запрос, а попытка не попадала в журнал.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest
from test_api_operation_admission import HEADERS, Issuer, _body, _client

from mandat.audit import attestation


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proof", "z3abc"),
        ("proof", ["z3abc"]),
        ("credentialSubject", ["verdict", "confirmed"]),
        ("credentialSubject", "confirmed"),
    ],
)
def test_malformed_credential_is_refused_and_journaled(tmp_path, field, value):
    issuer = Issuer(tmp_path)
    nonce = "n-" + "6" * 20
    body = _body(issuer.issue(nonce), nonce)
    body["credential"][field] = value
    answer = _client(issuer, tmp_path).post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert answer.status_code == 200, answer.text
    assert answer.json()["allowed"] is False
    assert answer.json()["problems"]
    (record,) = _records(tmp_path / "journal.jsonl")
    assert record["details"]["allowed"] is False


def test_validate_and_verify_do_not_raise_on_malformed_fields(tmp_path):
    issuer = Issuer(tmp_path)
    credential = issuer.issue("n-" + "8" * 20)
    for field, value in (("proof", "x"), ("credentialSubject", [1])):
        broken = copy.deepcopy(credential)
        broken[field] = value
        assert attestation.verify(broken, issuer.key.public_key()) is False
        problems = attestation.verify_cryptographic(broken, [issuer.key.public_key()], now=datetime.now(UTC))
        assert problems
