"""Журнал решений: JSON Lines, записи только дописываются, с хеш-цепочкой, HMAC и файлом-свидетелем.

Перенесено из SynAPS-ProgramPlan (``journal.py``). Каждая запись хранит хеш
предыдущей, поэтому удалённая, переставленная или изменённая строка ломает
цепочку, и ``verify_journal`` называет место. Файл ``<журнал>.head`` хранит число
записей и последний хеш: обрезка хвоста тоже обнаруживается. При заданном ключе
свидетель подписан тем же HMAC, поэтому обрезку с переписанным свидетелем видно и без
внешнего якоря. Без ключа подмену обоих файлов ловит только внешний якорь — последний
хеш, сохранённый вне журнала.

Строка журнала разбирается строго: повторяющийся ключ в JSON — подделка (хеш считался бы
по одному значению, а читатель увидел бы другое), битая строка — отказ проверки, а не исключение.

Ключ HMAC — переменная окружения ``MANDAT_JOURNAL_KEY``.

Сегменты (R2-PERF-2). Активный файл ``<журнал>`` держит не больше ``segment_records()`` записей;
заполненный сегмент переименовывается в ``<журнал>.NNNNNN`` и больше не меняется. Цепочка ``prev``
и сквозные номера ``seq`` проходят через границы сегментов, поэтому ``verify_journal`` проверяет
всю историю как одну цепочку: удалённый, переставленный или изменённый сегмент её рвёт, а
удаление последних сегментов ловит свидетель. Запись хеширует только активный сегмент — её
стоимость ограничена размером сегмента и не растёт с длиной журнала.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
ACTIONS = (
    "verify",
    "recommend_right",
    "review_open",
    "review_accept",
    "review_reject",
    "review_request_docs",
    "review_close",
    "review_appeal",
    "review_require",
    "review_reset",
    "event",
    "reverify",
    "status_change",
)


def _digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k not in {"hash", "sig"}}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Режимы, в которых журнал можно вести без ключа HMAC. Всё остальное (pilot, prod) требует ключ.
KEYLESS_MODES = frozenset({"", "demo", "dev", "test"})
MIN_KEY_BYTES = 32


class JournalKeyMissing(RuntimeError):
    """Режим требует подписанный журнал, а ``MANDAT_JOURNAL_KEY`` не задан."""


def mode() -> str:
    return os.environ.get("MANDAT_ENV", "demo").strip().lower()


def _key(key: str | None) -> str | None:
    if key is not None:
        value: str | None = key or None
    else:
        value = os.environ.get("MANDAT_JOURNAL_KEY", "").strip() or None
    if value is None and mode() not in KEYLESS_MODES:
        raise JournalKeyMissing(f"MANDAT_ENV={mode()}: журнал без подписи здесь не ведётся, задайте MANDAT_JOURNAL_KEY")
    if value is not None and mode() not in KEYLESS_MODES and len(value.encode("utf-8")) < MIN_KEY_BYTES:
        # RFC 2104, разд. 3: ключ HMAC короче длины выхода хеша (32 байта для SHA-256) не рекомендуется.
        raise JournalKeyMissing(f"MANDAT_ENV={mode()}: MANDAT_JOURNAL_KEY короче {MIN_KEY_BYTES} байт")
    return value


def _sign(record_hash: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), record_hash.encode("ascii"), hashlib.sha256).hexdigest()


def witness_path(path: Path) -> Path:
    return path.with_name(path.name + ".head")


class JournalError(ValueError):
    """Строка журнала не читается: битый JSON, повторяющийся ключ или не объект."""

    def __init__(self, line: int, reason: str):
        super().__init__(f"строка {line}: {reason}")
        self.line, self.reason = line, reason


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("повторяющийся ключ в записи")
    return dict(pairs)


def strict_loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_no_duplicates)


DEFAULT_SEGMENT_RECORDS = 1000


def segment_records() -> int:
    """Записей в сегменте: ``MANDAT_JOURNAL_SEGMENT_RECORDS``, по умолчанию 1000, не меньше 1."""
    raw = os.environ.get("MANDAT_JOURNAL_SEGMENT_RECORDS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_SEGMENT_RECORDS
    except ValueError:
        value = DEFAULT_SEGMENT_RECORDS
    return max(1, value)


def segment_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index:06d}")


def sealed_segments(path: Path) -> list[Path]:
    """Закрытые сегменты по порядку. Пропуск в нумерации — признак удаления, его ловит цепочка."""
    prefix = path.name + "."
    found = []
    if path.parent.exists():
        for item in path.parent.iterdir():
            suffix = item.name[len(prefix) :]
            if item.name.startswith(prefix) and len(suffix) == 6 and suffix.isdigit():
                found.append((int(suffix), item))
    return [item for _, item in sorted(found)]


def journal_files(path: Path) -> list[Path]:
    """Все файлы журнала в порядке цепочки: закрытые сегменты, затем активный."""
    return [*sealed_segments(path), *([path] if path.exists() else [])]


def manifest_path(path: Path) -> Path:
    """Манифест закрытых сегментов: строка JSON на сегмент — имя, диапазон seq, хеш последней записи, SHA-256."""
    return path.with_name(path.name + ".segments")


def read_manifest(path: Path) -> list[dict[str, Any]]:
    target = manifest_path(path)
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = strict_loads(line)
            if not isinstance(row, dict):
                raise ValueError("строка манифеста не объект")
            out.append(row)
    return out


def _manifest_tail(path: Path) -> dict[str, Any] | None:
    """Последняя строка манифеста без чтения всего файла: O(1) на запись."""
    try:
        with manifest_path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            lines = [x for x in handle.read().splitlines() if x.strip()]
    except FileNotFoundError:
        return None
    if not lines:
        return None
    row = strict_loads(lines[-1].decode("utf-8"))
    return row if isinstance(row, dict) else None


def _manifest_entry(segment: Path, index: int) -> dict[str, Any]:
    data = segment.read_bytes()
    rows = [strict_loads(x) for x in data.decode("utf-8").splitlines() if x.strip()]
    return {
        "segment": index,
        "name": segment.name,
        "first_seq": rows[0]["seq"] if rows else None,
        "last_seq": rows[-1]["seq"] if rows else None,
        "head": rows[-1]["hash"] if rows else None,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _append_manifest(path: Path, entry: dict[str, Any]) -> None:
    line = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(manifest_path(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def repair_manifest(path: Path) -> int:
    """Дописать в манифест закрытые сегменты, которых в нём нет (сбой между закрытием и записью
    манифеста или журнал до R4). Возвращает число добавленных строк. Не правит существующие строки."""
    listed = {row.get("name") for row in read_manifest(path)}
    added = 0
    for segment in sealed_segments(path):
        if segment.name not in listed:
            _append_manifest(path, _manifest_entry(segment, int(segment.name.rsplit(".", 1)[1])))
            added += 1
    return added


def manifest_problem(path: Path) -> str | None:
    """Манифест совпадает с закрытыми сегментами: те же имена по порядку, SHA-256 и диапазоны seq."""
    try:
        rows = read_manifest(path)
    except ValueError:
        return "манифест сегментов не разбирается"
    segments = sealed_segments(path)
    if not rows and not segments:
        return None
    if [r.get("name") for r in rows] != [s.name for s in segments]:
        return "манифест сегментов не совпадает с файлами сегментов"
    for row, segment in zip(rows, segments, strict=True):
        if _manifest_entry(segment, int(segment.name.rsplit(".", 1)[1])) != row:
            return f"сегмент {segment.name} не совпадает с манифестом"
    return None


def read_journal(path: Path) -> list[dict[str, Any]]:
    files = journal_files(path)
    if not files:
        return []
    lines: list[str] = []
    for item in files:
        lines.extend(item.read_text(encoding="utf-8").splitlines())
    out = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = strict_loads(line)
        except ValueError as exc:
            raise JournalError(number, str(exc) if "ключ" in str(exc) else "строка не разбирается как JSON") from exc
        if not isinstance(record, dict):
            raise JournalError(number, "запись не объект JSON")
        out.append(record)
    return out


def _witness_mac(records: int, head: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), f"head:{records}:{head}".encode("ascii"), hashlib.sha256).hexdigest()


def _write_witness(path: Path, records: int, head: str, key: str | None = None) -> None:
    witness = witness_path(path)
    tmp = witness.with_name(witness.name + ".tmp")
    data: dict[str, Any] = {"records": records, "hash": head}
    if key:
        data["mac"] = _witness_mac(records, head, key)
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(witness)


LOCK_WAIT_SECONDS = 5.0


def _lock_owner_pid(lock: Path) -> int | None:
    try:
        text = lock.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool | None:
    """``True`` — процесс жив, ``False`` — точно мёртв, ``None`` — неизвестно, lock не снимать."""
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _pid_alive_windows(pid: int) -> bool | None:
    import ctypes

    # WinDLL есть в типах ctypes только на Windows. На Linux функция не вызывается (os.name != "nt"),
    # но mypy проверяет тело везде, поэтому имена берутся через getattr.
    windll = getattr(ctypes, "WinDLL", None)
    last_error = getattr(ctypes, "get_last_error", None)
    if windll is None or last_error is None:
        return None
    kernel32 = windll("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    get_exit = kernel32.GetExitCodeProcess
    get_exit.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    get_exit.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    handle = open_process(0x1000, 0, pid)
    if not handle:
        err = last_error()
        if err == 87:  # ERROR_INVALID_PARAMETER: такого процесса нет
            return False
        if err == 5:  # ERROR_ACCESS_DENIED: процесс есть, спросить код выхода нельзя
            return True
        return None
    code = ctypes.c_uint32()
    ok = get_exit(handle, ctypes.byref(code))
    close_handle(handle)
    if not ok:
        return None
    if code.value == 259:  # STILL_ACTIVE
        return True
    return False


def _reclaim_dead_lock(lock: Path) -> bool:
    """Снять lock, только если записанный pid точно мёртв. По возрасту файла — нет."""
    pid = _lock_owner_pid(lock)
    if pid is None or _pid_alive(pid) is not False:
        return False
    if _lock_owner_pid(lock) != pid:
        return False
    try:
        lock.unlink()
    except FileNotFoundError:
        return True
    return True


def _acquire_lock(path: Path) -> tuple[int, Path]:
    """Эксклюзивная блокировка с ожиданием до ``LOCK_WAIT_SECONDS``.

    В файл lock пишется pid писателя. Чужой lock снимается, только если этот процесс
    точно мёртв. По возрасту файла lock не снимается: живой писатель мог держать его долго.
    Пустой файл и pid, который нельзя проверить, не снимаются.
    """
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    delay = 0.001
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if _reclaim_dead_lock(lock):
                continue
            if time.monotonic() >= deadline:
                raise ValueError("журнал занят другой записью") from None
            time.sleep(delay)
            delay = min(delay * 2, 0.05)
            continue
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        return fd, lock


def append(
    path: Path,
    *,
    action: str,
    actor: str,
    role: str,
    case_id: str,
    payload_hash: str | None = None,
    reason: str = "",
    details: dict[str, Any] | None = None,
    key: str | None = None,
    now: datetime | None = None,
    precondition: Callable[[list[dict[str, Any]]], dict[str, Any] | None] | None = None,
    anchors: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Добавить запись под блокировкой журнала.

    ``precondition`` вызывается под той же блокировкой с уже проверенной историей. Исключение из неё отменяет
    запись; возвращённая запись означает «уже записано» — она возвращается, новая не добавляется. Так проверка
    и запись не разделены окном, в которое успевает другой писатель.
    """
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    signing = _key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, lock = _acquire_lock(path)
    try:
        content = _content_digest(path)
        tail = _verified_tail(path, signing, content.hexdigest()) if precondition is None else None
        if tail is None and precondition is None:
            # Другой процесс дописал журнал после нашей записи: проверить только новые записи от
            # проверенного хвоста, а не всю историю (несколько воркеров uvicorn, R2-PERF-3).
            tail = _catch_up(path, signing)
        if tail is None:
            if journal_files(path) or witness_path(path).exists():
                repair_manifest(path)
                # Новая запись продолжает только целую историю: битый префикс или срезанный хвост — отказ.
                check = verify_journal(path, key=signing or "")
                if not check.ok:
                    raise JournalError(check.broken_at or 0, f"журнал не цел, запись не добавлена: {check.reason}")
            records = read_journal(path)
            if precondition is not None:
                done = precondition(records)
                if done is not None:
                    return done
            tail = (len(records), records[-1]["hash"] if records else GENESIS)
        seq, prev = tail
        record: dict[str, Any] = {
            "seq": seq + 1,
            "at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "actor": actor,
            "role": role,
            "action": action,
            "case_id": case_id,
            "payload_hash": payload_hash,
            "reason": reason,
            "details": details or {},
            "prev": prev,
        }
        record["hash"] = _digest(record)
        if signing:
            record["sig"] = _sign(record["hash"], signing)
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        witness = witness_path(path)
        previous_head = witness.read_bytes() if witness.exists() else None
        # Дозапись O_APPEND + fsync вместо переписывания всего файла: стоимость записи не растёт с длиной
        # журнала (R2-PERF-1). Откат при сбое свидетеля — усечение до прежнего размера.
        out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
        try:
            before = os.fstat(out).st_size
            os.write(out, line)
            os.fsync(out)
        finally:
            os.close(out)
        try:
            _write_witness(path, record["seq"], record["hash"], signing)
        except Exception:
            os.truncate(path, before)
            if previous_head is None:
                witness.unlink(missing_ok=True)
            else:
                witness.write_bytes(previous_head)
            _TAILS.pop(_cache_key(path), None)
            raise
        content.update(line)
        if record["seq"] % segment_records() == 0:
            # Сегмент заполнен: закрыть его. Следующая запись начнёт новый активный файл, хеш его
            # содержимого снова считается от пустого.
            last = _manifest_tail(path)
            if last is None and sealed_segments(path):
                repair_manifest(path)  # журнал до манифеста (R4): один раз перечислить сегменты
                last = _manifest_tail(path)
            index = int(last["segment"]) + 1 if last else 1
            target = segment_path(path, index)
            sealed_hex = content.hexdigest()
            os.replace(path, target)
            os.chmod(target, 0o400)  # закрытый сегмент только читается
            first_seq = int(last["last_seq"]) + 1 if last else 1
            _append_manifest(
                path,
                {
                    "segment": index,
                    "name": target.name,
                    "first_seq": first_seq,
                    "last_seq": record["seq"],
                    "head": record["hash"],
                    "sha256": sealed_hex,
                },
            )
            content = hashlib.sha256()
            # Внешний якорь: хеш закрытого сегмента уходит за пределы машины. Недоступный приёмник журнал
            # не останавливает — неопубликованный сегмент дописывается при следующем закрытии, а
            # verify_journal без квитанции не проходит.
            from mandat.audit import anchor

            sinks = anchor.configured_sinks() if anchors is None else anchors
            if sinks:
                anchor.publish_pending(path, sinks)
        _remember_tail(path, record["seq"], record["hash"], signing, content.hexdigest())
        return record
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


# Хвост журнала, проверенный этим процессом: SHA-256 всего файла, свидетель и ключ → (seq, hash).
# Если байты файла и свидетель с тех пор не менялись, полный разбор и пересчёт цепочки не нужны:
# хеш файла читается линейно, но в десятки раз дешевле разбора JSON и пересчёта хешей каждой записи.
# Отпечаток — по содержимому, а не по mtime: правка «той же длины» в пределах гранулярности часов ФС
# по времени не видна.
# Закрытые сегменты в отпечатке — stat манифеста и последнего закрытого сегмента (R4-PERF-5): O(1)
# на запись вместо обхода каталога и stat каждого сегмента. Это быстрая растяжка, а не доказательство;
# доказательство целостности закрытых сегментов — ``verify_journal`` (цепочка, HMAC, свидетель,
# SHA-256 сегментов в манифесте, внешний якорь). Сегменты закрываются с правами 0400.
Stat = tuple[int, int, int, int]
SealedState = tuple[Stat | None, str | None, Stat | None]
Fingerprint = tuple[str, bytes, str, SealedState]
# Значение: отпечаток, (seq, hash) последней записи, размер активного сегмента, (размер, SHA-256) манифеста.
_TAILS: dict[str, tuple[Fingerprint, tuple[int, str], int, tuple[int, str]]] = {}


def _cache_key(path: Path) -> str:
    return str(path.resolve())


def _content_digest(path: Path) -> Any:
    """SHA-256 содержимого файла (объект hashlib, чтобы дописать новую строку без перечитывания)."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except FileNotFoundError:
        pass
    return digest


def _marker(signing: str | None) -> str:
    return hashlib.sha256((signing or "").encode("utf-8")).hexdigest()


def _stat(target: Path) -> Stat | None:
    try:
        st = target.stat()
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _sealed_state(path: Path) -> SealedState:
    last = _manifest_tail(path)
    name = str(last["name"]) if last else None
    return (_stat(manifest_path(path)), name, _stat(path.with_name(name)) if name else None)


def _fingerprint(path: Path, signing: str | None, content: str | None = None) -> Fingerprint | None:
    try:
        witness = witness_path(path).read_bytes()
        sealed = _sealed_state(path)
    except (OSError, ValueError):
        return None
    return (content or _content_digest(path).hexdigest(), witness, _marker(signing), sealed)


def _verified_tail(path: Path, signing: str | None, content: str) -> tuple[int, str] | None:
    cached = _TAILS.get(_cache_key(path))
    if cached is None:
        return None
    fingerprint = _fingerprint(path, signing, content)
    return cached[1] if fingerprint is not None and fingerprint == cached[0] else None


def _catch_up(path: Path, signing: str | None) -> tuple[int, str] | None:
    """Проверить только записи, появившиеся после проверенного этим процессом хвоста.

    Условия: манифест только дописан (SHA-256 его прежнего префикса тот же), последний закрытый сегмент
    из отпечатка не менялся (stat); первые ``offset`` байт бывшего активного сегмента не изменились
    (SHA-256); новые записи продолжают цепочку и совпадают со свидетелем. Любое расхождение — ``None``,
    и вызывающий делает полную проверку.
    """
    cached = _TAILS.get(_cache_key(path))
    if cached is None:
        return None
    (prefix, _, marker, old), (seq, head), offset, (manifest_size, manifest_sha) = cached
    try:
        if marker != _marker(signing):
            return None
        new_names: list[str] = []
        if _stat(manifest_path(path)) != old[0]:
            raw = manifest_path(path).read_bytes()
            if len(raw) < manifest_size or hashlib.sha256(raw[:manifest_size]).hexdigest() != manifest_sha:
                return None
            for line in raw[manifest_size:].decode("utf-8").splitlines():
                if line.strip():
                    new_names.append(str(strict_loads(line)["name"]))
        if old[1] is not None and _stat(path.with_name(old[1])) != old[2]:
            return None
        files = [*(path.with_name(n) for n in new_names), *([path] if path.exists() else [])]
        if not files:
            return None
        first = files[0].read_bytes()
        if hashlib.sha256(first[:offset]).hexdigest() != prefix:
            return None
        blob = first[offset:] + b"".join(f.read_bytes() for f in files[1:])
        witness = strict_loads(witness_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    previous, count = head, seq
    for line in blob.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = strict_loads(line)
        except ValueError:
            return None
        count += 1
        if not isinstance(record, dict) or record.get("seq") != count or record.get("prev") != previous:
            return None
        if record.get("hash") != _digest(record):
            return None
        if signing:
            sig = record.get("sig")
            if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign(record["hash"], signing)):
                return None
        previous = record["hash"]
    if not isinstance(witness, dict) or witness.get("records") != count or witness.get("hash") != previous:
        return None
    if signing:
        mac = witness.get("mac")
        if not isinstance(mac, str) or not hmac.compare_digest(mac, _witness_mac(count, previous, signing)):
            return None
    return count, previous


def _remember_tail(path: Path, seq: int, head: str, signing: str | None, content: str) -> None:
    fingerprint = _fingerprint(path, signing, content)
    if fingerprint is not None:
        offset = path.stat().st_size if path.exists() else 0
        manifest = fingerprint[3][0]
        if manifest is None:
            state = (0, hashlib.sha256(b"").hexdigest())
        else:
            # Манифест мал (строка на 1000 записей) и меняется раз в сегмент: хеш при закрытии или промахе.
            cached = _TAILS.get(_cache_key(path))
            if cached is not None and cached[0][3][0] == manifest:
                state = cached[3]
            else:
                raw = manifest_path(path).read_bytes()
                state = (len(raw), hashlib.sha256(raw).hexdigest())
        _TAILS[_cache_key(path)] = (fingerprint, (seq, head), offset, state)


@dataclass(frozen=True)
class JournalCheck:
    ok: bool
    records: int
    broken_at: int | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "records": self.records, "broken_at": self.broken_at, "reason": self.reason}


def verify_journal(
    path: Path, *, anchor: str | None = None, key: str | None = None, anchors: Sequence[Any] | None = None
) -> JournalCheck:
    try:
        records = read_journal(path)
    except JournalError as exc:
        return JournalCheck(False, 0, exc.line, exc.reason)
    previous = GENESIS
    signing = _key(key)
    for index, record in enumerate(records, start=1):
        if record.get("seq") != index:
            return JournalCheck(False, len(records), index, "нарушен порядок номеров")
        if record.get("prev") != previous:
            return JournalCheck(False, len(records), index, "разорвана ссылка на предыдущую запись")
        if record.get("hash") != _digest(record):
            return JournalCheck(False, len(records), index, "содержимое записи не совпадает с хешем")
        if signing:
            sig = record.get("sig")
            if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign(record["hash"], signing)):
                return JournalCheck(False, len(records), index, "подпись записи не совпадает с ключом журнала")
        previous = record["hash"]
    head = records[-1]["hash"] if records else GENESIS
    witness = witness_path(path)
    if records and not witness.exists():
        return JournalCheck(False, len(records), None, "нет файла-свидетеля .head")
    if witness.exists():
        try:
            data = strict_loads(witness.read_text(encoding="utf-8"))
        except ValueError:
            return JournalCheck(False, len(records), None, "файл-свидетель .head не разбирается")
        if not isinstance(data, dict) or data.get("records") != len(records) or data.get("hash") != head:
            return JournalCheck(False, len(records), None, "свидетель не совпадает: хвост удалён или файл подменён")
        if signing:
            mac = data.get("mac")
            if not isinstance(mac, str) or not hmac.compare_digest(mac, _witness_mac(len(records), head, signing)):
                return JournalCheck(False, len(records), None, "подпись свидетеля .head не совпадает с ключом журнала")
    if anchor is not None and anchor != head:
        return JournalCheck(False, len(records), None, "внешний якорь не совпадает с последней записью")
    problem = manifest_problem(path)
    if problem:
        return JournalCheck(False, len(records), None, problem)
    from mandat.audit import anchor as external

    sinks = external.configured_sinks() if anchors is None else anchors
    if sinks:
        problem = external.check_anchors(path, sinks)
        if problem:
            return JournalCheck(False, len(records), None, problem)
    return JournalCheck(True, len(records))
