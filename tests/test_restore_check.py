"""Восстановление из копии проверяется, а не предполагается (воркшоп 20.11.2026, NIST SP 800-53 CP-9/CP-10).

Сценарий: сняли копию хранилища и списков статусов, затем удалили кейс по основанию 152-ФЗ и отозвали
удостоверение, затем восстановились из копии. Журнал ведётся дальше. ``restore-check`` должен найти воскресший
отчёт с историей разбора и откат отзыва, а с ``--repair`` — повторить удаление и отзыв и записать это в журнал.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.storage import SQLiteStore
from mandat.audit import journal
from mandat.audit.status_list import StatusRegistry, get_bit
from mandat.cli import main
from mandat.eval.synth import build
from mandat.services.recovery import check_restore

TOKEN = "t" * 40
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _sqlite_backup(src: Path, dst: Path) -> None:
    """Согласованная копия SQLite через backup API: копия файла без -wal могла бы потерять данные."""
    with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
        source.backup(target)


def _restore(backup: Path, live: Path) -> None:
    """Восстановление тем же backup API в обратную сторону: файл базы целиком заменяется содержимым копии."""
    _sqlite_backup(backup, live)


class Bank:
    """Стенд: API с хранилищем SQLite и журналом, списки статусов и ключ их подписи."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store_path = root / "live" / "store.db"
        self.store_path.parent.mkdir(parents=True)
        self.journal = root / "journal" / "audit.jsonl"
        self.status_dir = root / "live" / "status"
        self.key = Ed25519PrivateKey.generate()
        key_path = root / "live" / "keys" / "attestation_ed25519.pem"
        key_path.parent.mkdir(parents=True)
        pem = self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)
        self.client = TestClient(create_app(token=TOKEN, store=SQLiteStore(self.store_path), journal_path=self.journal))

    def create(self, case_id: str, seed: int) -> dict:
        manifest = {**build("ok", seed).inline_manifest(), "case_id": case_id}
        answer = self.client.post("/v1/cases", json=manifest, headers=HEADERS)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def erase(self, case_id: str) -> None:
        answer = self.client.post(f"/v1/cases/{case_id}/erase", json={"reason": "purpose_achieved"}, headers=HEADERS)
        assert answer.status_code == 200, answer.text

    def store(self) -> SQLiteStore:
        return SQLiteStore(self.store_path)


@pytest.fixture
def bank(tmp_path) -> Bank:
    return Bank(tmp_path)


def _after_backup_changes(bank: Bank) -> tuple[Path, Path, int]:
    """Кейсы A и B, копия, потом удаление A и отзыв удостоверения B. Возвращает пути копий и номер в списке."""
    bank.create("case-a", 1)
    bank.create("case-b", 2)
    registry = StatusRegistry.open(bank.status_dir)
    index = registry.allocate({"org_inn": "7700000016", "verdict": "confirmed", "case_id": "case-b"})
    registry.save(bank.key)
    store_copy, status_copy = bank.root / "backup" / "store.db", bank.root / "backup" / "status"
    store_copy.parent.mkdir()
    _sqlite_backup(bank.store_path, store_copy)
    shutil.copytree(bank.status_dir, status_copy)
    bank.erase("case-a")
    registry = StatusRegistry.open(bank.status_dir)
    registry.mark(index, "revocation", "POA_REVOKED")
    registry.save(bank.key)
    journal.append(
        bank.journal,
        action="status_change",
        actor="monitor",
        role="security",
        case_id="case-b",
        payload_hash=str(index),
        reason="POA_REVOKED",
        details={"status": "revocation"},
    )
    return store_copy, status_copy, index


def _restore_all(bank: Bank, store_copy: Path, status_copy: Path) -> None:
    _restore(store_copy, bank.store_path)
    shutil.rmtree(bank.status_dir)
    shutil.copytree(status_copy, bank.status_dir)


def test_restore_finds_erased_case_its_review_and_rolled_back_revocation(bank):
    store_copy, status_copy, index = _after_backup_changes(bank)
    assert bank.store().get("case-a") is None
    _restore_all(bank, store_copy, status_copy)
    assert bank.store().get("case-a") is not None, "копия снята до удаления: отчёт вернулся"

    report = check_restore(bank.journal, bank.store(), status_dir=bank.status_dir)
    kinds = sorted((f.kind, f.case_id) for f in report.findings)
    assert kinds == [("erased_resurrected", "case-a"), ("review_resurrected", "case-a"), ("status_rollback", "case-b")]
    assert report.journal_ok and not report.ok
    assert report.erased_in_journal == 1 and report.lost_after_backup == 0
    assert bank.store().get("case-a") is not None, "без --repair проверка ничего не меняет"


def test_repair_erases_again_restores_the_revocation_and_writes_the_journal(bank):
    store_copy, status_copy, index = _after_backup_changes(bank)
    _restore_all(bank, store_copy, status_copy)
    from mandat.audit.signing import as_signer

    signer = as_signer(bank.key)
    report = check_restore(bank.journal, bank.store(), status_dir=bank.status_dir, repair=True, signer=signer)
    assert report.ok and all(f.repaired for f in report.findings)
    store = bank.store()
    assert store.get("case-a") is None
    review = store.review_get("case-a")
    assert review is not None and review[0]["history"] == [] and review[0]["erased"]
    assert get_bit(StatusRegistry.open(bank.status_dir).lists["revocation"], index)
    records = journal.read_journal(bank.journal)
    reapplied = [r for r in records if (r.get("details") or {}).get("reapplied_after_restore")]
    assert [r["case_id"] for r in reapplied] == ["case-a"] and reapplied[0]["action"] == "erase_case"
    assert journal.verify_journal(bank.journal).ok
    again = check_restore(bank.journal, bank.store(), status_dir=bank.status_dir)
    assert again.ok and again.findings == []


def test_case_created_again_after_erasure_is_kept(bank):
    bank.create("case-a", 1)
    bank.erase("case-a")
    bank.create("case-a", 3)
    report = check_restore(bank.journal, bank.store(), repair=True)
    assert report.ok and report.findings == []
    assert bank.store().get("case-a") is not None


def test_reports_created_after_the_backup_are_counted_as_lost_not_failed(bank):
    bank.create("case-a", 1)
    store_copy = bank.root / "copy.db"
    _sqlite_backup(bank.store_path, store_copy)
    bank.create("case-late", 2)
    _restore(store_copy, bank.store_path)
    report = check_restore(bank.journal, bank.store())
    assert report.ok and report.lost_after_backup == 1


def test_broken_journal_stops_the_check_and_repairs_nothing(bank):
    store_copy, status_copy, _ = _after_backup_changes(bank)
    _restore_all(bank, store_copy, status_copy)
    lines = bank.journal.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["actor"] = "someone-else"
    bank.journal.write_text("\n".join([json.dumps(record, ensure_ascii=False), *lines[1:]]) + "\n", encoding="utf-8")
    report = check_restore(bank.journal, bank.store(), repair=True)
    assert not report.journal_ok and not report.ok and report.findings == []
    assert bank.store().get("case-a") is not None, "с повреждённым журналом сверять не с чем: ничего не удаляется"


def test_cli_restore_check_exit_codes(bank, capsys):
    store_copy, status_copy, _ = _after_backup_changes(bank)
    _restore_all(bank, store_copy, status_copy)
    args = ["restore-check", "--journal", str(bank.journal), "--store", f"sqlite:///{bank.store_path}"]
    status_args = ["--status-dir", str(bank.status_dir)]
    assert main([*args, *status_args]) == 1
    capsys.readouterr()
    assert main([*args, *status_args, "--repair"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and {f["kind"] for f in result["findings"]} >= {"erased_resurrected", "status_rollback"}
    assert main([*args, *status_args]) == 0
    assert main(["restore-check", "--journal", str(bank.journal), "--store", "memory"]) == 2
