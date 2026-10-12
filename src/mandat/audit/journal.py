"""Журнал решений: append-only JSON Lines с хеш-цепочкой, HMAC и файлом-свидетелем.

Перенесено из SynAPS-ProgramPlan (``journal.py``). Каждая запись хранит хеш
предыдущей, поэтому удалённая, переставленная или изменённая строка ломает
цепочку, и ``verify_journal`` называет место. Файл ``<журнал>.head`` хранит число
записей и последний хеш: обрезка хвоста тоже обнаруживается. Подмену обоих
файлов ловит только внешний якорь — последний хеш, сохранённый вне журнала.

Ключ HMAC — переменная окружения ``MANDAT_JOURNAL_KEY``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
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


def _key(key: str | None) -> str | None:
    if key is not None:
        return key or None
    return os.environ.get("MANDAT_JOURNAL_KEY", "").strip() or None


def _sign(record_hash: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), record_hash.encode("ascii"), hashlib.sha256).hexdigest()


def witness_path(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_witness(path: Path, records: int, head: str) -> None:
    witness = witness_path(path)
    tmp = witness.with_name(witness.name + ".tmp")
    tmp.write_text(json.dumps({"records": records, "hash": head}, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(witness)


def _acquire_lock(path: Path) -> tuple[int, Path]:
    """Эксклюзивная блокировка. Чужой lock не снимается по возрасту: это не доказательство, что писатель умер."""
    lock = path.with_name(path.name + ".lock")
    try:
        return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR), lock
    except FileExistsError:
        raise ValueError("журнал занят другой записью") from None


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
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, lock = _acquire_lock(path)
    try:
        records = read_journal(path)
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
        signing = _key(key)
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
            _write_witness(path, record["seq"], record["hash"])
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
    records = read_journal(path)
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
        data = json.loads(witness.read_text(encoding="utf-8"))
        if data.get("records") != len(records) or data.get("hash") != head:
            return JournalCheck(False, len(records), None, "свидетель не совпадает: хвост удалён или файл подменён")
    if anchor is not None and anchor != head:
        return JournalCheck(False, len(records), None, "внешний якорь не совпадает с последней записью")
    return JournalCheck(True, len(records))
