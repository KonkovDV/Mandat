"""Журнал, разбор и API: одна граница изменения, проверка истории перед записью, версии решений."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.storage import MemoryStore
from mandat.audit import journal
from mandat.eval import synth
from mandat.review.state_machine import ReviewCase, ReviewError, ReviewState

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.delenv("MANDAT_API_JOURNAL", raising=False)


def _log(tmp_path: Path, n: int = 2) -> Path:
    path = tmp_path / "journal.jsonl"
    for i in range(n):
        journal.append(path, action="verify", actor="a", role="operator", case_id=f"c{i}", payload_hash="h" * 64)
    return path


def test_append_refuses_corrupted_prefix(tmp_path):
    path = _log(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["actor"] = "mallory"
    lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(journal.JournalError):
        journal.append(path, action="verify", actor="a", role="operator", case_id="c9")
    assert path.read_bytes() == before


def test_append_refuses_truncated_tail(tmp_path):
    path = _log(tmp_path, 3)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    with pytest.raises(journal.JournalError):
        journal.append(path, action="verify", actor="a", role="operator", case_id="c9")


def test_concurrent_appends_keep_the_chain(tmp_path):
    path = tmp_path / "journal.jsonl"
    errors: list[Exception] = []

    def write(i: int) -> None:
        try:
            journal.append(path, action="verify", actor="a", role="operator", case_id=f"c{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = journal.verify_journal(path)
    assert check.ok and check.records == 16


def _in_review() -> ReviewCase:
    return ReviewCase("c1", "h" * 64, state=ReviewState.IN_REVIEW, version=3)


def test_accept_and_reject_of_one_version_conflict(tmp_path):
    path = tmp_path / "journal.jsonl"
    _in_review().apply("accept", actor="anna", role="lawyer", reason="ок", journal_path=path, expected_version=3)
    with pytest.raises(ReviewError, match="конфликт"):
        _in_review().apply("reject", actor="boris", role="lawyer", reason="нет", journal_path=path, expected_version=3)
    assert journal.verify_journal(path).records == 1


def test_same_decision_delivered_twice_is_idempotent(tmp_path):
    path = tmp_path / "journal.jsonl"
    for _ in range(2):
        case = _in_review()
        case.apply("accept", actor="anna", role="lawyer", reason="ок", journal_path=path, expected_version=3)
        assert case.state is ReviewState.ACCEPTED
    assert journal.verify_journal(path).records == 1


def test_protected_mode_requires_expected_version(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "k" * 32)
    with pytest.raises(ReviewError, match="версия"):
        _in_review().apply("accept", actor="anna", role="lawyer", reason="ок", journal_path=tmp_path / "j.jsonl")
    with pytest.raises(ReviewError, match="журнал"):
        _in_review().apply("accept", actor="anna", role="lawyer", reason="ок", expected_version=3)


# --------------------------------------------------------------------------- HTTP


def _client(store=None, journal_path=None) -> TestClient:
    return TestClient(create_app("t", rate_per_min=0, store=store or MemoryStore(), journal_path=journal_path))


def _manifest() -> dict:
    return synth.build("ok", 1).inline_manifest()


def test_api_case_is_journaled(tmp_path):
    path = tmp_path / "api.jsonl"
    r = _client(journal_path=path).post("/v1/cases", json=_manifest(), headers=AUTH)
    assert r.status_code == 200
    records = journal.read_journal(path)
    assert [rec["action"] for rec in records] == ["verify"]
    assert records[0]["payload_hash"] == r.json()["reproducibility_hash"]
    assert records[0]["case_id"] == r.json()["case_id"]
    assert journal.verify_journal(path).ok


def test_api_journal_failure_stores_nothing(tmp_path):
    path = tmp_path / "api.jsonl"
    _log(tmp_path)  # журнал уже есть
    path.write_text("испорчено\n", encoding="utf-8")
    store = MemoryStore()
    r = _client(store, path).post("/v1/cases", json=_manifest(), headers=AUTH)
    assert r.status_code == 503
    assert store.get(_manifest()["case_id"]) is None


def test_api_store_failure_after_journal_leaves_audit_trail(tmp_path):
    class Broken(MemoryStore):
        def commit(self, *a, **k):
            raise OSError("диск заполнен")

    path = tmp_path / "api.jsonl"
    client = TestClient(
        create_app("t", rate_per_min=0, store=Broken(), journal_path=path), raise_server_exceptions=False
    )
    r = client.post("/v1/cases", json=_manifest(), headers=AUTH)
    assert r.status_code >= 500
    assert [rec["action"] for rec in journal.read_journal(path)] == ["verify"]


def test_api_protected_mode_without_journal_refuses(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "k" * 32)
    r = _client().post("/v1/cases", json=_manifest(), headers=AUTH)
    assert r.status_code == 503


def test_two_concurrent_decisions_on_one_version_write_one_record(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    journal.append(path, action="verify", actor="a", role="operator", case_id="c1", payload_hash="h" * 64)
    slow_verify = journal.verify_journal

    def verify_slowly(*args, **kwargs):
        # Расширяет окно между проверкой и записью: без общей блокировки оба потока его проходят.
        result = slow_verify(*args, **kwargs)
        threading.Event().wait(0.05)
        return result

    monkeypatch.setattr(journal, "verify_journal", verify_slowly)
    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def decide(action: str, actor: str, reason: str) -> None:
        case = _in_review()
        barrier.wait()
        try:
            outcomes[actor] = case.apply(
                action, actor=actor, role="lawyer", reason=reason, journal_path=path, expected_version=3
            )
        except ReviewError as exc:
            outcomes[actor] = exc

    threads = [
        threading.Thread(target=decide, args=("accept", "anna", "ок")),
        threading.Thread(target=decide, args=("reject", "boris", "подпись не та")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    decisions = [r for r in journal.read_journal(path) if r["action"] != "verify"]
    assert len(decisions) == 1
    assert sum(isinstance(v, ReviewError) for v in outcomes.values()) == 1
    assert journal.verify_journal(path).ok
