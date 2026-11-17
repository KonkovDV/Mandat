"""M-03: /readyz (журнал + хранилище + ключи) и пилотный compose-оверлей."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.storage import MemoryStore, SQLiteStore

KEY = "k" * 40


def _pilot(monkeypatch, *, journal_key=KEY, pii_key=KEY):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    for name, value in (("MANDAT_JOURNAL_KEY", journal_key), ("MANDAT_PII_KEY", pii_key)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.delenv("MANDAT_API_JOURNAL", raising=False)


def test_readyz_is_open_but_requires_nothing_secret(monkeypatch, tmp_path):
    _pilot(monkeypatch)
    app = create_app(token="t", store=SQLiteStore(tmp_path / "s.db"), journal_path=tmp_path / "j.jsonl")
    response = TestClient(app).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"store": "ok", "journal": "ok", "keys": "ok"}}


def test_readyz_503_without_keys(monkeypatch, tmp_path):
    _pilot(monkeypatch, journal_key=None)
    app = create_app(token="t", store=SQLiteStore(tmp_path / "s.db"), journal_path=tmp_path / "j.jsonl")
    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["keys"] == "fail"


def test_readyz_503_in_pilot_without_journal_or_with_memory_store(monkeypatch, tmp_path):
    _pilot(monkeypatch)
    no_journal = TestClient(create_app(token="t", store=SQLiteStore(tmp_path / "s.db")))
    assert no_journal.get("/readyz").json()["checks"]["journal"] == "fail"
    assert no_journal.get("/readyz").status_code == 503
    memory = TestClient(create_app(token="t", store=MemoryStore(), journal_path=tmp_path / "j.jsonl"))
    assert memory.get("/readyz").json()["checks"]["store"] == "fail"
    assert memory.get("/readyz").status_code == 503


def test_readyz_503_when_store_broken_and_no_leak(monkeypatch, tmp_path):
    _pilot(monkeypatch)

    class Broken(MemoryStore):
        name = "sqlite"

        def ping(self):
            raise RuntimeError("secret-path /var/x")

    app = create_app(token="t", store=Broken(), journal_path=tmp_path / "j.jsonl")
    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    assert "secret-path" not in response.text
    assert response.json()["checks"]["store"] == "fail"


def test_readyz_503_when_journal_dir_unwritable(monkeypatch, tmp_path):
    _pilot(monkeypatch)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    app = create_app(token="t", store=SQLiteStore(tmp_path / "s.db"), journal_path=blocker / "j.jsonl")
    assert TestClient(app).get("/readyz").status_code == 503


def test_readyz_demo_mode_without_journal_is_ready(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.delenv("MANDAT_API_JOURNAL", raising=False)
    response = TestClient(create_app(token="t", store=MemoryStore())).get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"]["journal"] == "not_configured"


def test_health_stays_liveness_only(monkeypatch):
    _pilot(monkeypatch, journal_key=None)
    assert TestClient(create_app(token="t", store=MemoryStore())).get("/health").status_code == 200


def test_sqlite_store_ping(tmp_path):
    SQLiteStore(tmp_path / "s.db").ping()


def test_pilot_compose_overlay(root):
    text = (root / "deploy" / "compose.pilot.yml").read_text(encoding="utf-8")
    assert "MANDAT_ENV: pilot" in text
    assert "MANDAT_API_STORE: sqlite:////app/var/store.db" in text
    assert "MANDAT_API_JOURNAL: /app/var/journal.jsonl" in text
    assert "mandat-var:/app/var" in text
    assert "/readyz" in text
    # /app/var не tmpfs: в списке tmpfs оверлея только /tmp
    tmpfs = re.search(r"tmpfs:.*?\n((?:\s+-.*\n)+)", text).group(1)
    assert "/app/var" not in tmpfs
    # оверлей не подменяет секреты значениями
    assert not re.search(r"MANDAT_(JOURNAL|PII)_KEY:", text)


def test_ci_runs_pilot_compose(root):
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "compose.pilot.yml" in ci and "--build --wait" in ci
    assert (root / "scripts" / "pilot_smoke.py").exists()


def test_openapi_has_readyz_unauthenticated():
    spec = create_app(token="t", docs=True).openapi()
    assert "/readyz" in spec["paths"]
