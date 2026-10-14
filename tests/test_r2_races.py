"""S-3 и S-4 (аудит R2 14.10.2026): гонки при создании кейса и при чтении файлов кейса."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from mandat.eval import synth
from mandat.intake import case_loader
from mandat.intake.case_loader import MAX_DOC_BYTES, CaseError, _read_regular, load_case_dir

ROOT = Path(__file__).resolve().parents[1]
posix_only = pytest.mark.skipif(os.name != "posix", reason="O_NOFOLLOW и mkfifo есть только в POSIX")

# --- S-4: чтение файлов кейса -----------------------------------------------------------------


@posix_only
def test_symlink_is_not_followed(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("секрет", encoding="utf-8")
    link = tmp_path / "doc.xml"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="не открывается"):
        _read_regular(link, MAX_DOC_BYTES)


@posix_only
def test_symlink_swapped_after_resolve_is_refused(tmp_path, monkeypatch):
    """Путь проверен (внутри кейса), а к моменту открытия документ подменён ссылкой наружу."""
    case = tmp_path / "case"
    shutil.copytree(ROOT / "examples" / "cases" / "01_ok", case)
    outside = tmp_path / "outside.xml"
    outside.write_text("<x/>", encoding="utf-8")
    real_resolve = Path.resolve

    def swapping_resolve(self, strict=False):
        resolved = real_resolve(self, strict)
        if resolved.suffix == ".xml" and resolved.parent == real_resolve(case) and not resolved.is_symlink():
            resolved.unlink()
            resolved.symlink_to(outside)
        return resolved

    monkeypatch.setattr(Path, "resolve", swapping_resolve)
    loaded = load_case_dir(case)
    assert loaded.intake_errors
    assert all("не открывается" in e.error for e in loaded.intake_errors if e.error)


def test_file_growing_after_fstat_is_refused(tmp_path, monkeypatch):
    big = tmp_path / "big.xml"
    big.write_bytes(b"x" * (MAX_DOC_BYTES + 10))
    real_fstat = os.fstat

    def lying_fstat(fd):
        info = real_fstat(fd)
        fields = list(info)
        fields[6] = 10  # st_size: «маленький» на момент проверки
        return os.stat_result(fields)

    monkeypatch.setattr(case_loader.os, "fstat", lying_fstat)
    with pytest.raises(ValueError, match="больше допустимого"):
        _read_regular(big, MAX_DOC_BYTES)


def test_oversized_and_normal_file(tmp_path):
    ok = tmp_path / "ok.xml"
    ok.write_bytes(b"<a/>")
    assert _read_regular(ok, MAX_DOC_BYTES) == b"<a/>"
    big = tmp_path / "big.xml"
    big.write_bytes(b"x" * (MAX_DOC_BYTES + 1))
    with pytest.raises(ValueError, match="больше допустимого"):
        _read_regular(big, MAX_DOC_BYTES)


@posix_only
def test_fifo_does_not_hang(tmp_path):
    fifo = tmp_path / "pipe.xml"
    os.mkfifo(fifo)
    started = time.monotonic()
    with pytest.raises(ValueError, match="не является обычным файлом"):
        _read_regular(fifo, MAX_DOC_BYTES)
    assert time.monotonic() - started < 2


def test_directory_and_missing(tmp_path):
    with pytest.raises(ValueError):
        _read_regular(tmp_path, MAX_DOC_BYTES)
    with pytest.raises(CaseError):
        load_case_dir(tmp_path)


@posix_only
def test_manifest_symlink_refused(tmp_path):
    case = tmp_path / "case"
    shutil.copytree(ROOT / "examples" / "cases" / "01_ok", case)
    (case / "case.json").rename(tmp_path / "real.json")
    (case / "case.json").symlink_to(tmp_path / "real.json")
    with pytest.raises(CaseError, match="case.json"):
        load_case_dir(case)


def test_normal_case_still_loads():
    case = load_case_dir(ROOT / "examples" / "cases" / "01_ok")
    assert not case.intake_errors


# --- S-3: одновременное создание кейса и срок заявки -------------------------------------------


def _client(store):
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    return TestClient(create_app(token="t", rate_per_min=0, store=store))


def test_many_concurrent_creates_one_wins(tmp_path):
    from mandat.api.storage import SQLiteStore

    client = _client(SQLiteStore(tmp_path / "s.db"))
    manifest = synth.build("ok", 3).inline_manifest()
    codes: list[int] = []
    start = threading.Barrier(8, timeout=10)

    def post() -> None:
        start.wait()
        codes.append(client.post("/v1/cases", json=manifest, headers={"Authorization": "Bearer t"}).status_code)

    threads = [threading.Thread(target=post) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert codes.count(200) == 1
    assert set(codes) <= {200, 409}
    assert len(codes) == 8


def test_live_claim_blocks_and_expired_claim_does_not():
    from mandat.api.app import CREATE_CLAIM_SECONDS
    from mandat.api.storage import MemoryStore

    store = MemoryStore()
    client = _client(store)
    manifest = synth.build("ok", 4).inline_manifest()
    case_id = manifest["case_id"]
    headers = {"Authorization": "Bearer t"}
    # Живая заявка другого запроса: 409, кейс не записан.
    assert store.first_seen(f"create:{case_id}", CREATE_CLAIM_SECONDS)
    assert client.post("/v1/cases", json=manifest, headers=headers).status_code == 409
    assert store.get(case_id) is None
    # Заявка упавшего процесса истекла: запрос проходит.
    store.release(f"create:{case_id}")
    stale = time.time() - CREATE_CLAIM_SECONDS - 1
    assert store.first_seen(f"create:{case_id}", CREATE_CLAIM_SECONDS, now=stale)
    assert client.post("/v1/cases", json=manifest, headers=headers).status_code == 200
    # После записи заявка снята: повтор без replace — 409 «уже есть», с replace — 200.
    assert client.post("/v1/cases", json=manifest, headers=headers).status_code == 409
    assert client.post("/v1/cases?replace=true", json=manifest, headers=headers).status_code == 200


# --- заголовки безопасности (R2-API) ----------------------------------------------------------


def test_security_headers_on_success_and_refusals():
    from mandat.api.storage import MemoryStore

    client = _client(MemoryStore())
    for response in (client.get("/health"), client.get("/v1/cases/x")):
        h = response.headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["cache-control"] == "no-store"
        assert h["referrer-policy"] == "no-referrer"
        assert h["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert client.get("/v1/cases/x").status_code == 401


def test_html_card_keeps_own_csp():
    from mandat.api.storage import MemoryStore

    client = _client(MemoryStore())
    manifest = synth.build("ok", 5).inline_manifest()
    headers = {"Authorization": "Bearer t"}
    case_id = client.post("/v1/cases", json=manifest, headers=headers).json()["case_id"]
    response = client.get(f"/v1/cases/{case_id}/report.html", headers=headers)
    assert response.status_code == 200
    csp = response.headers.get_list("content-security-policy")
    assert len(csp) == 1 and "style-src 'unsafe-inline'" in csp[0]


# --- журнал под нагрузкой (R2-PERF-1) ---------------------------------------------------------


def _append(path, n):
    from mandat.audit import journal

    for i in range(n):
        journal.append(path, action="verify", actor="a", role="operator", case_id=f"c{i}")


def test_journal_append_cost_does_not_grow_with_length(tmp_path):
    from mandat.audit import journal

    path = tmp_path / "j.jsonl"
    _append(path, 50)
    t0 = time.perf_counter()
    _append(path, 50)
    early = time.perf_counter() - t0
    _append(path, 1500)
    t0 = time.perf_counter()
    _append(path, 50)
    late = time.perf_counter() - t0
    assert journal.verify_journal(path).ok
    assert late < early * 5 + 0.05  # раньше каждая запись перечитывала и переписывала весь файл


def test_journal_tampering_between_appends_is_still_caught(tmp_path):
    from mandat.audit import journal

    path = tmp_path / "j.jsonl"
    _append(path, 3)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"c1"', '"cX"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(journal.JournalError):
        _append(path, 1)


def test_journal_other_writer_invalidates_cache(tmp_path):
    from mandat.audit import journal

    path = tmp_path / "j.jsonl"
    _append(path, 2)
    journal._TAILS.clear()  # другой процесс: своего кэша нет
    _append(path, 1)
    journal._TAILS.clear()
    _append(path, 1)
    check = journal.verify_journal(path)
    assert check.ok and check.records == 4
