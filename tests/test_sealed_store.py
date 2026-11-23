"""Отчёты и разборы лежат в хранилище зашифрованными, а подмена записи обнаруживается.

Хранилище копируют: резервные копии, снимки Redis, реплики, файл SQLite. Каждая копия — та же база ПДн.
Без шифра ФИО, ИНН и СНИЛС из отчёта читались в любой копии. Вердикт в базе можно было тихо поменять
или переставить отчёт одного кейса под другой.
"""

from __future__ import annotations

import base64
import contextlib
import json
import sqlite3

import pytest
from conftest import random_key
from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.sealing import SealedStore, Sealer, SealError, StoreKeyMissing, load_sealer, sealed
from mandat.api.storage import Idem, MemoryStore, SQLiteStore
from mandat.eval.synth import build
from mandat.services.env_policy import key_problem

KEY = "store-key-" + "s" * 32
OTHER = "other-key-" + "o" * 32
MARKER = "Иванова-Петрова 112-233-445 95"
REPORT = {"verdict": "confirmed", "person": MARKER, "inn": "500100732259"}


@pytest.fixture(params=["memory", "sqlite", "redis"])
def inner(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    return request.getfixturevalue("redis_store")


def test_reports_reviews_and_staged_copies_round_trip_and_stay_sealed(inner):
    store = SealedStore(inner, Sealer(KEY))
    store.put("c1", REPORT, "anna", 10)
    assert store.get("c1") == (REPORT, "anna")
    store.stage("c2", REPORT, "anna", "a-1", Idem("anna", "k-1", "fp"))
    assert store.pending("c2").report == REPORT
    assert [p.report for p in store.pending_all()] == [REPORT]
    assert store.pending_for_idem("anna", "k-1").report == REPORT
    assert store.commit("c2", "a-1", 10)
    assert store.get("c2") == (REPORT, "anna")
    assert store.review_put("c1", {"state": "queued", "note": MARKER}, 0) == 1
    assert store.review_get("c1") == ({"state": "queued", "note": MARKER}, 1)
    assert store.review_list() == [("c1", {"state": "queued", "note": MARKER}, 1)]
    raw = json.dumps([inner.get("c1"), inner.get("c2"), inner.review_get("c1")], ensure_ascii=False)
    assert MARKER not in raw and "500100732259" not in raw and "confirmed" not in raw


def test_sqlite_file_holds_no_readable_report(tmp_path):
    store = SealedStore(SQLiteStore(tmp_path / "api.db"), Sealer(KEY))
    store.put("c1", REPORT, "anna", 10)
    store.review_put("c1", {"note": MARKER}, 0)
    on_disk = b"".join(f.read_bytes() for f in tmp_path.iterdir() if f.is_file())
    assert MARKER.encode() not in on_disk and b"500100732259" not in on_disk


def test_changed_byte_moved_record_and_plain_text_are_refused():
    inner = MemoryStore()
    store = SealedStore(inner, Sealer(KEY))
    store.put("c1", REPORT, "anna", 10)
    store.put("c2", {**REPORT, "verdict": "refuted"}, "anna", 10)
    envelope, owner = inner.get("c1")
    data = bytearray(envelope["data"].encode())
    data[5] = ord("A") if data[5] != ord("A") else ord("B")
    inner.put("c1", {**envelope, "data": data.decode()}, owner, 10)
    with pytest.raises(SealError, match="изменена"):
        store.get("c1")
    # Отчёт кейса c2 под номером c1: шифр цел, но привязан к другому кейсу.
    inner.put("c1", inner.get("c2")[0], owner, 10)
    with pytest.raises(SealError, match="другому кейсу"):
        store.get("c1")
    inner.put("c1", {**REPORT, "verdict": "confirmed"}, owner, 10)
    with pytest.raises(SealError, match="не зашифрована"):
        store.get("c1")


def test_review_cannot_be_rolled_back_under_a_newer_version():
    inner = MemoryStore()
    store = SealedStore(inner, Sealer(KEY))
    store.review_put("c1", {"state": "queued"}, 0)
    old_body, _ = inner.review_get("c1")
    store.review_put("c1", {"state": "rejected"}, 1)
    # Прежнее тело под текущим номером версии 2.
    inner._reviews["c1"] = (old_body, 2)
    with pytest.raises(SealError):
        store.review_get("c1")


def test_key_rotation_reads_old_records_and_writes_with_the_new_key():
    inner = MemoryStore()
    SealedStore(inner, Sealer(OTHER)).put("c1", REPORT, "anna", 10)
    rotated = SealedStore(inner, Sealer(KEY, [OTHER]))
    assert rotated.get("c1") == (REPORT, "anna")
    rotated.put("c1", REPORT, "anna", 10)
    assert inner.get("c1")[0]["kid"] == Sealer(KEY).kid
    # Прежний ключ убрали из связки: старые копии больше не читаются.
    SealedStore(inner, Sealer(OTHER)).put("c2", REPORT, "anna", 10)
    with pytest.raises(SealError, match="неизвестным ключом"):
        SealedStore(inner, Sealer(KEY)).get("c2")


def test_key_policy(monkeypatch):
    for name in ("MANDAT_STORE_KEY", "MANDAT_STORE_KEY_PREVIOUS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert load_sealer() is None
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", random_key("journal"))
    monkeypatch.setenv("MANDAT_PII_KEY", random_key("pii"))
    with pytest.raises(StoreKeyMissing, match="MANDAT_STORE_KEY"):
        load_sealer()
    assert "MANDAT_STORE_KEY" in (key_problem() or "")
    monkeypatch.setenv("MANDAT_STORE_KEY", "short")
    with pytest.raises(StoreKeyMissing, match="короче"):
        load_sealer()
    monkeypatch.setenv("MANDAT_STORE_KEY", random_key("journal"))
    with pytest.raises(StoreKeyMissing, match="MANDAT_JOURNAL_KEY"):
        load_sealer()
    # HKDF не растягивает пароль: в pilot ключ хранилища — только случайный.
    monkeypatch.setenv("MANDAT_STORE_KEY", KEY)
    with pytest.raises(StoreKeyMissing, match="случайный ключ"):
        load_sealer()
    monkeypatch.setenv("MANDAT_STORE_KEY", random_key("store"))
    monkeypatch.setenv("MANDAT_STORE_KEY_PREVIOUS", "short")
    with pytest.raises(StoreKeyMissing, match="PREVIOUS"):
        load_sealer()
    # Прежний ключ может быть паролем: им только читают, чтобы уйти с пароля на случайный ключ.
    monkeypatch.setenv("MANDAT_STORE_KEY_PREVIOUS", OTHER)
    assert isinstance(load_sealer(), Sealer)
    assert key_problem() is None


def test_app_without_a_store_key_does_not_start_in_pilot(monkeypatch, tmp_path):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.delenv("MANDAT_STORE_KEY", raising=False)
    monkeypatch.setenv("MANDAT_API_STORE", f"sqlite:///{tmp_path / 'api.db'}")
    with pytest.raises(StoreKeyMissing):
        api.create_app(token="t" * 40)


def test_api_keeps_reports_sealed_and_refuses_a_tampered_one(monkeypatch, tmp_path):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.setenv("MANDAT_STORE_KEY", KEY)
    db = tmp_path / "api.db"
    monkeypatch.setenv("MANDAT_API_STORE", f"sqlite:///{db}")
    headers = {"Authorization": "Bearer " + "t" * 40}
    client = TestClient(api.create_app(token="t" * 40, rate_per_min=0), raise_server_exceptions=False)
    created = client.post("/v1/cases", json=build("ok", seed=31).inline_manifest(), headers=headers)
    assert created.status_code == 200, created.text
    report = created.json()
    inn = report["requester"]["inn"]
    assert client.get(f"/v1/cases/{report['case_id']}", headers=headers).json() == report
    on_disk = b"".join(f.read_bytes() for f in tmp_path.iterdir() if f.is_file())
    assert inn.encode() not in on_disk
    with contextlib.closing(sqlite3.connect(db)) as con, con:
        con.execute("UPDATE reports SET body = ? WHERE case_id = ?", (json.dumps(report), report["case_id"]))
    answer = client.get(f"/v1/cases/{report['case_id']}", headers=headers)
    assert answer.status_code == 500
    assert answer.json()["detail"] == "запись хранилища не прошла проверку целостности"
    assert inn not in answer.text


def test_records_without_personal_data_pass_through_unchanged(monkeypatch):
    inner = MemoryStore()
    store = SealedStore(inner, Sealer(KEY))
    store.ping()
    assert store.name == "memory"
    assert store.allow("client", 1, now=100.0) and not store.allow("client", 1, now=100.5)
    assert store.first_seen("k", 60, now=1.0) and not store.first_seen("k", 60, now=2.0)
    assert store.claim("n", "v", 60, now=1.0) is None and store.claim("n", "w", 60, now=2.0) == "v"
    store.release("n")
    assert store.claim("n", "w", 60, now=3.0) is None
    assert store.watermark_raise("list", 5) == 5 and store.watermark_get("list") == 5
    store.stage("c1", REPORT, "anna", "a-1", Idem("anna", "k-1", "fp"))
    assert store.discard("c1", "a-1") and store.pending("c1") is None
    store.stage("c1", REPORT, "anna", "a-2", Idem("anna", "k-1", "fp"))
    assert store.commit("c1", "a-2", 10)
    assert store.idem_get("anna", "k-1") == ("c1", "fp")
    assert store.erase("c1") and store.get("c1") is None and store.review_get("c1") is None
    # Уже обёрнутое хранилище второй раз не оборачивается; без ключа на стенде — как есть.
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.setenv("MANDAT_STORE_KEY", KEY)
    assert sealed(store) is store
    monkeypatch.delenv("MANDAT_STORE_KEY")
    assert sealed(inner) is inner


def test_sealed_value_that_is_not_an_object_is_refused():
    sealer = Sealer(KEY)
    envelope = sealer.seal({"x": 1}, "report\x00c1")
    cipher = sealer._keys[sealer.kid]
    nonce = b"\x00" * 12
    data = cipher.encrypt(nonce, b"[1, 2]", b"report\x00c1")
    forged = {**envelope, "nonce": base64.b64encode(nonce).decode(), "data": base64.b64encode(data).decode()}
    with pytest.raises(SealError, match="не объект"):
        sealer.open(forged, "report\x00c1")
