"""Повтор запроса по Idempotency-Key — это чтение отчёта: видимость проверяется, чтение пишется в журнал.

Повтор с тем же ``Idempotency-Key`` в течение суток отдавал отчёт с ФИО, ИНН и СНИЛС без записи ``read_case``
и без проверки, чей это кейс. Журнал чтений обещает, что каждое чтение отчёта записано (152-ФЗ, ст. 19, ч. 2, п. 8),
а повтор был чтением без следа. Строка ключа в хранилище не зашифрована: подменённая, она отдавала по чужому
ключу чужой отчёт. Теперь повтор идёт тем же путём, что и ``GET /v1/cases/{id}``: проверка видимости, запись
в журнал, без журнала — 503.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore, SQLiteStore
from mandat.audit import journal
from mandat.eval.synth import build

TOKEN = "t" * 40
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _actions(path) -> list[str]:
    return [json.loads(line)["action"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_replay_returns_the_report_and_journals_the_read(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    path = tmp_path / "journal.jsonl"
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore(), journal_path=path))
    manifest = build("ok", seed=31).inline_manifest()
    first = client.post("/v1/cases", json=manifest, headers={**HEADERS, "Idempotency-Key": "k-1"})
    assert first.status_code == 200, first.text
    assert _actions(path) == ["verify"]

    again = client.post("/v1/cases", json=manifest, headers={**HEADERS, "Idempotency-Key": "k-1"})
    assert again.status_code == 200
    assert again.json() == first.json()
    assert _actions(path) == ["verify", "read_case"]
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["case_id"] == first.json()["case_id"]
    assert record["payload_hash"] == first.json()["reproducibility_hash"]
    assert journal.verify_journal(path).ok


def test_replay_gives_no_report_when_the_journal_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    path = tmp_path / "journal.jsonl"
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore(), journal_path=path))
    manifest = build("ok", seed=32).inline_manifest()
    first = client.post("/v1/cases", json=manifest, headers={**HEADERS, "Idempotency-Key": "k-2"})
    assert first.status_code == 200, first.text
    real = journal.append

    def failing(target, **kwargs):
        if kwargs.get("action") == "read_case":
            raise OSError("диск журнала недоступен")
        return real(target, **kwargs)

    monkeypatch.setattr(journal, "append", failing)
    again = client.post("/v1/cases", json=manifest, headers={**HEADERS, "Idempotency-Key": "k-2"})
    assert again.status_code == 503
    assert first.json()["requester"]["inn"] not in again.text


def test_replay_key_pointing_to_another_users_case_gives_no_report(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    users = [
        {"name": "alice", "role": "operator", "token_sha256": hash_token("A" * 40)},
        {"name": "mallory", "role": "operator", "token_sha256": hash_token("M" * 40)},
    ]
    db = tmp_path / "api.db"
    path = tmp_path / "journal.jsonl"
    client = TestClient(api.create_app(users=json.dumps(users), store=SQLiteStore(db), journal_path=path))
    alice = {"Authorization": "Bearer " + "A" * 40}
    mallory = {"Authorization": "Bearer " + "M" * 40}
    created = client.post("/v1/cases", json=build("ok", seed=33).inline_manifest(), headers=alice)
    assert created.status_code == 200, created.text
    case_id = created.json()["case_id"]

    # Тот, кто пишет в файл хранилища, ставит под свой ключ чужой кейс и отпечаток своего запроса.
    own = build("ok", seed=34).inline_manifest()
    canonical = json.dumps({"manifest": own, "profile": None, "replace": False}, sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT OR REPLACE INTO idem (owner, key, case_id, fp, at) VALUES ('mallory', 'k-9', ?, ?, ?)",
            (case_id, fingerprint, time.time()),
        )

    before = len(_actions(path))
    answer = client.post("/v1/cases", json=own, headers={**mallory, "Idempotency-Key": "k-9"})
    assert answer.status_code == 404
    assert created.json()["requester"]["inn"] not in answer.text
    assert len(_actions(path)) == before
