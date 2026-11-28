"""Резервная копия каталога данных стенда без остановки службы: журнал аудита, базы SQLite и остальные файлы.

Каждый вид файлов копируется согласованным способом:

* журнал аудита (файл ``X`` рядом со свидетелем ``X.head``) — под той же блокировкой, под которой служба
  дописывает записи (``journal.locked``): активный и закрытые сегменты, свидетель, манифест и квитанции якоря
  попадают в копию одним срезом, и копия журнала сразу проверяется (``journal.verify_journal``);
* база SQLite (файл с заголовком ``SQLite format 3``) — встроенным резервным копированием SQLite, вместе с тем, что
  ещё лежит в WAL; файлы ``-wal``, ``-shm`` и ``-journal`` отдельно не копируются, копия проходит ``quick_check``;
* остальные файлы (разборы, заключения, списки статусов) — по одному: файл читается целиком во временный файл
  копии, затем fsync и переименование. Служба пишет такие файлы тоже через временный файл и переименование,
  поэтому в копию попадает целая версия каждого файла.

Не копируются блокировки (``*.lock``), временные файлы (``*.tmp``), символические ссылки, не обычные файлы и всё,
что совпало с шаблоном ``exclude`` (путь от каталога данных или имя). Копия собирается в скрытом каталоге
``.mandat-….partial`` и получает имя ``mandat-ГГГГММДД-ЧЧММСС`` (время московское), только когда готова целиком,
вместе с ``MANIFEST.json``: размер и SHA-256 каждого файла и итог проверки журнала. Права — 0700 на каталог и
0600 на файлы. После копии остаются ``keep`` последних, старые и брошенные недособранные удаляются.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

from mandat.audit import journal

KEEP = 14
MOSCOW = ZoneInfo("Europe/Moscow")
NAME = re.compile(r"mandat-(\d{8}-\d{6})(?:-(\d+))?")
SQLITE_MAGIC = b"SQLite format 3\x00"
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
# Недособранная копия старше этого брошена упавшим запуском: её можно удалить, она никому не нужна.
STALE_PARTIAL_SECONDS = 6 * 3600.0
# Журнал занят записью дольше LOCK_WAIT_SECONDS — ещё попытки, прежде чем копия журнала считается не снятой.
LOCK_ATTEMPTS = 3
_CHUNK = 1 << 20


class _Skip(Exception):
    """Файл не копируется; текст — причина для манифеста."""


@dataclass
class BackupResult:
    path: Path
    files: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    journals: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backup": str(self.path),
            "files": len(self.files),
            "bytes": sum(int(row["bytes"]) for row in self.files),
            "journals": self.journals,
            "skipped": self.skipped,
            "problems": self.problems,
            "removed": self.removed,
        }


def _transient(name: str) -> str | None:
    if name.endswith(".lock"):
        return "блокировка"
    if name.endswith(".tmp"):
        return "временный файл"
    return None


def _excluded(rel: Path, patterns: Sequence[str]) -> bool:
    text = rel.as_posix()
    return any(fnmatch(text, pattern) or fnmatch(rel.name, pattern) for pattern in patterns)


def _open_regular(path: Path) -> BinaryIO:
    """Открыть обычный файл на чтение, не проходя по ссылке и не застревая на FIFO, иначе ``_Skip``."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise _Skip("файл исчез во время копии") from exc
    except OSError as exc:
        if os.path.islink(path):
            raise _Skip("символическая ссылка") from exc
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise _Skip("не обычный файл")
    return os.fdopen(fd, "rb")


def _is_sqlite(path: Path) -> bool:
    try:
        with _open_regular(path) as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except (_Skip, OSError):
        return False


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # Windows не открывает каталог как файл: там переименование и так атомарно
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_new(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    return os.fdopen(fd, "wb")


def _copy_file(src: Path, dst: Path) -> tuple[int, str]:
    """Файл целиком во временный файл копии, fsync и переименование. Возвращает размер и SHA-256."""
    tmp = dst.with_name(f".{dst.name}.tmp")
    digest, size = hashlib.sha256(), 0
    with _open_regular(src) as source, _write_new(tmp) as target:
        for block in iter(lambda: source.read(_CHUNK), b""):
            digest.update(block)
            target.write(block)
            size += len(block)
        target.flush()
        os.fsync(target.fileno())
    os.replace(tmp, dst)
    return size, digest.hexdigest()


def _digest(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _copy_sqlite(src: Path, dst: Path) -> tuple[int, str]:
    """База SQLite через её резервное копирование: согласованный срез с учётом WAL, затем ``quick_check`` копии."""
    tmp = dst.with_name(f".{dst.name}.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(tmp))
        try:
            source.backup(target)
            verdict = target.execute("PRAGMA quick_check").fetchone()
            # Копия — самостоятельный файл без WAL: её можно открыть и без служебных файлов рядом.
            target.execute("PRAGMA journal_mode=DELETE").fetchall()
        finally:
            target.close()
    finally:
        source.close()
    if not verdict or verdict[0] != "ok":
        tmp.unlink(missing_ok=True)
        raise ValueError(f"копия базы не прошла quick_check: {verdict[0] if verdict else 'нет ответа'}")
    os.chmod(tmp, 0o600)
    with tmp.open("rb+") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, dst)
    return _digest(dst)


def _is_witness(path: Path) -> bool:
    """Свидетель журнала ``X.head``: маленький JSON с числом записей и последним хешем, а не случайный файл."""
    try:
        with _open_regular(path) as handle:
            data = json.loads(handle.read(4096).decode("utf-8"))
    except (_Skip, OSError, ValueError):
        return False
    return isinstance(data, dict) and isinstance(data.get("records"), int) and isinstance(data.get("hash"), str)


def _walk(root: Path, exclude: Sequence[str], problems: list[str]) -> tuple[list[Path], list[dict[str, str]]]:
    """Обычные файлы каталога данных (пути от него) и пропущенное с причиной. По ссылкам обход не идёт;
    каталог, который не читается, — в ``problems``, а не молча мимо копии."""
    found: list[Path] = []
    skipped: list[dict[str, str]] = []

    def unreadable(exc: OSError) -> None:
        problems.append(f"{exc.filename}: каталог не читается: {exc.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=unreadable, followlinks=False):
        here = Path(dirpath)
        base = here.relative_to(root)
        kept = []
        for name in sorted(dirnames):
            rel = base / name
            if _excluded(rel, exclude):
                skipped.append({"path": f"{rel.as_posix()}/", "why": "исключено"})
            elif (here / name).is_symlink():
                skipped.append({"path": f"{rel.as_posix()}/", "why": "символическая ссылка"})
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            rel = base / name
            why = "исключено" if _excluded(rel, exclude) else _transient(name)
            if why is None:
                try:
                    mode = os.lstat(here / name).st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(mode):
                    why = "символическая ссылка"
                elif not stat.S_ISREG(mode):
                    why = "не обычный файл"
            if why is not None:
                skipped.append({"path": rel.as_posix(), "why": why})
            else:
                found.append(rel)
    return found, skipped


def _journal_base(rel: Path, bases: list[Path]) -> Path | None:
    """Журнал, которому принадлежит файл: сам журнал или файл ``журнал.*`` рядом с ним; при вложенных именах —
    самый длинный."""
    matching = [
        b for b in bases if rel.parent == b.parent and (rel.name == b.name or rel.name.startswith(b.name + "."))
    ]
    return max(matching, key=lambda b: len(b.name)) if matching else None


def _copy_journal(root: Path, base: Path, target: Path, exclude: Sequence[str], result: BackupResult) -> None:
    """Все файлы журнала под его блокировкой записи, затем проверка цепочки копии."""
    live = root / base
    for attempt in range(1, LOCK_ATTEMPTS + 1):
        rows: list[dict[str, Any]] = []
        try:
            with journal.locked(live):
                for item in sorted(live.parent.iterdir()):
                    rel = base.parent / item.name
                    belongs = item.name == base.name or item.name.startswith(base.name + ".")
                    if not belongs or _transient(item.name) or _excluded(rel, exclude):
                        continue
                    try:
                        size, digest = _copy_file(item, target / rel)
                    except _Skip:
                        continue
                    rows.append({"path": rel.as_posix(), "bytes": size, "sha256": digest, "how": "journal"})
        except ValueError:
            if attempt == LOCK_ATTEMPTS:
                result.problems.append(f"журнал {base.as_posix()} занят записью: его копия не снята")
                return
            time.sleep(1.0)
            continue
        break
    result.files.extend(rows)
    try:
        check = journal.verify_journal(target / base, anchors=())
    except Exception as exc:  # noqa: BLE001 — ключ журнала не задан или файл не читается: копия есть, проверки нет
        result.journals.append({"path": base.as_posix(), "ok": False, "records": None, "reason": str(exc)})
        result.problems.append(f"копия журнала {base.as_posix()} не проверена: {exc}")
        return
    result.journals.append({"path": base.as_posix(), **check.as_dict()})
    if not check.ok:
        result.problems.append(f"копия журнала {base.as_posix()} не прошла проверку: {check.reason}")


def _copy_tree(root: Path, target: Path, exclude: Sequence[str], result: BackupResult) -> None:
    found, skipped = _walk(root, exclude, result.problems)
    result.skipped.extend(skipped)
    heads = [rel for rel in found if rel.name.endswith(".head") and _is_witness(root / rel)]
    bases = sorted({rel.with_name(rel.name[: -len(".head")]) for rel in heads})
    rest = [rel for rel in found if _journal_base(rel, bases) is None]
    databases = {rel for rel in rest if _is_sqlite(root / rel)}
    sidecars = {db.with_name(db.name + suffix) for db in databases for suffix in SQLITE_SIDECARS}
    for base in bases:
        _copy_journal(root, base, target, exclude, result)
    for rel in rest:
        if rel in sidecars:
            result.skipped.append({"path": rel.as_posix(), "why": "служебный файл SQLite: его данные уже в копии базы"})
            continue
        how = "sqlite" if rel in databases else "copy"
        copy = _copy_sqlite if how == "sqlite" else _copy_file
        try:
            size, digest = copy(root / rel, target / rel)
        except _Skip as exc:
            result.skipped.append({"path": rel.as_posix(), "why": str(exc)})
            continue
        except (OSError, ValueError, sqlite3.Error) as exc:
            result.problems.append(f"{rel.as_posix()}: {type(exc).__name__}: {exc}")
            continue
        result.files.append({"path": rel.as_posix(), "bytes": size, "sha256": digest, "how": how})


def _free_name(out: Path, name: str) -> str:
    candidate, n = name, 1
    while (out / candidate).exists() or (out / f".{candidate}.partial").exists():
        n += 1
        candidate = f"{name}-{n}"
    return candidate


def _order(name: str) -> tuple[str, int]:
    found = NAME.fullmatch(name)
    assert found is not None
    return found.group(1), int(found.group(2) or 1)


def rotate(out: Path, keep: int = KEEP, *, now: float | None = None) -> list[str]:
    """Оставить ``keep`` последних копий; удалить старые и брошенные недособранные. Возвращает удалённые имена.

    Трогает только каталоги с именами копий: чужие файлы в каталоге копий остаются.
    """
    if keep < 1:
        raise ValueError("keep: нужна хотя бы одна копия")
    moment = time.time() if now is None else now
    done = sorted(
        (p.name for p in out.iterdir() if NAME.fullmatch(p.name) and p.is_dir() and not p.is_symlink()), key=_order
    )
    removed: list[str] = []
    for name in done[:-keep]:
        shutil.rmtree(out / name, ignore_errors=True)
        removed.append(name)
    for item in out.iterdir():
        partial = item.name.startswith(".mandat-") and item.name.endswith(".partial")
        if partial and item.is_dir() and not item.is_symlink():
            if moment - item.stat().st_mtime > STALE_PARTIAL_SECONDS:
                shutil.rmtree(item, ignore_errors=True)
                removed.append(item.name)
    return removed


def backup(
    data_dir: Path,
    out_dir: Path,
    *,
    keep: int = KEEP,
    exclude: Sequence[str] = (),
    now: datetime | None = None,
) -> BackupResult:
    """Снять копию ``data_dir`` в ``out_dir/mandat-ГГГГММДД-ЧЧММСС`` и оставить ``keep`` последних копий.

    Каталог копий не может лежать внутри каталога данных. Ошибка посреди копии удаляет недособранный каталог и
    поднимается дальше; файл, который не удалось скопировать согласованно, и журнал, копия которого не прошла
    проверку, — в ``problems``: копия при этом сохраняется, а ``ok`` ложно.
    """
    if keep < 1:
        raise ValueError("keep: нужна хотя бы одна копия")
    root = data_dir.resolve()
    out = out_dir.resolve()
    if not root.is_dir():
        raise ValueError(f"каталога данных нет: {data_dir}")
    if out == root or root in out.parents:
        raise ValueError("каталог копий внутри каталога данных: копия копировала бы сама себя")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    moment = (now or datetime.now(UTC)).astimezone(MOSCOW)
    name = _free_name(out, f"mandat-{moment:%Y%m%d-%H%M%S}")
    work = out / f".{name}.partial"
    work.mkdir(mode=0o700)
    result = BackupResult(out / name)
    try:
        _copy_tree(root, work / "data", exclude, result)
        manifest = {
            "format": 1,
            "created_at": moment.isoformat(timespec="seconds"),
            "source": str(root),
            "files": result.files,
            "journals": result.journals,
            "skipped": result.skipped,
            "problems": result.problems,
        }
        with _write_new(work / "MANIFEST.json") as handle:
            handle.write((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(work)
        os.replace(work, result.path)
        _fsync_dir(out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    result.removed = rotate(out, keep)
    return result
