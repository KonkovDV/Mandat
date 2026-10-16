"""Журнал, удостоверение, разбор человеком, ПДн, мониторинг, советник, оценка."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import run

from mandat.advisory.explainer import Fact, explain, facts_of
from mandat.advisory.gate import as_advisory_findings, ground_candidates
from mandat.advisory.llm import OpenAICompatClient, charter_messages, parse_facts
from mandat.audit import attestation, journal
from mandat.domain.model import TextDocument
from mandat.eval.metrics import cohen_kappa, required_n, wilson_interval
from mandat.eval.run import run_corpus
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.privacy.pii import find, pseudonymize
from mandat.review.state_machine import ReviewCase, ReviewError, ReviewState
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case


def test_journal_detects_edit_truncation_and_bad_key(tmp_path: Path):
    log = tmp_path / "j.jsonl"
    for i in range(3):
        journal.append(log, action="verify", actor="t", role="operator", case_id=f"c{i}", key="k1")
    assert journal.verify_journal(log, key="k1").ok
    assert not journal.verify_journal(log, key="other").ok
    lines = log.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[1])
    edited["case_id"] = "подмена"
    log.write_text("\n".join([lines[0], json.dumps(edited, ensure_ascii=False), lines[2]]) + "\n", encoding="utf-8")
    check = journal.verify_journal(log, key="k1")
    assert not check.ok and check.broken_at == 2
    log.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    assert "свидетель" in journal.verify_journal(log, key="k1").reason


def test_attestation_sign_verify_and_tamper(tmp_path: Path):
    key = attestation.load_or_create_key(tmp_path / "k.pem")
    report = run("ok").as_dict()
    cred = attestation.issue(report, key)
    public = attestation.public_key_from_multibase(attestation.public_key_multibase(key))
    assert attestation.verify(cred, public)
    assert cred["credentialSubject"]["verdict"] == "needs_review"
    assert report["requester"]["inn"] not in json.dumps(cred)
    cred["credentialSubject"]["verdict"] = "refuted"
    assert not attestation.verify(cred, public)
    data = b"\x00\x00hello"
    assert attestation.b58decode(attestation.b58encode(data)) == data


def test_review_requires_reason_and_valid_transition(tmp_path: Path):
    case = ReviewCase("c1", "h" * 64)
    with pytest.raises(ReviewError):
        case.apply("accept", actor="op", role="operator", reason="ok")
    case.apply("open", actor="op", role="operator", journal_path=tmp_path / "j.jsonl")
    with pytest.raises(ReviewError):
        case.apply("reject", actor="op", role="operator", reason=" ")
    with pytest.raises(ReviewError):
        case.apply("accept", actor="x", role="intern", reason="ok")
    assert (
        case.apply(
            "accept",
            actor="op",
            role="lawyer",
            reason="одобрение получено",
            journal_path=tmp_path / "j.jsonl",
        )
        is ReviewState.ACCEPTED
    )
    assert journal.verify_journal(tmp_path / "j.jsonl").records == 2


def test_pii_detects_and_pseudonymizes_russian_identifiers():
    from mandat.domain.identifiers import make_inn12, make_snils

    inn, snils = make_inn12("7712345678"), make_snils("123456789")
    text = f"Представитель ИНН {inn}, СНИЛС {snils}, тел. +7 (915) 123-45-67, почта a.b@bank.example"
    kinds = {k for k, _ in find(text)}
    assert {"ИНН", "СНИЛС", "ТЕЛЕФОН", "EMAIL"} <= kinds
    safe = pseudonymize(text)
    assert inn not in safe and snils not in safe and "a.b@bank.example" not in safe
    assert pseudonymize(text) == safe
    assert "1234567890123" in pseudonymize("ОГРН 1234567890123")


def test_monitoring_takeover_pattern_and_revocation(root: Path):
    payload = json.loads((root / "examples" / "events" / "takeover.json").read_text(encoding="utf-8"))
    rights, events = parse_events(payload)
    alerts = evaluate(rights, events)
    assert alerts[0].kind == "possible_takeover" and alerts[0].risk == "high"
    revoke = [e for e in events if e.kind == "EIO_CHANGED"][:1]
    alerts = evaluate(rights, revoke)
    assert alerts and alerts[0].kind == "eio_changed"
    assert any("не отзывать" in a for a in alerts[0].actions)
    number = payload["rights"][0]["poa_numbers"][0]
    payload["events"] = [
        {
            "kind": "POA_REVOKED",
            "org_inn": payload["rights"][0]["org_inn"],
            "at": "2026-10-22T10:00:00+03:00",
            "payload": {"number": number},
        }
    ]
    rights, events = parse_events(payload)
    assert evaluate(rights, events)[0].affected_rights == ("R-001",)


def test_advisory_cannot_change_verdict():
    from mandat.eval.synth import build

    sc = build("ok", seed=3)
    case = load_case_inline(sc.inline_manifest())
    doc = TextDocument("charter", "0" * 64, "charter_text", "Сделки свыше 100 рублей одобряет собрание.")
    gate = ground_candidates(
        [{"kind": "approval_threshold", "value": 100, "quote": "Сделки свыше 100 рублей", "confidence": 0.9}],
        doc,
    )
    assert len(gate.accepted) == 1
    plain = verify_case(case)
    report = verify_case(case, advisory=as_advisory_findings(gate.accepted))
    assert report.verdict == plain.verdict
    assert any(f.origin == "advisory" for f in report.findings)


def test_gate_drops_ungrounded_and_unsure_candidates():
    doc = TextDocument("c", "0" * 64, "charter_text", "Директор действует без доверенности.")
    gate = ground_candidates(
        [
            {"kind": "joint_signature", "quote": "только совместно", "confidence": 0.99},
            {"kind": "joint_signature", "quote": "Директор действует", "confidence": 0.2},
            {"kind": "verdict", "quote": "Директор действует", "confidence": 0.99},
        ],
        doc,
    )
    assert not gate.accepted and len(gate.dropped) == 3


def test_missing_amount_and_joint_flag_do_not_confirm():
    import re
    from copy import deepcopy

    from mandat.eval.synth import build

    limited = build("charter_limit", seed=1).inline_manifest()
    assert verify_case(load_case_inline(limited)).verdict.value == "needs_review"
    omitted = deepcopy(limited)
    omitted["operation"]["amount_rub"] = None
    negative = deepcopy(limited)
    negative["operation"]["amount_rub"] = -1
    assert verify_case(load_case_inline(omitted)).verdict.value == "needs_review"
    # С 0.3.0 строже: неположительную сумму не принимает уже загрузчик.
    with pytest.raises(CaseError):
        load_case_inline(negative)

    joint = build("joint_powers", seed=1).inline_manifest()
    xml = joint["documents"][0]["content"]
    assert "ПрСовмПолн" in xml
    stripped = deepcopy(joint)
    stripped["documents"][0]["content"] = re.sub(r'\sПрСовмПолн="[^"]*"', "", xml)
    report = verify_case(load_case_inline(stripped))
    assert report.verdict.value == "needs_review"
    assert any("не указан" in f.message for f in report.findings if f.rule_id == "R-JNT-001")


def test_takeover_window_keeps_revocation():
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    payload = {
        "rights": [
            {
                "right_id": "r1",
                "org_inn": "7700000000",
                "representative_inn": "1",
                "operation": "pay",
                "poa_numbers": ["n1"],
                "eio_inn": "old",
            }
        ],
        "events": [
            {"kind": "REQUISITES_CHANGE_REQUEST", "org_inn": "7700000000", "at": base.isoformat(), "payload": {}},
            {
                "kind": "NEW_POA",
                "org_inn": "7700000000",
                "at": (base + timedelta(hours=1)).isoformat(),
                "payload": {},
            },
            {
                "kind": "EIO_CHANGED",
                "org_inn": "7700000000",
                "at": (base + timedelta(hours=2)).isoformat(),
                "payload": {"new_eio_inn": "new"},
            },
            {
                "kind": "POA_REVOKED",
                "org_inn": "7700000000",
                "at": (base + timedelta(hours=3)).isoformat(),
                "payload": {"number": "n1"},
            },
        ],
    }
    rights, events = parse_events(payload)
    kinds = {alert.kind for alert in evaluate(rights, events)}
    assert {"possible_takeover", "poa_invalidated"} <= kinds


def test_explainer_drops_verdict_inversion():
    facts = [Fact("verdict", "Вердикт: ОПРОВЕРГНУТО")]

    def fake(_messages):
        return json.dumps(
            {
                "statements": [
                    {
                        "text": "Полномочия подтверждены, платёж можно проводить",
                        "fact_ids": ["verdict"],
                    }
                ]
            },
            ensure_ascii=False,
        )

    kept, dropped = explain("можно?", facts, fake)
    assert kept == [] and dropped == 1


def test_webhook_signature_roundtrip():
    from datetime import UTC, datetime

    from mandat.services.webhooks import canonical_body, delivery, signature_ok

    payload = {"alerts": [{"kind": "possible_takeover"}]}
    now = datetime(2026, 10, 12, tzinfo=UTC)
    packed = delivery(payload, "secret", now=now)
    body = canonical_body(payload)
    assert signature_ok("secret", packed["headers"], body, now)
    assert not signature_ok("other", packed["headers"], body, now)


def test_explainer_keeps_only_grounded_statements():
    facts = [Fact("f1", "МЧД 7 отозвана 01.10.2026"), Fact("verdict", "Вердикт: ОПРОВЕРГНУТО")]

    def fake(_messages):
        return json.dumps(
            {
                "statements": [
                    {"text": "МЧД отозвана 01.10.2026", "fact_ids": ["f1"]},
                    {"text": "Сумма 900 000 рублей превышена", "fact_ids": ["f1"]},
                    {"text": "Без ссылки", "fact_ids": []},
                    {"text": "Чужой факт", "fact_ids": ["f9"]},
                ]
            },
            ensure_ascii=False,
        )

    kept, dropped = explain("почему?", facts, fake)
    assert [s.text for s in kept] == ["МЧД отозвана 01.10.2026"] and dropped == 3
    assert facts_of(run("revoked").as_dict())[0].id == "verdict"


def _redacted():
    from mandat.advisory.llm import case_charter_messages
    from mandat.eval import synth
    from mandat.intake.case_loader import load_case_inline

    return case_charter_messages(load_case_inline(synth.build("ok", 1).inline_manifest()))


def test_llm_client_is_closed_by_default(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(ValueError):
        OpenAICompatClient("https://api.external.example/v1", "m")
    client = OpenAICompatClient("http://127.0.0.1:8000/v1", "local")
    assert client.body(_redacted())["temperature"] == 0
    msgs = charter_messages("ИНН 7712345678 СНИЛС 112-233-445 95")
    assert "112-233-445 95" not in msgs[1]["content"]
    assert parse_facts('мусор {"facts":[{"kind":"joint_signature","quote":"q"}]} ') == [
        {"kind": "joint_signature", "quote": "q"}
    ]


def test_metrics():
    lo, hi = wilson_interval(0, 50)
    assert lo == 0.0 and 0.06 < hi < 0.08
    assert required_n(0.9, 0.05) == 139
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert abs(cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])) < 1e-9


def test_corpus_has_no_false_confirmations():
    metrics = run_corpus(2, seed=5)
    assert metrics["false_confirmed"] == 0
    assert metrics["agreement"] == 1.0, metrics["mismatches"]
    assert metrics["refute_recall"] == 1.0


def test_review_does_not_commit_when_journal_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = ReviewCase("c-audit", "h" * 64, state=ReviewState.IN_REVIEW)

    def boom(*_args, **_kwargs):
        raise OSError("нет диска")

    monkeypatch.setattr("mandat.review.state_machine.journal.append", boom)
    with pytest.raises(OSError):
        case.apply("accept", actor="lawyer-1", role="lawyer", reason="основание", journal_path=tmp_path / "j.jsonl")
    assert case.state == ReviewState.IN_REVIEW
    assert case.version == 0
    assert case.history == []


def test_fractional_amount_and_naive_event_time_are_rejected():
    from mandat.eval.synth import build
    from mandat.intake.case_loader import CaseError

    manifest = build("ok", seed=1).inline_manifest()
    manifest["operation"]["amount_rub"] = 10.5
    with pytest.raises(CaseError):
        load_case_inline(manifest)
    manifest["operation"]["amount_rub"] = True
    with pytest.raises(CaseError):
        load_case_inline(manifest)
    # С 0.3.0 время без пояса принимается как московское с предупреждением: иначе падала вся пачка.
    warnings: list[str] = []
    _, events = parse_events({"events": [{"kind": "NEW_POA", "org_inn": "1", "at": "2026-10-01T12:00:00"}]}, warnings)
    assert events[0].at.isoformat() == "2026-10-01T09:00:00+00:00"
    assert warnings and "UTC+3" in warnings[0]


def test_unknown_egrul_history_is_not_active():
    from datetime import date

    from mandat.domain.model import ExecutiveRecord, Person
    from mandat.eval.synth import build
    from mandat.intake.egrul import parse_egrul

    payload = json.loads(build("ok", seed=1).files["egrul.json"])
    del payload["status"]
    snapshot = parse_egrul(json.dumps(payload), doc_id="egrul")
    assert snapshot.status == "unknown"
    unknown = ExecutiveRecord(Person("А"), None, None, None, None)
    assert unknown.active_on(date(2026, 10, 20)) is False
    known = snapshot.executives[0]
    assert known.valid_from is not None and known.active_on(date(2026, 10, 20)) is True


@pytest.mark.parametrize(
    "statement",
    [
        "Иванов уполномочен подписывать платёжные поручения.",
        "Представитель имеет полномочия подписать платёж.",
        "Иванов наделён полномочиями на подписание платежей.",
        "Представитель правомочен подписать платёж.",
    ],
)
def test_explainer_drops_authority_wording_when_verdict_is_not_confirmed(statement):
    facts = [Fact("verdict", "Вердикт: на разбор"), Fact("f1", "Полномочие из МЧД покрывает подписание платежей")]
    raw = json.dumps({"statements": [{"text": statement, "fact_ids": ["f1"]}]}, ensure_ascii=False)
    kept, dropped = explain("Можно ли подписывать?", facts, lambda _messages: raw)
    assert kept == [] and dropped == 1


def test_explainer_keeps_negated_authority_wording():
    facts = [Fact("verdict", "Вердикт: опровергнуто"), Fact("f1", "МЧД отменена, представитель не уполномочен")]
    raw = json.dumps(
        {"statements": [{"text": "Представитель не уполномочен.", "fact_ids": ["f1"]}]}, ensure_ascii=False
    )
    kept, _ = explain("почему?", facts, lambda _messages: raw)
    assert [s.text for s in kept] == ["Представитель не уполномочен."]
