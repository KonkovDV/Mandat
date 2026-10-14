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


class _IOCounter:
    """Байты, прочитанные и записанные журналом в каталоге ``root``: детерминированная мера стоимости записи.

    Считаем ``Path.open``/``read_bytes``/``read_text`` и ``os.write``. ``read_bytes``/``read_text`` читают
    через ``io.open`` напрямую, чтобы не учитывать байты дважды при любой версии ``pathlib``.
    """

    def __init__(self, monkeypatch, root):
        import io
        import os
        import pathlib

        self.read = 0
        self.written = 0
        self.full_scans = 0
        root = str(root)
        counter = self
        mine = lambda target: str(target).startswith(root)  # noqa: E731

        class Handle:
            def __init__(self, raw):
                self._raw = raw

            def read(self, *args):
                data = self._raw.read(*args)
                counter.read += len(data)
                return data

            def __getattr__(self, name):
                return getattr(self._raw, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._raw.close()

            def __iter__(self):
                for line in self._raw:
                    counter.read += len(line)
                    yield line

        def opener(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
            raw = io.open(self, mode, buffering, encoding, errors, newline)  # noqa: UP020
            return Handle(raw) if mine(self) and "r" in mode else raw

        def read_bytes(self):
            with io.open(self, "rb") as handle:  # noqa: UP020
                data = handle.read()
            if mine(self):
                counter.read += len(data)
            return data

        def read_text(self, encoding=None, errors=None, newline=None):
            with io.open(self, "rb") as handle:  # noqa: UP020
                data = handle.read()
            if mine(self):
                counter.read += len(data)
            return data.decode(encoding or "utf-8", errors or "strict")

        real_write = os.write

        def write(fd, data):
            counter.written += len(data)
            return real_write(fd, data)

        from mandat.audit import journal

        real_read, real_verify = journal.read_journal, journal.verify_journal

        def read_journal(*args, **kwargs):
            counter.full_scans += 1
            return real_read(*args, **kwargs)

        def verify_journal(*args, **kwargs):
            counter.full_scans += 1
            return real_verify(*args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "open", opener)
        monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
        monkeypatch.setattr(pathlib.Path, "read_text", read_text)
        monkeypatch.setattr(os, "write", write)
        monkeypatch.setattr(journal, "read_journal", read_journal)
        monkeypatch.setattr(journal, "verify_journal", verify_journal)

    def snapshot(self):
        return self.read + self.written, self.full_scans


def test_journal_append_cost_does_not_grow_with_length(tmp_path, monkeypatch):
    """Стоимость записи не зависит от длины журнала (R2-PERF-1), без часов.

    Раньше каждая запись перечитывала и переписывала весь файл. Меряем байты ввода-вывода по файлам
    журнала на одинаковой позиции внутри сегмента — в начале журнала и после 15 закрытых сегментов —
    и число полных разборов истории. При старом поведении поздняя запись дороже ранней в десятки раз.
    """
    from mandat.audit import journal

    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "100")
    path = tmp_path / "j.jsonl"
    _append(path, 50)
    io_counter = _IOCounter(monkeypatch, tmp_path)

    def cost(n):
        bytes0, scans0 = io_counter.snapshot()
        _append(path, n)
        bytes1, scans1 = io_counter.snapshot()
        return bytes1 - bytes0, scans1 - scans0

    early, early_scans = cost(50)  # записи 51–100, включая закрытие первого сегмента
    _append(path, 1500)
    late, late_scans = cost(50)  # записи 1551–1600: та же позиция в сегменте
    assert early_scans == 0 and late_scans == 0  # свой проверенный хвост: без полного разбора истории
    assert late <= early * 1.5 + 8192, (early, late)
    monkeypatch.undo()
    assert journal.verify_journal(path).ok


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


# --- R2-PERF-2: сегменты журнала ---------------------------------------------------------------


def _seg_append(path, n, **kw):
    from mandat.audit import journal as j

    for i in range(n):
        j.append(path, action="event", actor="t", role="operator", case_id=f"c{i}", **kw)


def test_journal_rotates_into_sealed_segments(tmp_path, monkeypatch):
    from mandat.audit import journal as j

    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "5")
    path = tmp_path / "j.jsonl"
    _seg_append(path, 12)
    sealed = j.sealed_segments(path)
    assert [s.name for s in sealed] == ["j.jsonl.000001", "j.jsonl.000002"]
    assert len(path.read_text().splitlines()) == 2
    records = j.read_journal(path)
    assert [r["seq"] for r in records] == list(range(1, 13))
    assert j.verify_journal(path).ok
    assert records[5]["prev"] == records[4]["hash"]  # цепочка через границу сегмента


def test_journal_segment_tamper_and_removal_detected(tmp_path, monkeypatch):
    from mandat.audit import journal as j

    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "3")
    path = tmp_path / "j.jsonl"
    _seg_append(path, 8)
    first, second = j.sealed_segments(path)
    assert second.stat().st_mode & 0o777 == 0o400
    first.chmod(0o600)
    second.chmod(0o600)
    original = second.read_text()
    second.write_text(original.replace('"c4"', '"cX"'))  # та же длина
    assert not j.verify_journal(path).ok
    second.write_text(original.replace('"c4"', '"cXY"'))  # другой размер: растяжка stat срабатывает
    with pytest.raises(j.JournalError):
        _seg_append(path, 1)
    second.write_text(original)
    j._TAILS.clear()
    assert j.verify_journal(path).ok
    second.unlink()
    check = j.verify_journal(path)
    assert not check.ok and check.broken_at == 4
    first.rename(tmp_path / "keep")
    second.write_text(original)
    assert not j.verify_journal(path).ok


def test_journal_last_segment_removal_caught_by_witness(tmp_path, monkeypatch):
    from mandat.audit import journal as j

    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "3")
    path = tmp_path / "j.jsonl"
    _seg_append(path, 6)
    assert not path.exists() or path.read_text() == ""
    j.sealed_segments(path)[-1].unlink()
    assert not j.verify_journal(path).ok


def test_journal_append_cost_bounded_by_segment(tmp_path, monkeypatch):
    from mandat.audit import journal as j

    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "50")
    path = tmp_path / "j.jsonl"
    sizes = []
    real = j._content_digest

    def spy(p):
        sizes.append(p.stat().st_size if p.exists() else 0)
        return real(p)

    monkeypatch.setattr(j, "_content_digest", spy)
    _seg_append(path, 400)
    # хешируется только активный сегмент: объём не растёт с длиной журнала
    assert max(sizes[-50:]) <= max(sizes[50:100]) * 1.2
    assert j.verify_journal(path).records == 400


def test_segment_records_env(monkeypatch):
    from mandat.audit import journal as j

    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "x")
    assert j.segment_records() == j.DEFAULT_SEGMENT_RECORDS
    monkeypatch.setenv("MANDAT_JOURNAL_SEGMENT_RECORDS", "0")
    assert j.segment_records() == 1
    monkeypatch.delenv("MANDAT_JOURNAL_SEGMENT_RECORDS")
    assert j.segment_records() == 1000
