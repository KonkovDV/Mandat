"""A4: файл lock журнала в Windows.

В Windows файл, открытый другим писателем, не удаляется (WinError 32), а файл, который ещё удаляется, нельзя
создать заново (PermissionError). Писатель, который в этот миг читает pid владельца, не должен ронять ни снятие,
ни взятие lock: иначе один упавший запрос оставлял lock со своим pid, и журнал процесса стоял до перезапуска.
Ветки Windows здесь проверяются подменой ``_WINDOWS`` и отказов файловой системы; настоящую гонку гоняет задача CI
``windows`` (тест одновременной записи восьми потоков).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from mandat.audit import journal


@pytest.fixture
def stand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANDAT_ENV", raising=False)
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)


def _busy_unlink(monkeypatch: pytest.MonkeyPatch, target: Path, failures: int | None) -> list[int]:
    """``Path.unlink`` для ``target`` отказывает, как Windows при открытом читателе: ``failures`` раз или всегда."""
    calls: list[int] = []
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == target:
            calls.append(1)
            if failures is None or len(calls) <= failures:
                raise PermissionError(13, "файл открыт другим писателем", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    return calls


def _busy_open(monkeypatch: pytest.MonkeyPatch, target: Path, failures: int | None) -> list[int]:
    """``os.open`` для ``target`` отказывает, как Windows для файла, который ещё удаляется."""
    calls: list[int] = []
    real = os.open

    def fake_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path) == target:
            calls.append(1)
            if failures is None or len(calls) <= failures:
                raise PermissionError(13, "файл lock ещё удаляется", str(path))
        return real(path, flags, mode)

    monkeypatch.setattr(journal.os, "open", fake_open)
    return calls


def test_release_retries_while_another_writer_reads_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(journal, "_WINDOWS", True)
    lock = tmp_path / "j.jsonl.lock"
    lock.write_text("1\n", encoding="ascii")
    calls = _busy_unlink(monkeypatch, lock, failures=3)
    journal._release_lock(lock)
    assert not lock.exists()
    assert len(calls) == 4


def test_release_gives_up_after_the_lock_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(journal, "_WINDOWS", True)
    monkeypatch.setattr(journal, "LOCK_WAIT_SECONDS", 0.02)
    lock = tmp_path / "j.jsonl.lock"
    lock.write_text("1\n", encoding="ascii")
    _busy_unlink(monkeypatch, lock, failures=None)
    with pytest.raises(PermissionError):
        journal._release_lock(lock)


def test_release_outside_windows_does_not_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(journal, "_WINDOWS", False)
    lock = tmp_path / "j.jsonl.lock"
    lock.write_text("1\n", encoding="ascii")
    calls = _busy_unlink(monkeypatch, lock, failures=1)
    with pytest.raises(PermissionError):
        journal._release_lock(lock)
    assert len(calls) == 1


def test_acquire_waits_while_the_previous_lock_is_being_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(journal, "_WINDOWS", True)
    path = tmp_path / "j.jsonl"
    calls = _busy_open(monkeypatch, path.with_name("j.jsonl.lock"), failures=2)
    fd, lock = journal._acquire_lock(path)
    try:
        assert len(calls) == 3
    finally:
        os.close(fd)
    assert lock.read_text(encoding="ascii").strip() == str(os.getpid())
    journal._release_lock(lock)
    assert not lock.exists()


def test_acquire_reports_busy_journal_when_the_lock_never_frees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(journal, "_WINDOWS", True)
    monkeypatch.setattr(journal, "LOCK_WAIT_SECONDS", 0.02)
    path = tmp_path / "j.jsonl"
    _busy_open(monkeypatch, path.with_name("j.jsonl.lock"), failures=None)
    with pytest.raises(ValueError, match="занят"):
        journal._acquire_lock(path)


def test_acquire_outside_windows_keeps_a_real_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(journal, "_WINDOWS", False)
    path = tmp_path / "j.jsonl"
    calls = _busy_open(monkeypatch, path.with_name("j.jsonl.lock"), failures=None)
    with pytest.raises(PermissionError):
        journal._acquire_lock(path)
    assert len(calls) == 1


@pytest.mark.parametrize("windows", [True, False])
def test_dead_lock_held_open_by_a_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool):
    monkeypatch.setattr(journal, "_WINDOWS", windows)
    monkeypatch.setattr(journal, "_pid_alive", lambda pid: False)
    lock = tmp_path / "j.jsonl.lock"
    lock.write_text("424242\n", encoding="ascii")
    _busy_unlink(monkeypatch, lock, failures=None)
    if windows:
        assert journal._reclaim_dead_lock(lock) is False  # снять позже, когда читатель закроет файл
    else:
        with pytest.raises(PermissionError):
            journal._reclaim_dead_lock(lock)
    assert lock.exists()


def test_simultaneous_appends_from_eight_threads_all_land(tmp_path: Path, stand: None):
    path = tmp_path / "journal.jsonl"
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def write(i: int) -> None:
        try:
            barrier.wait()
            for k in range(5):
                journal.append(path, action="verify", actor="a", role="operator", case_id=f"c{i}-{k}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = journal.verify_journal(path)
    assert check.ok and check.records == 40
    assert not path.with_name("journal.jsonl.lock").exists()
