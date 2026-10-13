"""Регрессии повторного аудита 13.10.2026 (R01, R12, R13, R14, R18): каждый тест падал до исправления."""

from __future__ import annotations

import base64
import decimal
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mandat.advisory.gate import ground_candidates
from mandat.audit import attestation
from mandat.audit.jcs import canonicalize
from mandat.audit.status_list import StatusRegistry, decode_bits
from mandat.domain.model import TextDocument
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.intake.egrul import EgrulError, parse_egrul
from mandat.services.verify import verify_case

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)


@pytest.mark.parametrize("precision", [2, 4, 28, 50])
def test_jcs_does_not_depend_on_decimal_context(precision):
    with decimal.localcontext() as ctx:
        ctx.prec = precision
        assert canonicalize({"x": 1.23456789, "y": 0.1 + 0.2}) == '{"x":1.23456789,"y":0.30000000000000004}'


def test_attestation_rejects_stale_status_list(tmp_path: Path):
    key = attestation.Ed25519PrivateKey.generate()
    registry = StatusRegistry.open(tmp_path / "status")
    report = verify_case(load_case_inline(synth.build("ok", 1).inline_manifest()), profile="strict").as_dict()
    index = registry.allocate(
        {"org_inn": report["organization"]["inn"], "verdict": report["verdict"], "poa_numbers": []}
    )
    cred = attestation.issue(report, key, now=NOW, status=registry.credential_status(index))
    registry.save(key, now=NOW - timedelta(days=8))
    problems = attestation.validate_attestation(
        cred, [key.public_key()], now=NOW + timedelta(minutes=1), status_dir=tmp_path / "status"
    )
    assert any("устарел" in p for p in problems)


def test_status_list_decompression_is_bounded():
    bomb = gzip.compress(bytes(64 << 20), mtime=0)
    encoded = "u" + base64.urlsafe_b64encode(bomb).decode().rstrip("=")
    with pytest.raises(ValueError, match="больше"):
        decode_bits(encoded)


def test_nested_json_inside_egrul_is_an_input_error():
    deep = '{"status":"active","executives":' + "[" * 100_000 + "]" * 100_000 + "}"
    with pytest.raises(EgrulError):
        parse_egrul(deep, doc_id="egrul")


def test_egrul_unreliable_flag_must_be_boolean():
    payload = {"status": "active", "executives": [{"full_name": "Иванов И. И.", "unreliable": "false"}]}
    with pytest.raises(EgrulError, match="unreliable"):
        parse_egrul(json.dumps(payload, ensure_ascii=False), doc_id="egrul")


def test_huge_integer_confidence_is_dropped_not_crash():
    doc = TextDocument(
        doc_id="charter",
        sha256="0" * 64,
        kind="charter_text",
        text="Сделки свыше 1000000 рублей одобряет совет директоров.",
    )
    raw = [
        {"kind": "approval_threshold", "value": 1000000, "quote": "Сделки свыше 1000000 рублей", "confidence": 10**400}
    ]
    result = ground_candidates(raw, doc)
    assert result.accepted == ()
    assert any("уверенность" in reason for reason in result.dropped)


def _effects(report, rule_id: str) -> set[str]:
    return {f.effect.value for f in report.findings if f.rule_id == rule_id}


def test_client_bankruptcy_answer_is_not_trusted_in_strict():
    manifest = synth.build("ok", 1).inline_manifest()
    plain = load_case_inline(manifest)
    # Клиент сам пишет в пакете «процедуры нет» по доверителю и заявителю.
    manifest["bankruptcy"] = {plain.organization.inn: "none", plain.requester.inn: "none"}
    case = load_case_inline(manifest)
    trusted = frozenset({"registry", "egrul", "signature"})
    report = verify_case(case, profile="strict", trusted_sources=trusted)
    assert "not_verified" in _effects(report, "R-BNK-001")
    assert report.verdict.value != "confirmed"
    vouched = verify_case(case, profile="strict", trusted_sources=trusted | {"bankruptcy"})
    assert "not_verified" not in _effects(vouched, "R-BNK-001")


def test_r_sub_007_foreign_child_needs_review():
    from mandat.domain.identifiers import make_inn12
    from mandat.intake.emchd import parse_emchd

    manifest = synth.build("lost_on_subdelegation", 1).inline_manifest()
    child = next(doc for doc in manifest["documents"] if doc["id"] == "poa-child")
    signer_inn = parse_emchd(child["content"], doc_id="poa-child").signer.inn
    assert signer_inn
    child["content"] = child["content"].replace(signer_inn, make_inn12("7700000001"))
    report = verify_case(load_case_inline(manifest))
    assert _effects(report, "R-SUB-007") == {"needs_review"}
    assert report.verdict.value != "refuted"


def test_concurrent_create_with_same_case_id_does_not_overwrite():
    import threading

    from fastapi.testclient import TestClient

    from mandat.api.app import create_app
    from mandat.api.storage import MemoryStore

    class RacyStore(MemoryStore):
        """Оба запроса читают хранилище до того, как любой из них запишет кейс."""

        def __init__(self) -> None:
            super().__init__()
            self.barrier = threading.Barrier(2, timeout=2)

        def get(self, case_id):
            found = super().get(case_id)
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass  # второй запрос до чтения не дошёл: его остановила заявка на создание
            return found

    client = TestClient(create_app(token="t", rate_per_min=0, store=RacyStore()))
    manifest = synth.build("ok", 1).inline_manifest()
    codes: list[int] = []

    def post() -> None:
        codes.append(client.post("/v1/cases", json=manifest, headers={"Authorization": "Bearer t"}).status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert sorted(codes) == [200, 409]


@pytest.mark.parametrize(
    "raw",
    [
        "11223344595",  # СНИЛС без дефисов
        "40702810900000000001",  # расчётный счёт
        "500100732258",  # 12-значный ИНН с неверной контрольной суммой
    ],
)
def test_pseudonymize_masks_compact_snils_accounts_and_mistyped_inn(raw):
    from mandat.privacy.pii import pseudonymize

    masked = pseudonymize(f"Реквизит {raw} в тексте")
    assert raw not in masked
    assert "[" in masked


def test_pii_token_is_64_bit():
    import re as _re

    from mandat.privacy.pii import pseudonymize

    assert _re.fullmatch(r"\[ИНН:[0-9a-f]{16}\]", pseudonymize("7707083893"))


def test_registry_snapshot_from_the_future_is_not_evidence():
    manifest = synth.build("ok", 1).inline_manifest()
    manifest["registry"] = {
        number: {"status": status, "observed_at": "2099-01-01T00:00:00+00:00"}
        for number, status in manifest["registry"].items()
    }
    report = verify_case(load_case_inline(manifest), now=NOW)
    assert "not_verified" in _effects(report, "R-REG-001")
    assert report.verdict.value != "confirmed"


def test_short_journal_key_is_refused_outside_the_stand(tmp_path, monkeypatch):
    from mandat.audit import journal

    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "k")
    with pytest.raises(journal.JournalKeyMissing, match="короче"):
        journal.append(tmp_path / "j.jsonl", action="verify", actor="t", role="operator", case_id="c1")
    assert not (tmp_path / "j.jsonl").exists()


def test_nan_confidence_threshold_is_rejected():
    doc = TextDocument(doc_id="charter", sha256="0" * 64, kind="charter_text", text="Сделки свыше 5 рублей.")
    raw = [{"kind": "approval_threshold", "value": 5, "quote": "Сделки свыше 5 рублей", "confidence": 0.01}]
    with pytest.raises(ValueError, match="min_confidence"):
        ground_candidates(raw, doc, min_confidence=float("nan"))


def test_deeply_nested_model_answer_yields_no_facts():
    from mandat.advisory.explainer import _statements
    from mandat.advisory.llm import parse_facts

    deep = '{"facts":' + "[" * 100_000 + "]" * 100_000 + "}"
    assert parse_facts(deep) == []
    assert _statements(deep.replace("facts", "statements")) == []
