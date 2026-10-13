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
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
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


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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


def _acquire_lock(path: Path) -> tuple[int, Path]:
    """Эксклюзивная блокировка с ожиданием до ``LOCK_WAIT_SECONDS``.

    Чужой lock не снимается по возрасту: это не доказательство, что писатель умер.
    """
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    delay = 0.001
    while True:
        try:
            return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR), lock
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("журнал занят другой записью") from None
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


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
        if path.exists() or witness_path(path).exists():
            # Новая запись продолжает только целую историю: битый префикс или срезанный хвост — отказ.
            check = verify_journal(path, key=signing or "")
            if not check.ok:
                raise JournalError(check.broken_at or 0, f"журнал не цел, запись не добавлена: {check.reason}")
        records = read_journal(path)
        if precondition is not None:
            done = precondition(records)
            if done is not None:
                return done
        record: dict[str, Any] = {
            "seq": len(records) + 1,
            "at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "actor": actor,
            "role": role,
            "action": action,
            "case_id": case_id,
            "payload_hash": payload_hash,
            "reason": reason,
            "details": details or {},
            "prev": records[-1]["hash"] if records else GENESIS,
        }
        record["hash"] = _digest(record)
        if signing:
            record["sig"] = _sign(record["hash"], signing)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        existing = path.read_bytes() if path.exists() else None
        witness = witness_path(path)
        previous_head = witness.read_bytes() if witness.exists() else None
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes((existing or b"") + line.encode("utf-8"))
        os.replace(tmp, path)
        try:
            _write_witness(path, record["seq"], record["hash"], signing)
        except Exception:
            if existing is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(existing)
            if previous_head is None:
                witness.unlink(missing_ok=True)
            else:
                witness.write_bytes(previous_head)
            raise
        return record
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


@dataclass(frozen=True)
class JournalCheck:
    ok: bool
    records: int
    broken_at: int | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "records": self.records, "broken_at": self.broken_at, "reason": self.reason}


def verify_journal(path: Path, *, anchor: str | None = None, key: str | None = None) -> JournalCheck:
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
    return JournalCheck(True, len(records))
