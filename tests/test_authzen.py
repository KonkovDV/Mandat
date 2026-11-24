"""Допуск операции по OpenID AuthZEN Authorization API 1.0 (Final, 12.01.2026).

Решение считает та же функция, что и ``POST /v1/operations/authorize``: стандарт меняет конверт, а не правила.
Проверяется то, что спецификация называет обязательным: метаданные PDP (раздел 9), отказ — 200 и
``decision: false`` (10.1.2), ошибка запроса — 400, без токена — 401 с ``WWW-Authenticate`` (11.3), возврат того же
``X-Request-ID`` (10.1.3), значения по умолчанию и три семантики пакета (7.1), ошибка одной оценки — в её ``context``
(7.2.1), неизвестные поля игнорируются (10.1.1), JSON читается как I-JSON (11.5).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from mandat.api import authzen
from mandat.api.app import create_app
from mandat.api.storage import SQLiteStore
from mandat.audit import attestation, journal
from mandat.audit.attestation import BANK_TZ
from mandat.audit.signing import Keyring
from mandat.audit.status_list import StatusRegistry
from mandat.services import admission

TOKEN = "t" * 40
PSEUDONYM_KEY = b"k" * 32
REP_INN = "123456789012"
ORG_INN = "7707083893"
AUDIENCE = "bank-dbo-msb"
PDP = "https://mandat.bank.test"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _today() -> str:
    return datetime.now(UTC).astimezone(BANK_TZ).date().isoformat()


class Issuer:
    """Издатель стенда: ключ, список статусов и удостоверения на запрос."""

    def __init__(self, tmp_path) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.status_dir = tmp_path / "status"
        self.registry = StatusRegistry.open(self.status_dir)

    def issue(self, nonce: str, verdict: str = "confirmed") -> dict:
        report = {
            "verdict": verdict,
            "profile": "strict",
            "organization": {"inn": ORG_INN},
            "requester": {"inn": REP_INN},
            "operation": {"code": "PAYMENT_SIGN", "date": _today(), "amount_rub": 500_000},
            "chain": {"poa_numbers": ["poc"]},
            "rule_pack": {"sha256": "a" * 64},
            "reproducibility_hash": "b" * 64,
        }
        index = self.registry.allocate({"org_inn": ORG_INN, "verdict": verdict, "poa_numbers": ["poc"]})
        cred = attestation.issue(
            report,
            self.key,
            status=self.registry.credential_status(index),
            pseudonym_key=PSEUDONYM_KEY,
            audience=AUDIENCE,
            nonce=nonce,
        )
        self.registry.save(self.key)
        return cred

    def config(self) -> admission.AdmissionConfig:
        return admission.AdmissionConfig(Keyring.of([self.key.public_key()]), self.status_dir, PSEUDONYM_KEY)


@pytest.fixture
def issuer(tmp_path) -> Issuer:
    return Issuer(tmp_path)


def _client(issuer: Issuer, tmp_path, **kw) -> TestClient:
    app = create_app(
        token=TOKEN,
        store=SQLiteStore(tmp_path / "store.db"),
        journal_path=tmp_path / "journal.jsonl",
        admission_config=issuer.config(),
        authzen_pdp=kw.pop("authzen_pdp", PDP),
        **kw,
    )
    return TestClient(app)


def _context(cred: dict, nonce: str, **changes) -> dict:
    return {"credential": cred, "nonce": nonce, "audience": AUDIENCE, "operation_risk": "payment", **changes}


def _evaluation(cred: dict, nonce: str, *, amount: int | None = 500_000, **context) -> dict:
    properties = {"operation_date": _today()}
    if amount is not None:
        properties["amount_rub"] = amount
    return {
        "subject": {"type": "representative", "id": REP_INN},
        "resource": {"type": "organization", "id": ORG_INN},
        "action": {"name": "PAYMENT_SIGN", "properties": properties},
        "context": _context(cred, nonce, **context),
    }


def _nonce(tag: str) -> str:
    return ("n-" + tag).ljust(20, "x")


# --- метаданные -----------------------------------------------------------------------------------------------


def test_metadata_names_only_the_endpoints_that_exist(issuer, tmp_path):
    answer = _client(issuer, tmp_path).get(authzen.METADATA_PATH, headers=HEADERS)
    assert answer.status_code == 200
    assert answer.json() == {
        "policy_decision_point": PDP,
        "access_evaluation_endpoint": PDP + "/access/v1/evaluation",
        "access_evaluations_endpoint": PDP + "/access/v1/evaluations",
    }
    assert answer.headers["content-type"].startswith("application/json")


def test_metadata_without_a_configured_identifier_uses_the_request_address_on_the_stand(issuer, tmp_path):
    answer = _client(issuer, tmp_path, authzen_pdp="").get(authzen.METADATA_PATH, headers=HEADERS)
    assert answer.json()["policy_decision_point"] == "http://testserver"


@pytest.mark.parametrize(
    "value", ["http://mandat.bank.test", "https://mandat.bank.test/?x=1", "https://mandat.bank.test/#a", "mandat"]
)
def test_pdp_identifier_must_be_https_without_query_or_fragment(value):
    with pytest.raises(ValueError):
        authzen.check_pdp_identifier(value)


def test_pdp_identifier_loses_the_trailing_slash_and_bad_value_stops_the_start(issuer, tmp_path):
    assert authzen.check_pdp_identifier("https://mandat.bank.test/") == PDP
    with pytest.raises(ValueError):
        _client(issuer, tmp_path, authzen_pdp="http://mandat.bank.test")


# --- одна оценка ----------------------------------------------------------------------------------------------


def test_confirmed_credential_is_permitted_once_and_the_replay_is_denied(issuer, tmp_path):
    nonce = _nonce("once")
    client = _client(issuer, tmp_path)
    body = _evaluation(issuer.issue(nonce), nonce)
    first = client.post(authzen.EVALUATION_PATH, json=body, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["decision"] is True
    assert set(first.json()["context"]) == {"checked_at"}
    again = client.post(authzen.EVALUATION_PATH, json=body, headers=HEADERS)
    assert again.status_code == 200
    context = again.json()["context"]
    assert again.json()["decision"] is False
    assert context["already_admitted"] is True and context["admitted_at"]
    assert context["reason_user"] == {"403": authzen.USER_REASON}


@pytest.mark.parametrize("verdict", ["refuted", "needs_review", "insufficient_data"])
def test_verdict_other_than_confirmed_is_a_deny_with_reasons_not_an_error(issuer, tmp_path, verdict):
    nonce = _nonce(verdict)
    answer = _client(issuer, tmp_path).post(
        authzen.EVALUATION_PATH, json=_evaluation(issuer.issue(nonce, verdict), nonce), headers=HEADERS
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["decision"] is False
    assert any("нужен confirmed" in p for p in body["context"]["problems"])
    assert "нужен confirmed" in body["context"]["reason_admin"]["403"]
    assert "admitted_at" not in body["context"], "пустые поля не пишутся (раздел 11.5)"


def test_decision_and_reasons_match_the_native_admission(issuer, tmp_path):
    """Тот же запрос с суммой больше проверенной: AuthZEN и /v1/operations/authorize отказывают одинаково."""
    client = _client(issuer, tmp_path)
    nonce_a, nonce_b = _nonce("native"), _nonce("authzen")
    native = client.post(
        "/v1/operations/authorize",
        json={
            "credential": issuer.issue(nonce_a),
            "request": {
                "organization_inn": ORG_INN,
                "operation": "PAYMENT_SIGN",
                "operation_date": _today(),
                "amount_rub": 600_000,
                "representative_inn": REP_INN,
                "audience": AUDIENCE,
                "nonce": nonce_a,
            },
            "operation_risk": "payment",
        },
        headers=HEADERS,
    ).json()
    standard = client.post(
        authzen.EVALUATION_PATH, json=_evaluation(issuer.issue(nonce_b), nonce_b, amount=600_000), headers=HEADERS
    ).json()
    assert native["allowed"] is standard["decision"] is False
    assert native["problems"] == standard["context"]["problems"]


def test_every_evaluation_is_written_to_the_journal_without_the_representative(issuer, tmp_path):
    nonce = _nonce("journal")
    body = _evaluation(issuer.issue(nonce), nonce)
    _client(issuer, tmp_path).post(authzen.EVALUATION_PATH, json=body, headers=HEADERS)
    records = [r for r in journal.read_journal(tmp_path / "journal.jsonl") if r["action"] == "authorize_operation"]
    assert len(records) == 1
    assert records[0]["details"]["via"] == "authzen" and records[0]["details"]["allowed"] is True
    assert REP_INN not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")


def test_unknown_fields_are_ignored(issuer, tmp_path):
    nonce = _nonce("unknown")
    body = _evaluation(issuer.issue(nonce), nonce)
    body["subject"]["properties"] = {"department": "МСБ"}
    body["action"]["properties"]["channel"] = "web"
    body["context"]["time"] = "2026-11-24T10:00:00+03:00"
    body["future_field"] = {"x": 1}
    answer = _client(issuer, tmp_path).post(authzen.EVALUATION_PATH, json=body, headers=HEADERS)
    assert answer.status_code == 200 and answer.json()["decision"] is True


# --- ошибки запроса и доступа ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spoil",
    [
        lambda b: b.pop("subject"),
        lambda b: b["subject"].pop("id"),
        lambda b: b["action"].pop("name"),
        lambda b: b["subject"].update(type="user"),
        lambda b: b["resource"].update(type="account"),
        lambda b: b["subject"].update(id="12345"),
        lambda b: b["context"].pop("credential"),
        lambda b: b["context"].update(nonce="short"),
        lambda b: b["context"].pop("audience"),
        lambda b: b["action"]["properties"].pop("operation_date"),
        lambda b: b["action"]["properties"].update(operation_date="20.10.2026"),
        lambda b: b["action"]["properties"].update(amount_rub=True),
        lambda b: b["action"]["properties"].update(amount_rub=-1),
        lambda b: b.update(context=None),
    ],
)
def test_malformed_request_is_400_and_spends_no_nonce(issuer, tmp_path, spoil):
    nonce = _nonce("spoil")
    client = _client(issuer, tmp_path)
    cred = issuer.issue(nonce)
    body = _evaluation(cred, nonce)
    spoil(body)
    answer = client.post(authzen.EVALUATION_PATH, json=body, headers=HEADERS)
    assert answer.status_code == 400, answer.text
    assert answer.json()["detail"] == authzen.BAD_REQUEST
    good = client.post(authzen.EVALUATION_PATH, json=_evaluation(cred, nonce), headers=HEADERS)
    assert good.json()["decision"] is True, "отвергнутый запрос не должен расходовать nonce"


@pytest.mark.parametrize("raw", [b'{"subject": {}, "subject": {}}', b"[1, 2]", b'{"a": NaN}', b"not json"])
def test_body_that_is_not_an_i_json_object_is_400(issuer, tmp_path, raw):
    answer = _client(issuer, tmp_path).post(
        authzen.EVALUATION_PATH, content=raw, headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert answer.status_code == 400


def test_missing_and_wrong_token_get_401_with_a_bearer_challenge(issuer, tmp_path):
    client = _client(issuer, tmp_path)
    missing = client.post(authzen.EVALUATION_PATH, json={})
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == 'Bearer realm="mandat"'
    wrong = client.get(authzen.METADATA_PATH, headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401
    assert 'error="invalid_token"' in wrong.headers["www-authenticate"]


def test_read_only_role_gets_403(issuer, tmp_path):
    import hashlib
    import json

    token = "a" * 40
    users = json.dumps([{"name": "aud", "role": "auditor", "token_sha256": hashlib.sha256(token.encode()).hexdigest()}])
    app = create_app(
        users=users,
        store=SQLiteStore(tmp_path / "s.db"),
        journal_path=tmp_path / "j.jsonl",
        admission_config=issuer.config(),
        authzen_pdp=PDP,
    )
    nonce = _nonce("auditor")
    answer = TestClient(app).post(
        authzen.EVALUATION_PATH,
        json=_evaluation(issuer.issue(nonce), nonce),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert answer.status_code == 403


def test_request_id_from_the_caller_is_returned(issuer, tmp_path):
    client = _client(issuer, tmp_path)
    rid = "bfe9eb29-ab87-4ca3-be83-a1d5d8305716"
    nonce = _nonce("rid")
    answer = client.post(
        authzen.EVALUATION_PATH, json=_evaluation(issuer.issue(nonce), nonce), headers={**HEADERS, "X-Request-ID": rid}
    )
    assert answer.headers["x-request-id"] == rid
    bad = client.post(authzen.EVALUATION_PATH, json={}, headers={**HEADERS, "X-Request-ID": rid})
    assert bad.status_code == 400 and bad.json()["request_id"] == rid
    unsafe = client.get(authzen.METADATA_PATH, headers={**HEADERS, "X-Request-ID": "id with spaces"})
    assert re.fullmatch(r"[0-9a-f]{16}", unsafe.headers["x-request-id"])


def test_caller_request_id_is_masked_in_the_server_log(issuer, tmp_path, caplog):
    import logging

    with caplog.at_level(logging.WARNING, "mandat.api"):
        _client(issuer, tmp_path).post(
            authzen.EVALUATION_PATH, json={}, headers={**HEADERS, "X-Request-ID": "4111111111111111"}
        )
    assert "4111111111111111" not in caplog.text and "request_id=<num>" in caplog.text


# --- пакет оценок -----------------------------------------------------------------------------------------------


def _batch(items: list[dict], semantic: str | None = None) -> dict:
    """Пакет с общими subject, resource и action: у каждой оценки свой context с удостоверением и nonce."""
    first = items[0]
    body = {
        "subject": first["subject"],
        "resource": first["resource"],
        "action": first["action"],
        "evaluations": [{"context": item["context"]} for item in items],
    }
    if semantic:
        body["options"] = {"evaluations_semantic": semantic}
    return body


def test_batch_with_defaults_returns_decisions_in_order(issuer, tmp_path):
    good, other, spoiled = _nonce("b1"), _nonce("b2"), _nonce("b3")
    items = [
        _evaluation(issuer.issue(good), good),
        _evaluation(issuer.issue(other, "refuted"), other),
        _evaluation(issuer.issue(spoiled), "short"),
    ]
    answer = _client(issuer, tmp_path).post(authzen.EVALUATIONS_PATH, json=_batch(items), headers=HEADERS)
    assert answer.status_code == 200, answer.text
    decisions = answer.json()["evaluations"]
    assert [d["decision"] for d in decisions] == [True, False, False]
    assert decisions[2]["context"]["error"]["status"] == 400
    assert "decision" not in answer.json(), "при пакете верхний decision не пишется (раздел 7.2)"


def test_deny_on_first_deny_stops_and_keeps_the_rest_unspent(issuer, tmp_path):
    a, b, c = _nonce("d1"), _nonce("d2"), _nonce("d3")
    third = issuer.issue(c)
    items = [_evaluation(issuer.issue(a), a), _evaluation(issuer.issue(b, "refuted"), b), _evaluation(third, c)]
    client = _client(issuer, tmp_path)
    decisions = client.post(authzen.EVALUATIONS_PATH, json=_batch(items, "deny_on_first_deny"), headers=HEADERS).json()[
        "evaluations"
    ]
    assert [d["decision"] for d in decisions] == [True, False]
    later = client.post(authzen.EVALUATION_PATH, json=_evaluation(third, c), headers=HEADERS)
    assert later.json()["decision"] is True, "невыполненная оценка не расходует nonce"


def test_permit_on_first_permit_stops_after_the_first_permit(issuer, tmp_path):
    a, b, c = _nonce("p1"), _nonce("p2"), _nonce("p3")
    third = issuer.issue(c)
    items = [_evaluation(issuer.issue(a, "needs_review"), a), _evaluation(issuer.issue(b), b), _evaluation(third, c)]
    client = _client(issuer, tmp_path)
    decisions = client.post(
        authzen.EVALUATIONS_PATH, json=_batch(items, "permit_on_first_permit"), headers=HEADERS
    ).json()["evaluations"]
    assert [d["decision"] for d in decisions] == [False, True]
    assert client.post(authzen.EVALUATION_PATH, json=_evaluation(third, c), headers=HEADERS).json()["decision"]


def test_batch_without_evaluations_answers_like_a_single_evaluation(issuer, tmp_path):
    nonce = _nonce("compat")
    body = _evaluation(issuer.issue(nonce), nonce)
    answer = _client(issuer, tmp_path).post(authzen.EVALUATIONS_PATH, json=body, headers=HEADERS)
    assert answer.status_code == 200
    assert answer.json()["decision"] is True and "evaluations" not in answer.json()


def test_evaluation_without_subject_and_without_default_rejects_the_whole_batch(issuer, tmp_path):
    first, second = _nonce("w1"), _nonce("w2")
    cred = issuer.issue(first)
    body = {
        "evaluations": [
            _evaluation(cred, first),
            {k: v for k, v in _evaluation(issuer.issue(second), second).items() if k != "subject"},
        ]
    }
    client = _client(issuer, tmp_path)
    assert client.post(authzen.EVALUATIONS_PATH, json=body, headers=HEADERS).status_code == 400
    after = client.post(authzen.EVALUATION_PATH, json=_evaluation(cred, first), headers=HEADERS)
    assert after.json()["decision"] is True, "ошибка структуры пакета проверяется до первой оценки"


@pytest.mark.parametrize(
    "options", [{"evaluations_semantic": "any"}, {"evaluations_semantic": ""}, {"evaluations_semantic": 1}]
)
def test_unknown_semantic_is_400(issuer, tmp_path, options):
    nonce = _nonce("sem")
    body = {**_batch([_evaluation(issuer.issue(nonce), nonce)]), "options": options}
    assert _client(issuer, tmp_path).post(authzen.EVALUATIONS_PATH, json=body, headers=HEADERS).status_code == 400


def test_batch_is_limited(issuer, tmp_path):
    nonce = _nonce("many")
    item = _evaluation(issuer.issue(nonce), nonce)
    body = {"evaluations": [item] * (authzen.MAX_EVALUATIONS + 1)}
    assert _client(issuer, tmp_path).post(authzen.EVALUATIONS_PATH, json=body, headers=HEADERS).status_code == 400


def test_audience_of_another_channel_is_an_error_of_that_evaluation_only(issuer, tmp_path):
    import hashlib
    import json

    token = "d" * 40
    users = json.dumps(
        [
            {
                "name": "dbo-msb",
                "role": "dbo",
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "audiences": [AUDIENCE],
            }
        ]
    )
    app = create_app(
        users=users,
        store=SQLiteStore(tmp_path / "s.db"),
        journal_path=tmp_path / "j.jsonl",
        admission_config=issuer.config(),
        authzen_pdp=PDP,
    )
    client = TestClient(app)
    good, foreign = _nonce("aud1"), _nonce("aud2")
    items = [_evaluation(issuer.issue(good), good), _evaluation(issuer.issue(foreign), foreign, audience="other")]
    body = {"evaluations": items}
    decisions = client.post(authzen.EVALUATIONS_PATH, json=body, headers={"Authorization": f"Bearer {token}"}).json()
    assert [d["decision"] for d in decisions["evaluations"]] == [True, False]
    assert decisions["evaluations"][1]["context"]["error"]["status"] == 403
    single = client.post(authzen.EVALUATION_PATH, json=items[1], headers={"Authorization": f"Bearer {token}"})
    assert single.status_code == 403


def test_without_admission_configured_the_pdp_is_503(tmp_path):
    app = create_app(token=TOKEN, store=SQLiteStore(tmp_path / "s.db"), journal_path=tmp_path / "j.jsonl")
    client = TestClient(app)
    assert client.get(authzen.METADATA_PATH, headers=HEADERS).status_code == 503
    body = _evaluation({"type": ["VerifiableCredential"]}, _nonce("off"))
    assert client.post(authzen.EVALUATION_PATH, json=body, headers=HEADERS).status_code == 503
    assert client.post(authzen.EVALUATIONS_PATH, json={"evaluations": [body]}, headers=HEADERS).status_code == 503
