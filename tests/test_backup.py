"""``mandat backup``: копия каталога данных без остановки службы — журнал под его блокировкой, SQLite своим
резервным копированием, остальное по файлу атомарно; ротация оставляет последние копии."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mandat import cli
from mandat.api.storage import SQLiteStore
from mandat.audit import journal
from mandat.services import backup as backups

MOMENT = datetime(2026, 11, 28, 0, 15, tzinfo=UTC)  # 03:15 по Москве


def _append(path: Path, n: int) -> None:
    for i in range(n):
        journal.append(path, action="verify", actor="stand", role="operator", case_id=f"c-{i}", anchors=())


def _stand(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    _append(data / "journal.jsonl", 5)
    store = SQLiteStore(data / "store.db")
    store.put("c-1", {"case_id": "c-1", "verdict": "confirmed"}, "stand-op", 10)
    (data / "conclusions").mkdir()
    (data / "conclusions" / "c-1.json").write_text('{"case_id": "c-1", "conclusion": "подтверждено"}', "utf-8")
    (data / "other.lock").write_text("123\n", "utf-8")
    (data / ".registry.json.ab.tmp").write_text("{", "utf-8")
    (data / "mandat-stand-x1y2").mkdir()
    (data / "mandat-stand-x1y2" / "registry.json").write_text("{}", "utf-8")
    if os.name != "nt":
        (data / "passwd").symlink_to("/etc/passwd")
        os.mkfifo(data / "pipe")
    return data


def test_backup_copies_journal_sqlite_and_files_consistently(tmp_path):
    data = _stand(tmp_path)
    result = backups.backup(data, tmp_path / "out", exclude=("mandat-stand-*",), now=MOMENT)
    assert result.ok, result.problems
    assert result.path.name == "mandat-20261128-031500" and not list((tmp_path / "out").glob(".*partial"))
    copy = result.path / "data"
    check = journal.verify_journal(copy / "journal.jsonl", anchors=())
    assert check.ok and check.records == 5 and result.journals[0]["records"] == 5
    assert not (copy / "store.db-wal").exists() and not (copy / "store.db-shm").exists()
    assert SQLiteStore(copy / "store.db").get("c-1") == ({"case_id": "c-1", "verdict": "confirmed"}, "stand-op")
    assert (copy / "conclusions" / "c-1.json").read_bytes() == (data / "conclusions" / "c-1.json").read_bytes()
    manifest = json.loads((result.path / "MANIFEST.json").read_text("utf-8"))
    how = {row["path"]: row["how"] for row in manifest["files"]}
    assert how["store.db"] == "sqlite" and how["journal.jsonl"] == how["journal.jsonl.head"] == "journal"
    assert how["conclusions/c-1.json"] == "copy" and manifest["created_at"] == "2026-11-28T03:15:00+03:00"
    skipped = {row["path"]: row["why"] for row in manifest["skipped"]}
    assert skipped["other.lock"] == "блокировка" and skipped["mandat-stand-x1y2/"] == "исключено"
    assert skipped[".registry.json.ab.tmp"] == "временный файл"
    if os.name != "nt":
        assert skipped["passwd"] == "символическая ссылка" and skipped["pipe"] == "не обычный файл"
        assert (result.path.stat().st_mode & 0o777) == 0o700
        assert all((copy / row["path"]).stat().st_mode & 0o777 == 0o600 for row in manifest["files"])


def test_rotation_keeps_the_newest_copies_and_drops_abandoned_ones(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "state.json").write_text("{}", "utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes").mkdir()
    stale = out / ".mandat-20200101-000000.partial"
    stale.mkdir()
    os.utime(stale, (0, 0))
    names = [backups.backup(data, out, now=MOMENT + timedelta(days=i)).path.name for i in range(16)]
    twin = backups.backup(data, out, now=MOMENT + timedelta(days=15))
    assert twin.path.name == names[-1] + "-2"
    kept = sorted(p.name for p in out.iterdir() if backups.NAME.fullmatch(p.name))
    assert kept == sorted(names[3:] + [twin.path.name]) and len(kept) == backups.KEEP
    assert (out / "notes").is_dir() and not stale.exists()
    assert backups.rotate(out, 1) == sorted(kept, key=backups._order)[:-1]


def test_copies_taken_while_the_journal_is_written_verify(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "journal.jsonl"
    _append(path, 3)
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            _append(path, 1)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        results = [backups.backup(data, tmp_path / "out", now=MOMENT + timedelta(seconds=i)) for i in range(3)]
    finally:
        stop.set()
        thread.join()
    for result in results:
        assert result.ok and result.journals[0]["ok"] and result.journals[0]["records"] >= 3


def test_journal_lock_holds_writers_until_released(tmp_path):
    path = tmp_path / "journal.jsonl"
    _append(path, 1)
    done = threading.Event()
    with journal.locked(path):
        thread = threading.Thread(target=lambda: (_append(path, 1), done.set()))
        thread.start()
        time.sleep(0.2)
        assert not done.is_set()
    thread.join(5)
    assert done.is_set() and journal.verify_journal(path).records == 2


def test_problems_keep_the_copy_but_say_so(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    _append(data / "journal.jsonl", 2)
    lines = (data / "journal.jsonl").read_text("utf-8").splitlines()
    lines[0] = lines[0].replace('"c-0"', '"c-9"')
    (data / "journal.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    (data / "broken.db").write_bytes(backups.SQLITE_MAGIC + b"\0" * 200)
    result = backups.backup(data, tmp_path / "out", now=MOMENT)
    assert not result.ok and result.path.is_dir()
    assert any("не прошла проверку" in p for p in result.problems) and any("broken.db" in p for p in result.problems)

    @__import__("contextlib").contextmanager
    def busy(path):
        raise ValueError("журнал занят другой записью")
        yield

    monkeypatch.setattr(backups.journal, "locked", busy)
    monkeypatch.setattr(backups.time, "sleep", lambda s: None)
    result = backups.backup(data, tmp_path / "out", now=MOMENT)
    assert any("занят записью" in p for p in result.problems)


def test_failed_backup_leaves_no_half_copy_and_bad_targets_are_refused(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.json").write_text("{}", "utf-8")

    def boom(src, dst):
        raise RuntimeError("диск отвалился")

    monkeypatch.setattr(backups, "_copy_file", boom)
    with pytest.raises(RuntimeError):
        backups.backup(data, tmp_path / "out", now=MOMENT)
    assert list((tmp_path / "out").iterdir()) == []
    with pytest.raises(ValueError, match="внутри каталога данных"):
        backups.backup(data, data / "backups")
    with pytest.raises(ValueError, match="каталога данных нет"):
        backups.backup(tmp_path / "nope", tmp_path / "out")
    with pytest.raises(ValueError, match="keep"):
        backups.backup(data, tmp_path / "out", keep=0)


def test_cli_backup(tmp_path, monkeypatch, capsys):
    data = _stand(tmp_path)
    out = tmp_path / "out"
    assert cli.main(["backup", "--data-dir", str(data), "--out", str(out), "--exclude", "mandat-stand-*"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["files"] == 4 and report["journals"][0]["records"] == 5
    monkeypatch.setenv("MANDAT_STAND_DIR", str(data))
    assert cli.main(["backup", "--out", str(out), "--keep", "1"]) == 0
    assert len([p for p in out.iterdir() if backups.NAME.fullmatch(p.name)]) == 1
    monkeypatch.delenv("MANDAT_STAND_DIR")
    capsys.readouterr()
    assert cli.main(["backup", "--out", str(out)]) == 2
    assert cli.main(["backup", "--data-dir", str(data), "--out", str(out), "--keep", "0"]) == 2
    (data / "journal.jsonl").write_text("не журнал\n", "utf-8")
    assert cli.main(["backup", "--data-dir", str(data), "--out", str(out)]) == 1
    assert "замечание" in capsys.readouterr().err
