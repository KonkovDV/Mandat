"""Владелец кейса привязан к шифру записи хранилища.

Шифрование хранилища привязывало шифр к виду записи и ``case_id``, но не к владельцу. Владелец лежит рядом
с шифром открытым полем, и по нему API решает, кто видит кейс. Тот, кто может писать в хранилище (файл SQLite,
Redis, восстановленная копия), но ключа не знает, переписывал владельца на имя своей учётной записи, и сервис
сам расшифровывал ему чужой отчёт с ФИО, ИНН и СНИЛС. Модель угроз шифрования — именно такой нарушитель. Теперь
владелец входит в привязку шифра: запись под чужим владельцем — отказ, а не данные.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.principals import hash_token
from mandat.api.sealing import SealedStore, Sealer, SealError
from mandat.api.storage import MemoryStore, SQLiteStore
from mandat.eval.synth import build

KEY = "store-key-" + "s" * 32
REPORT = {"verdict": "confirmed", "inn": "500100732259"}


@pytest.fixture(params=["memory", "sqlite", "redis"])
def inner(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    return request.getfixturevalue("redis_store")


def test_report_moved_to_another_owner_is_refused(inner):
    store = SealedStore(inner, Sealer(KEY))
    store.put("c1", REPORT, "anna", 10)
    envelope, _ = inner.get("c1")
    inner.put("c1", envelope, "mallory", 10)  # тот же шифр, другой владелец
    with pytest.raises(SealError):
        store.get("c1")


def test_staged_copy_moved_to_another_owner_is_refused(inner):
    store = SealedStore(inner, Sealer(KEY))
    store.stage("c1", REPORT, "anna", "a-1", None)
    envelope = inner.pending("c1").report
    inner.stage("c1", envelope, "mallory", "a-1", None)
    with pytest.raises(SealError):
        store.pending("c1")


def test_previous_format_without_owner_binding_is_refused_with_a_reason():
    sealer = Sealer(KEY)
    old = {**sealer.seal(REPORT, "report\x00c1"), "sealed": "mandat.sealed.v1"}
    with pytest.raises(SealError, match="v1"):
        sealer.open(old, "report\x00c1")


def test_api_does_not_decrypt_a_re_owned_report_for_another_user(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.setenv("MANDAT_STORE_KEY", KEY)
    users = [
        {"name": "alice", "role": "operator", "token_sha256": hash_token("A" * 40)},
        {"name": "mallory", "role": "operator", "token_sha256": hash_token("M" * 40)},
    ]
    db = tmp_path / "api.db"
    store = api.sealed(SQLiteStore(db))
    client = TestClient(api.create_app(users=json.dumps(users), store=store), raise_server_exceptions=False)
    alice = {"Authorization": "Bearer " + "A" * 40}
    mallory = {"Authorization": "Bearer " + "M" * 40}
    created = client.post("/v1/cases", json=build("ok", seed=21).inline_manifest(), headers=alice)
    assert created.status_code == 200, created.text
    case_id = created.json()["case_id"]
    inn = created.json()["requester"]["inn"]
    assert client.get(f"/v1/cases/{case_id}", headers=mallory).status_code == 404

    with sqlite3.connect(db) as con:
        con.execute("UPDATE reports SET owner = 'mallory' WHERE case_id = ?", (case_id,))

    answer = client.get(f"/v1/cases/{case_id}", headers=mallory)
    assert answer.status_code == 500
    assert "целостности" in answer.json()["detail"]
    assert inn not in answer.text
