"""Полный аудит 31.10.2026: отказ вместо исключения на чужих символах, строгий read-back якоря (#131),
сумма кейса в пределах I-JSON (#132), длина proofValue до декодирования base58.

14 из 21 теста падают на дереве 3a6826b. Остальные 7 держат то, что уже работало: обычный ответ read-back,
ответ не-объект, ИНН кириллицей и предельную сумму в удостоверении. Сценарии отказа —
docs/audit/AUDIT-2026-10-31-FULL.md.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime

import jsonschema
import pytest
from conftest import ROOT
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_r3_anchor import KEY
from test_r5_tlog import _HeaderResponse

from mandat.audit import anchor, attestation, journal
from mandat.audit.attestation import OperationRequest
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.services import webhooks

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
NONCE = "n-0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_journal_cache(monkeypatch):
    for name in ("MANDAT_JOURNAL_ANCHOR", "MANDAT_JOURNAL_ANCHOR_POLICY", "MANDAT_JOURNAL_ANCHOR_SECRET"):
        monkeypatch.delenv(name, raising=False)
    journal._TAILS.clear()


# --- K-01: nonce и токен представителя на чужих символах -----------------------------------------------


def _subject(nonce: str = NONCE) -> dict:
    return {
        "organizationInn": "7707083893",
        "operationDate": NOW.date().isoformat(),
        "operation": "payment_sign",
        "amountRub": None,
        "audience": "bank-dbo-msb",
        "nonce": nonce,
        "representativeToken": attestation.representative_token("123456789012", b"k" * 32),
    }


def _request(nonce: str = NONCE, representative_inn: str = "123456789012") -> OperationRequest:
    return OperationRequest(
        operation="payment_sign",
        amount_rub=None,
        representative_inn=representative_inn,
        audience="bank-dbo-msb",
        nonce=nonce,
        organization_inn="7707083893",
        operation_date=NOW.date(),
    )


def test_non_ascii_nonce_in_the_request_is_a_refusal_not_an_exception():
    """hmac.compare_digest на str с кириллицей бросает TypeError: допуск падал вместо отказа."""
    problems = attestation.check_request(
        _subject(), _request(nonce="нонс-0123456789abcdef"), pseudonym_key=b"k" * 32, consume_nonce=lambda n: True
    )
    assert any("nonce" in p for p in problems), problems


def test_non_ascii_nonce_issued_and_presented_is_accepted_once():
    used: set[str] = set()

    def consume(nonce: str) -> bool:
        if nonce in used:
            return False
        used.add(nonce)
        return True

    nonce = "ключ-запроса-0123456789"
    first = attestation.check_request(_subject(nonce), _request(nonce), pseudonym_key=b"k" * 32, consume_nonce=consume)
    assert first == []
    again = attestation.check_request(_subject(nonce), _request(nonce), pseudonym_key=b"k" * 32, consume_nonce=consume)
    assert any("уже использован" in p for p in again)


@pytest.mark.parametrize("inn", ["ИНН123456789012", "\ud800" * 12, None])
def test_strange_representative_inn_is_a_refusal_not_an_exception(inn):
    problems = attestation.check_request(
        _subject(), _request(representative_inn=inn), pseudonym_key=b"k" * 32, consume_nonce=lambda n: True
    )
    assert any("представител" in p for p in problems), problems


# --- K-02: подпись записи и свидетеля журнала на чужих символах ----------------------------------------


def _journal(tmp_path):
    path = tmp_path / "j.jsonl"
    for i in range(2):
        journal.append(path, action="event", actor="a", role="operator", case_id=f"c{i}", key=KEY)
    return path


def test_non_ascii_record_signature_is_reported_as_tampering(tmp_path):
    path = _journal(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["sig"] = "подделка"
    lines[0] = json.dumps(record, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    journal._TAILS.clear()
    check = journal.verify_journal(path, key=KEY, anchors=[])
    assert not check.ok
    assert check.broken_at == 1
    assert "подпись записи" in check.reason


def test_non_ascii_witness_mac_is_reported_as_tampering(tmp_path):
    path = _journal(tmp_path)
    witness = journal.witness_path(path)
    data = json.loads(witness.read_text(encoding="utf-8"))
    data["mac"] = "подделка"
    witness.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    journal._TAILS.clear()
    check = journal.verify_journal(path, key=KEY, anchors=[])
    assert not check.ok
    assert "подпись свидетеля" in check.reason


# --- K-03 (#131): ответ read-back разбирается строго и с пределом размера -------------------------------


class _Answer:
    """Получатель, который отвечает на read-back подписанным телом ``body``."""

    def __init__(self, secret: str, body: bytes):
        self.secret, self.body = secret, body

    def __call__(self, request):
        headers = {k.lower(): v for k, v in request.header_items()}
        signed = webhooks.sign_headers(self.secret, headers["webhook-id"], self.body, datetime.now(UTC))
        return _HeaderResponse(signed, self.body)


def _sink(body: bytes) -> anchor.WebhookSink:
    secret = webhooks.new_secret()
    return anchor.WebhookSink(
        "https://h.example/anchor", secret, _Answer(secret, body), readback="https://h.example/anchor/{sha256}"
    )


def test_readback_answer_deeper_than_the_limit_is_not_parsed():
    """#131: глубина 100 000 роняла проверку якоря RecursionError, а не «не разбирается»."""
    stmt = {"segment": 1}
    body = b"[" * 100_000 + b"]" * 100_000
    problem = _sink(body).remote_problem(stmt, {"sha256": anchor.digest(stmt), "delivery_id": "anchor-1"})
    assert problem is not None and "не разбирается" in problem


@pytest.mark.parametrize(
    "body",
    [b'{"sha256": "a", "sha256": "b", "statement": {}}', b'["statement"]', b'"statement"', b'{"statement": 1}'],
)
def test_readback_answer_that_is_not_a_strict_object_is_refused(body):
    stmt = {"segment": 1}
    receipt = {"sha256": anchor.digest(stmt), "delivery_id": "anchor-1"}
    problem = _sink(body).remote_problem(stmt, receipt)
    assert problem is not None, body


def test_oversized_readback_answer_is_refused_before_parsing():
    stmt = {"segment": 1}
    body = b'{"pad": "' + b"x" * (anchor.MAX_READBACK_BYTES + 1) + b'"}'
    problem = _sink(body).remote_problem(stmt, {"sha256": anchor.digest(stmt), "delivery_id": "anchor-1"})
    assert problem is not None and "больше" in problem


def test_good_readback_answer_still_passes():
    stmt = {"segment": 1}
    receipt = {"sha256": anchor.digest(stmt), "delivery_id": "anchor-1"}
    body = anchor.canonical({"sha256": receipt["sha256"], "statement": stmt, "delivery_id": "anchor-1"})
    assert _sink(body).remote_problem(stmt, receipt) is None


# --- K-04 (#132): сумма кейса в пределах точных целых I-JSON --------------------------------------------


def _manifest(amount):
    base = ROOT / "examples" / "cases" / "01_ok"
    manifest = json.loads((base / "case.json").read_text(encoding="utf-8"))
    for doc in manifest["documents"]:
        doc["content"] = (base / doc.pop("path")).read_text(encoding="utf-8")
    manifest["operation"]["amount_rub"] = amount
    return manifest


def test_amount_above_max_safe_integer_is_rejected_at_intake():
    """#132: такой кейс проходил приём, а удостоверение на него не выпускалось."""
    with pytest.raises(CaseError, match="amount_rub"):
        load_case_inline(_manifest(2**53))
    assert load_case_inline(_manifest(2**53 - 1)).operation.amount_rub == 2**53 - 1


def test_schema_and_loader_agree_on_the_amount_ceiling():
    schema = json.loads((ROOT / "docs" / "api" / "case-manifest.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    assert list(validator.iter_errors(_manifest(2**53)))
    assert not list(validator.iter_errors(_manifest(2**53 - 1)))


def test_largest_accepted_amount_can_be_attested():
    report = {
        "verdict": "confirmed",
        "profile": "strict",
        "organization": {"inn": "7707083893"},
        "operation": {"code": "payment_sign", "date": date(2026, 10, 31).isoformat(), "amount_rub": 2**53 - 1},
        "chain": {"poa_numbers": ["poc"]},
    }
    cred = attestation.issue(copy.deepcopy(report), Ed25519PrivateKey.generate(), now=NOW)
    assert cred["credentialSubject"]["amountRub"] == 2**53 - 1


# --- K-05: длина proofValue и ключа проверяется до base58 ---------------------------------------------


def test_oversized_proof_value_is_rejected_without_decoding(monkeypatch):
    """Перевод base58 квадратичен по длине: мегабайт в proofValue занимал процессор до отказа."""
    key = Ed25519PrivateKey.generate()
    cred = attestation.issue({"verdict": "confirmed", "operation": {}}, key, now=NOW)
    cred["proof"]["proofValue"] = "z" + "2" * 200_000
    calls: list[int] = []
    real = attestation.b58decode
    monkeypatch.setattr(attestation, "b58decode", lambda text: calls.append(len(text)) or real(text))
    assert attestation.verify(cred, key.public_key()) is False
    assert calls == []


def test_real_proof_value_fits_the_bound():
    for _ in range(50):
        key = Ed25519PrivateKey.generate()
        cred = attestation.issue({"verdict": "confirmed", "operation": {}}, key, now=NOW)
        assert len(cred["proof"]["proofValue"]) <= attestation.MAX_PROOF_VALUE_CHARS
        assert attestation.verify(cred, key.public_key())


def test_oversized_multibase_key_is_rejected_without_decoding(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(attestation, "b58decode", lambda text: calls.append(len(text)) or b"")
    with pytest.raises(ValueError, match="ключ"):
        attestation.public_key_from_multibase("z" + "2" * 10_000)
    assert calls == []


def test_real_multibase_key_fits_the_bound():
    key = Ed25519PrivateKey.generate()
    value = attestation.public_key_multibase(key)
    assert len(value) <= attestation.MAX_KEY_MULTIBASE_CHARS
    assert attestation.public_key_from_multibase(value).public_bytes_raw() == key.public_key().public_bytes_raw()
