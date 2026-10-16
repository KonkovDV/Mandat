"""Ветки, которые статический разбор 16.10.2026 назвал открытыми.

Устав с найденным фрагментом, соседние передоверия в один день, блокировка журнала
после смерти писателя, список статусов без ``now`` в защищённом режиме.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import rule_effects

from mandat.audit import journal
from mandat.domain.identifiers import make_inn12
from mandat.domain.model import Person
from mandat.eval.synth import build
from mandat.intake.case_loader import load_case_inline
from mandat.intake.emchd import build_emchd_xml, parse_emchd
from mandat.services.verify import verify_case

_THRESHOLD = " Сделки на сумму свыше 100 000 000 рублей совершаются с одобрения общего собрания."


def _with_threshold(defect: str, seed: int = 4):
    scenario = build(defect, seed=seed)
    scenario.files["charter.txt"] += _THRESHOLD.encode()
    return verify_case(load_case_inline(scenario.inline_manifest()))


def test_partial_required_charter_blocks_confirmation() -> None:
    report = _with_threshold("ok")
    assert report.verdict.value != "confirmed"
    assert rule_effects(report, "R-CHR-002") == {"needs_review"}
    assert any("остальной текст устава" in finding.message for finding in report.findings)


def test_partial_charter_with_demo_clearance_stays_info() -> None:
    report = _with_threshold("ok_charter_cleared")
    assert report.verdict.value == "confirmed"
    assert rule_effects(report, "R-CHR-002") == {"info"}
    assert "needs_review" not in rule_effects(report, "R-CHR-001")


def test_partial_charter_on_optional_operation_stays_info() -> None:
    scenario = build("ok", seed=4)
    scenario.files["charter.txt"] += _THRESHOLD.encode()
    manifest = scenario.inline_manifest()
    manifest["operation"]["code"] = "STATEMENT_VIEW"
    report = verify_case(load_case_inline(manifest))
    assert rule_effects(report, "R-CHR-002") == {"info"}


def test_same_day_sibling_transfers_need_review() -> None:
    scenario = build("ok_subdelegation", seed=2)
    child = parse_emchd(scenario.files["poa-child.xml"].decode("utf-8"), doc_id="poa-child")
    assert child.parent_number and child.issue_date and child.number
    codes = [(a.code, a.mnemonic or "", a.name or "") for a in child.authorities if a.kind == "machine" and a.code]
    other = Person(full_name="Петров Пётр Петрович", inn=make_inn12("5000000001"))
    xml = build_emchd_xml(
        number="dddddddd-0000-0000-0000-000000000099",
        issue_date=child.issue_date,
        valid_until=child.valid_until,
        principal=child.principal,
        signer=child.signer,
        representatives=[other],
        machine_codes=codes,
        parent_number=child.parent_number,
    )
    scenario.files["poa-sibling.xml"] = xml.encode("utf-8")
    scenario.manifest["documents"].append({"id": "poa-sibling", "kind": "emchd_xml", "path": "poa-sibling.xml"})
    report = verify_case(load_case_inline(scenario.inline_manifest()))
    assert report.verdict.value != "confirmed"
    assert "needs_review" in rule_effects(report, "R-SUB-009")
    assert any("разные передоверия" in finding.message for finding in report.findings)


def test_same_bytes_are_not_sibling_transfers() -> None:
    scenario = build("ok_subdelegation", seed=2)
    scenario.files["poa-copy.xml"] = scenario.files["poa-child.xml"]
    scenario.manifest["documents"].append({"id": "poa-copy", "kind": "emchd_xml", "path": "poa-copy.xml"})
    report = verify_case(load_case_inline(scenario.inline_manifest()))
    assert not any("разные передоверия" in finding.message for finding in report.findings)


def _stand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)


def test_dead_journal_lock_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stand(monkeypatch)
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait(timeout=30)
    path = tmp_path / "journal.jsonl"
    lock = path.with_name(path.name + ".lock")
    lock.write_text(f"{proc.pid}\n", encoding="ascii")
    record = journal.append(path, action="verify", actor="t", role="operator", case_id="c1")
    assert record["seq"] == 1
    assert not lock.exists()


def test_live_journal_lock_is_not_stolen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stand(monkeypatch)
    monkeypatch.setattr(journal, "LOCK_WAIT_SECONDS", 0.05)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    path = tmp_path / "journal.jsonl"
    lock = path.with_name(path.name + ".lock")
    lock.write_text(f"{proc.pid}\n", encoding="ascii")
    try:
        with pytest.raises(ValueError, match="занят"):
            journal.append(path, action="verify", actor="t", role="operator", case_id="c1")
        assert lock.read_text(encoding="ascii").strip() == str(proc.pid)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_unreadable_journal_lock_is_not_stolen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stand(monkeypatch)
    monkeypatch.setattr(journal, "LOCK_WAIT_SECONDS", 0.05)
    path = tmp_path / "journal.jsonl"
    lock = path.with_name(path.name + ".lock")
    lock.write_text("not-a-pid\n", encoding="ascii")
    with pytest.raises(ValueError, match="занят"):
        journal.append(path, action="verify", actor="t", role="operator", case_id="c1")


def test_protected_status_without_now_is_not_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fcntl")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from mandat.audit import attestation
    from mandat.audit.status_list import StatusRegistry, check_status

    now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    key = Ed25519PrivateKey.generate()
    registry = StatusRegistry.open(tmp_path / "status")
    index = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    report = {
        "verdict": "confirmed",
        "profile": "strict",
        "organization": {"inn": "7707083893"},
        "requester": {"inn": "123456789012"},
        "operation": {"code": "payment_sign", "date": now.date().isoformat()},
        "chain": {"poa_numbers": ["poc"]},
        "rule_pack": {"sha256": "a" * 64},
        "reproducibility_hash": "b" * 64,
    }
    cred = attestation.issue(report, key, now=now, status=registry.credential_status(index))
    registry.save(key, now=now)
    monkeypatch.setenv("MANDAT_ENV", "prod")
    assert check_status(cred, tmp_path / "status", key.public_key())["state"] == "свежесть не проверена"
    fresh = StatusRegistry.open(tmp_path / "status")
    assert fresh.mark(index, "revocation", "отозвано")
    fresh.save(key, now=now)
    assert check_status(cred, tmp_path / "status", key.public_key())["state"] == "отозвано"
