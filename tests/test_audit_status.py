"""Списки статусов W3C, дерево Меркла журнала, апелляция, форматы, классификатор, плашка стенда."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from conftest import run

from mandat.audit import attestation, journal, merkle
from mandat.audit.status_list import (
    MIN_BITS,
    StatusRegistry,
    check_status,
    decode_bits,
    encode_bits,
    get_bit,
    set_bit,
)
from mandat.cli import main
from mandat.eval.synth import build
from mandat.intake.emchd import EmchdError
from mandat.intake.formats import FORMATS, describe, parse_poa
from mandat.report.html import render
from mandat.review.state_machine import ReviewCase, ReviewError, ReviewState
from mandat.rules.cedar_export import export_policies, export_schema
from mandat.rules.checks import normalize_org_name
from mandat.rules.classifier import default_classifier, from_payload, import_esnsi


def test_bitstring_encoding_follows_w3c_layout():
    bits = bytearray(MIN_BITS // 8)
    set_bit(bits, 0)
    set_bit(bits, 9)
    assert bits[0] == 0b1000_0000 and bits[1] == 0b0100_0000
    encoded = encode_bits(bits)
    assert encoded.startswith("u") and "=" not in encoded
    decoded = decode_bits(encoded)
    assert len(decoded) * 8 == MIN_BITS
    assert get_bit(decoded, 0) and get_bit(decoded, 9) and not get_bit(decoded, 1)


def test_status_list_revocation_is_signed_and_checked(tmp_path: Path):
    key = attestation.load_or_create_key(tmp_path / "k.pem")
    public = attestation.public_key_from_multibase(attestation.public_key_multibase(key))
    registry = StatusRegistry.open(tmp_path / "status")
    report = run("ok").as_dict()
    index = registry.allocate({"org_inn": report["organization"]["inn"], "verdict": "confirmed", "poa_numbers": []})
    cred = attestation.issue(report, key, status=registry.credential_status(index))
    registry.save(key)
    assert attestation.verify(cred, public)
    assert check_status(cred, tmp_path / "status", public)["state"] == "действует"

    registry = StatusRegistry.open(tmp_path / "status")
    assert registry.find(org_inn=report["organization"]["inn"]) == [index]
    assert registry.mark(index, "suspension", "possible_takeover")
    assert not registry.mark(index, "suspension", "possible_takeover")
    registry.save(key)
    assert check_status(cred, tmp_path / "status", public)["state"] == "приостановлено"

    from mandat.audit.status_list import published_root

    root = published_root(tmp_path / "status")
    listed = json.loads((root / "suspension.json").read_text(encoding="utf-8"))
    listed["credentialSubject"]["encodedList"] = encode_bits(bytearray(MIN_BITS // 8))
    (root / "suspension.json").write_text(json.dumps(listed), encoding="utf-8")
    with pytest.raises(ValueError, match="подпись"):
        check_status(cred, tmp_path / "status", public)


def test_attestation_carries_chain_and_status():
    report = run("ok_subdelegation").as_dict()
    key = attestation.Ed25519PrivateKey.generate()
    cred = attestation.issue(report, key, status=[{"statusPurpose": "revocation"}])
    assert cred["credentialSubject"]["authorityChain"] == report["chain"]["poa_numbers"]
    assert len(report["chain"]["poa_numbers"]) == 2
    assert cred["credentialStatus"][0]["statusPurpose"] == "revocation"


def test_merkle_inclusion_proofs_for_every_size():
    for size in range(1, 34):
        leaves = [merkle.leaf_hash(os.urandom(8)) for _ in range(size)]
        tree = merkle.root(leaves)
        for i in range(size):
            proof = merkle.inclusion_proof(i, leaves)
            assert merkle.verify_inclusion(leaves[i], i, size, proof, tree)
            assert len(proof) <= size.bit_length()
            if size > 1:
                assert not merkle.verify_inclusion(leaves[(i + 1) % size], i, size, proof, tree)
    assert not merkle.verify_inclusion(leaves[0], 40, size, [], tree)


def test_journal_proof_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    log = tmp_path / "j.jsonl"
    for i in range(5):
        journal.append(log, action="verify", actor="t", role="operator", case_id=f"c{i}")
    assert main(["journal-proof", str(log), "--seq", "4"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] and out["size"] == 5 and out["record"]["case_id"] == "c3"
    assert main(["journal-proof", str(log), "--seq", "9"]) == 2


def test_monitor_revokes_and_suspends_attestations(root: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(root)
    demo = tmp_path / "demo"
    assert main(["demo", "--out", str(demo)]) == 0
    key = (demo / "public_key.txt").read_text().strip()
    capsys.readouterr()
    status = ["--status-dir", str(demo / "status")]
    assert main(["monitor", str(root / "examples" / "events" / "poa_revoked.json"), *status]) == 0
    changes = json.loads(capsys.readouterr().out)["status_changes"]
    assert changes == [{"attestation": "attestations/03_subdelegation_ok.json", "status": "revocation"}]
    sub = str(demo / "attestations" / "03_subdelegation_ok.json")
    ok = str(demo / "attestations" / "01_ok.json")
    assert main(["attest-verify", sub, "--public-key", key, *status]) == 3
    assert main(["attest-verify", ok, "--public-key", key, *status]) == 0
    assert main(["monitor", str(root / "examples" / "events" / "takeover.json"), *status]) == 0
    assert main(["attest-verify", ok, "--public-key", key, *status]) == 3
    refuted = str(demo / "attestations" / "07_revoked.json")
    assert main(["attest-verify", refuted, "--public-key", key, *status]) == 0  # опровергнутые не трогаем


def test_review_appeal_of_auto_verdict(tmp_path: Path):
    case = ReviewCase("c1", "h" * 64, state=ReviewState.AUTO_DECIDED)
    with pytest.raises(ReviewError):
        case.apply("appeal", actor="op", role="operator")
    assert (
        case.apply(
            "appeal",
            actor="op",
            role="operator",
            reason="клиент не согласен",
            journal_path=tmp_path / "j.jsonl",
        )
        == ReviewState.QUEUED
    )
    assert case.apply("open", actor="op", role="operator") == ReviewState.IN_REVIEW
    record = journal.read_journal(tmp_path / "j.jsonl")[0]
    assert record["action"] == "review_appeal"
    assert record["details"]["on_behalf_of"] == "client"


def test_format_registry_rejects_planned_formats():
    assert FORMATS["EMCHD_1"].status == "supported"
    assert {row["format_id"] for row in describe()} >= {"EMCHD_1", "CBR_FINMARKET"}
    sc = build("ok", seed=3)
    raw = sc.files["poa-root.xml"].replace(b"EMCHD_1", b"CBR_FINMARKET")
    with pytest.raises(EmchdError, match="315-ФЗ"):
        parse_poa(raw, doc_id="x")


def test_classifier_dates_and_esnsi_import():
    clf = default_classifier()
    old = clf.get("SYN-BANK-PAY-SIGN-OLD")
    assert old is not None and old.active_on(date(2026, 1, 30)) and not old.active_on(date(2026, 1, 31))
    csv_raw = "CODE;MNEMONIC;NAME;STARTED;REVOKED\n1_X;MX;Полномочие;01.03.2022;\n2_Y;MY;Старое;2022-03-01;2025-12-31\n"
    snapshot = import_esnsi(csv_raw.encode(), source="test.csv")
    imported = from_payload(snapshot)
    assert not imported.synthetic and imported.get("2_Y").revoked == date(2025, 12, 31)
    as_json = import_esnsi(json.dumps([{"CODE": "1_X", "NAME": "n", "STARTED": "2022-03-01"}]).encode(), source="j")
    assert as_json["entries"][0]["started"] == "2022-03-01"
    with pytest.raises(ValueError):
        import_esnsi(b"A;B\n1;2\n", source="bad")


def test_org_names_compare_with_expanded_legal_form():
    assert normalize_org_name("ООО «Ромашка»") == normalize_org_name(
        'Общество с ограниченной ответственностью "Ромашка"'
    )
    assert normalize_org_name("АО «Ромашка»") != normalize_org_name("ООО «Ромашка»")


def test_report_shows_stand_banner():
    report = run("ok").as_dict()
    assert any("заглушка" in note for note in report["stand"])
    assert any("не ГОСТ" in note for note in report["stand"])
    assert "Режим стенда" in render(report)


def test_cedar_export_is_up_to_date(root: Path):
    assert (root / "policies" / "cedar" / "mandat.cedar").read_text(encoding="utf-8") == export_policies(), (
        "policies/cedar устарел: python scripts/export_cedar.py"
    )
    assert (root / "policies" / "cedar" / "mandat.cedarschema").read_text(encoding="utf-8") == export_schema()
    assert "SYN-BANK-PAY-SIGN-OLD" not in export_policies()


def test_effect_model_has_no_hidden_defaults(root: Path):
    import sys

    sys.path.insert(0, str(root / "scripts"))
    from effect_model import effect

    out = effect(cases=1000, coverage=0.5, t_manual=20, t_auto=5, rate=600)
    assert out["time_saving_rub"] == 1000 * 0.5 * 15 / 60 * 600
    slower = effect(cases=1000, coverage=0.5, t_manual=5, t_auto=20, rate=600)
    assert slower["time_saving_rub"] < 0
    assert out["incident_scenario_rub"] == 0
    with pytest.raises(ValueError):
        effect(cases=1, coverage=1.5, t_manual=1, t_auto=0, rate=1)


def test_repeated_classifier_code_is_rejected():
    payload = {
        "classifier_id": "c",
        "version": "1",
        "entries": [
            {"code": "1_X", "name": "a", "started": "2022-03-01", "revoked": "2024-01-01"},
            {"code": "1_X", "name": "a", "started": "2022-03-01", "revoked": None},
        ],
    }
    with pytest.raises(ValueError, match="1_X"):
        from_payload(payload)
    raw = b"CODE;NAME;STARTED;REVOKED\n1_X;a;2022-03-01;2024-01-01\n1_X;a;2022-03-01;\n"
    with pytest.raises(ValueError, match="1_X"):
        import_esnsi(raw, source="dup.csv")
