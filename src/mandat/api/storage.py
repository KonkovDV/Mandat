"""Общее состояние API: окно лимита частоты, отчёты с владельцами, однократные ключи.

Три реализации одного интерфейса ``Store``:

* ``MemoryStore`` — по умолчанию, один процесс. Для стенда и тестов.
* ``SQLiteStore`` — файл SQLite в режиме WAL: несколько воркеров на одной машине. Только stdlib.
* ``RedisStore`` — несколько машин. Нужен extra ``redis``.

Выбор: ``MANDAT_API_STORE`` = ``memory`` | ``sqlite:///путь/к/файлу.db`` | ``redis://хост:порт/база``.
Окно лимита скользящее: запрос старше 60 секунд уже не считается, ровно 60 — ещё
считается. Время берётся настенное (``time.time``): монотонные часы у
разных процессов не сравнимы.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, NamedTuple, Protocol

IDEM_TTL_SECONDS = 24 * 3600.0


class Idem(NamedTuple):
    """Idempotency-Key запроса: владелец, ключ и отпечаток тела (тот же ключ с другим телом — отказ)."""

    owner: str
    key: str
    fingerprint: str


class Pending(NamedTuple):
    case_id: str
    report: dict[str, Any]
    owner: str
    attempt: str
    idem: Idem | None
    staged_at: float


WINDOW_SECONDS = 60.0
MAX_CLIENTS = 10_000


class ReviewVersionConflict(RuntimeError):
    """Состояние разбора уже записано с другой версией (M-09): запрос основан на устаревшем снимке."""

    def __init__(self, case_id: str, expected: int, current: int) -> None:
        super().__init__(f"разбор {case_id}: ожидалась версия {expected}, сейчас {current}")
        self.case_id = case_id
        self.expected = expected
        self.current = current


class Store(Protocol):
    name: str

    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        """Учесть запрос клиента. False, если за последние 60 секунд уже ``limit`` запросов."""

    def get(self, case_id: str) -> tuple[dict[str, Any], str] | None:
        """Отчёт и имя владельца или None."""

    def put(self, case_id: str, report: dict[str, Any], owner: str, max_reports: int) -> None:
        """Сохранить отчёт последним по порядку и вытеснить самые старые сверх ``max_reports``."""

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        """True ровно один раз за ``ttl`` секунд для данного ключа (защита от повтора вебхука)."""

    def release(self, key: str) -> None:
        """Снять ключ ``first_seen`` раньше срока: заявка на создание кейса завершена."""

    def ping(self) -> None:
        """Проверка живости хранилища для /readyz; исключение значит «не готово»."""

    # --- Двухфазная запись отчёта (M-10): stage → журнал → commit. GET видит только закоммиченное. ---
    def stage(
        self,
        case_id: str,
        report: dict[str, Any],
        owner: str,
        attempt: str,
        idem: Idem | None,
        now: float | None = None,
    ) -> None:
        """Положить отчёт в «ожидающие» под номером попытки; новая попытка вытесняет прежнюю (fencing)."""

    def commit(self, case_id: str, attempt: str, max_reports: int, now: float | None = None) -> bool:
        """Атомарно сделать ожидающий отчёт видимым, если номер попытки совпал; False — попытку вытеснили."""

    def discard(self, case_id: str, attempt: str) -> bool:
        """Убрать ожидающий отчёт, если номер попытки совпал."""

    def pending(self, case_id: str) -> Pending | None:
        """Ожидающая запись по кейсу или None."""

    def pending_all(self) -> list[Pending]:
        """Все ожидающие записи (для сверки после сбоя)."""

    def pending_for_idem(self, owner: str, key: str) -> Pending | None:
        """Ожидающая запись, оставленная запросом с этим Idempotency-Key."""

    def idem_get(self, owner: str, key: str) -> tuple[str, str] | None:
        """(case_id, отпечаток запроса) завершённого запроса с этим Idempotency-Key или None."""

    # --- Состояние разбора (M-09): запись только по ожидаемой версии (CAS). ---
    def review_get(self, case_id: str) -> tuple[dict[str, Any], int] | None:
        """(состояние разбора, версия) или None, если разбора нет."""

    def review_put(self, case_id: str, state: dict[str, Any], expected_version: int) -> int:
        """Записать состояние, если текущая версия равна ``expected_version`` (0 — разбора ещё нет).

        Возвращает новую версию (``expected_version + 1``); иначе ``ReviewVersionConflict``.
        """

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        """Все разборы: (case_id, состояние, версия), по case_id. Для очереди разбора."""


class MemoryStore:
    name = "memory"

    def ping(self) -> None:
        return None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._reports: OrderedDict[str, tuple[dict[str, Any], str]] = OrderedDict()
        self._seen: dict[str, float] = {}
        self._pending: dict[str, Pending] = {}
        self._idem: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._reviews: dict[str, tuple[dict[str, Any], int]] = {}

    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if len(self._hits) > MAX_CLIENTS:
                for stale in [k for k, w in self._hits.items() if not w or now - w[-1] > WINDOW_SECONDS]:
                    del self._hits[stale]
            window = self._hits.setdefault(client, deque())
            while window and now - window[0] > WINDOW_SECONDS:
                window.popleft()
            if len(window) >= limit:
                return False
            window.append(now)
            return True

    def get(self, case_id: str) -> tuple[dict[str, Any], str] | None:
        with self._lock:
            return self._reports.get(case_id)

    def put(self, case_id: str, report: dict[str, Any], owner: str, max_reports: int) -> None:
        with self._lock:
            self._reports.pop(case_id, None)
            self._reports[case_id] = (report, owner)
            while len(self._reports) > max_reports:
                self._reports.popitem(last=False)

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if len(self._seen) > MAX_CLIENTS:
                for stale in [k for k, until in self._seen.items() if until <= now]:
                    del self._seen[stale]
            if self._seen.get(key, 0.0) > now:
                return False
            self._seen[key] = now + ttl
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._seen.pop(key, None)

    def stage(
        self,
        case_id: str,
        report: dict[str, Any],
        owner: str,
        attempt: str,
        idem: Idem | None,
        now: float | None = None,
    ) -> None:
        with self._lock:
            self._pending[case_id] = Pending(case_id, report, owner, attempt, idem, time.time() if now is None else now)

    def commit(self, case_id: str, attempt: str, max_reports: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            pend = self._pending.get(case_id)
            if pend is None or pend.attempt != attempt:
                return False
            del self._pending[case_id]
            self._reports.pop(case_id, None)
            self._reports[case_id] = (pend.report, pend.owner)
            while len(self._reports) > max_reports:
                self._reports.popitem(last=False)
            if pend.idem is not None:
                for k in [k for k, v in self._idem.items() if now - v[2] > IDEM_TTL_SECONDS]:
                    del self._idem[k]
                self._idem[(pend.idem.owner, pend.idem.key)] = (case_id, pend.idem.fingerprint, now)
            return True

    def discard(self, case_id: str, attempt: str) -> bool:
        with self._lock:
            pend = self._pending.get(case_id)
            if pend is None or pend.attempt != attempt:
                return False
            del self._pending[case_id]
            return True

    def pending(self, case_id: str) -> Pending | None:
        with self._lock:
            return self._pending.get(case_id)

    def pending_all(self) -> list[Pending]:
        with self._lock:
            return list(self._pending.values())

    def pending_for_idem(self, owner: str, key: str) -> Pending | None:
        with self._lock:
            return next(
                (p for p in self._pending.values() if p.idem and (p.idem.owner, p.idem.key) == (owner, key)), None
            )

    def idem_get(self, owner: str, key: str, now: float | None = None) -> tuple[str, str] | None:
        now = time.time() if now is None else now
        with self._lock:
            found = self._idem.get((owner, key))
            if found is None or now - found[2] > IDEM_TTL_SECONDS:
                return None
            return found[0], found[1]

    def review_get(self, case_id: str) -> tuple[dict[str, Any], int] | None:
        with self._lock:
            found = self._reviews.get(case_id)
            return (json.loads(json.dumps(found[0])), found[1]) if found else None

    def review_put(self, case_id: str, state: dict[str, Any], expected_version: int) -> int:
        body = json.loads(json.dumps(state, ensure_ascii=False))
        with self._lock:
            current = self._reviews.get(case_id, ({}, 0))[1]
            if current != expected_version:
                raise ReviewVersionConflict(case_id, expected_version, current)
            self._reviews[case_id] = (body, current + 1)
            return current + 1

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        with self._lock:
            items = sorted(self._reviews.items())
        return [(cid, json.loads(json.dumps(body)), version) for cid, (body, version) in items]


class SQLiteStore:
    """Одна БД на машину. ``BEGIN IMMEDIATE`` делает проверку и запись лимита атомарными между процессами."""

    name = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._local = threading.local()
        # executescript сам завершает транзакцию, поэтому схема создаётся вне _tx.
        self._db().executescript(
            """
            CREATE TABLE IF NOT EXISTS hits (client TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS hits_client_at ON hits (client, at);
            CREATE TABLE IF NOT EXISTS reports (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pending (
                case_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                attempt TEXT NOT NULL,
                body TEXT NOT NULL,
                idem_owner TEXT NOT NULL DEFAULT '',
                idem_key TEXT NOT NULL DEFAULT '',
                fp TEXT NOT NULL DEFAULT '',
                staged_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS idem (
                owner TEXT NOT NULL, key TEXT NOT NULL, case_id TEXT NOT NULL, fp TEXT NOT NULL, at REAL NOT NULL,
                PRIMARY KEY (owner, key)
            );
            CREATE TABLE IF NOT EXISTS reviews (
                case_id TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL
            );
            """
        )

    def ping(self) -> None:
        self._db().execute("SELECT 1 FROM reports LIMIT 1").fetchall()

    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=10000")
            self._local.db = db
        return db

    class _Tx:
        def __init__(self, db: sqlite3.Connection) -> None:
            self.db = db

        def __enter__(self) -> sqlite3.Connection:
            self.db.execute("BEGIN IMMEDIATE")
            return self.db

        def __exit__(self, exc_type: object, *_: object) -> None:
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")

    def _tx(self) -> SQLiteStore._Tx:
        return SQLiteStore._Tx(self._db())

    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            db.execute("DELETE FROM hits WHERE at < ?", (now - WINDOW_SECONDS,))
            (count,) = db.execute("SELECT COUNT(*) FROM hits WHERE client = ?", (client,)).fetchone()
            if count >= limit:
                return False
            db.execute("INSERT INTO hits (client, at) VALUES (?, ?)", (client, now))
            return True

    def get(self, case_id: str) -> tuple[dict[str, Any], str] | None:
        row = self._db().execute("SELECT body, owner FROM reports WHERE case_id = ?", (case_id,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def put(self, case_id: str, report: dict[str, Any], owner: str, max_reports: int) -> None:
        body = json.dumps(report, ensure_ascii=False)
        with self._tx() as db:
            db.execute("DELETE FROM reports WHERE case_id = ?", (case_id,))
            db.execute("INSERT INTO reports (case_id, owner, body) VALUES (?, ?, ?)", (case_id, owner, body))
            db.execute(
                "DELETE FROM reports WHERE seq NOT IN (SELECT seq FROM reports ORDER BY seq DESC LIMIT ?)",
                (max_reports,),
            )

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            db.execute("DELETE FROM seen WHERE until <= ?", (now,))
            cur = db.execute("INSERT OR IGNORE INTO seen (key, until) VALUES (?, ?)", (key, now + ttl))
            return cur.rowcount == 1

    def release(self, key: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM seen WHERE key = ?", (key,))

    def stage(
        self,
        case_id: str,
        report: dict[str, Any],
        owner: str,
        attempt: str,
        idem: Idem | None,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        body = json.dumps(report, ensure_ascii=False)
        i_owner, i_key, fp = (idem.owner, idem.key, idem.fingerprint) if idem else ("", "", "")
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO pending (case_id, owner, attempt, body, idem_owner, idem_key, fp, staged_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, owner, attempt, body, i_owner, i_key, fp, now),
            )

    def commit(self, case_id: str, attempt: str, max_reports: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            row = db.execute(
                "SELECT owner, body, idem_owner, idem_key, fp FROM pending WHERE case_id = ? AND attempt = ?",
                (case_id, attempt),
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM pending WHERE case_id = ?", (case_id,))
            db.execute("DELETE FROM reports WHERE case_id = ?", (case_id,))
            db.execute("INSERT INTO reports (case_id, owner, body) VALUES (?, ?, ?)", (case_id, row[0], row[1]))
            db.execute(
                "DELETE FROM reports WHERE seq NOT IN (SELECT seq FROM reports ORDER BY seq DESC LIMIT ?)",
                (max_reports,),
            )
            if row[3]:
                db.execute("DELETE FROM idem WHERE at < ?", (now - IDEM_TTL_SECONDS,))
                db.execute(
                    "INSERT OR REPLACE INTO idem (owner, key, case_id, fp, at) VALUES (?, ?, ?, ?, ?)",
                    (row[2], row[3], case_id, row[4], now),
                )
            return True

    def discard(self, case_id: str, attempt: str) -> bool:
        with self._tx() as db:
            return db.execute("DELETE FROM pending WHERE case_id = ? AND attempt = ?", (case_id, attempt)).rowcount == 1

    @staticmethod
    def _pending_row(row: Any) -> Pending:
        idem = Idem(row[4], row[5], row[6]) if row[5] else None
        return Pending(row[0], json.loads(row[3]), row[1], row[2], idem, row[7])

    def pending(self, case_id: str) -> Pending | None:
        row = (
            self._db()
            .execute(
                "SELECT case_id, owner, attempt, body, idem_owner, idem_key, fp, "
                "staged_at FROM pending WHERE case_id = ?",
                (case_id,),
            )
            .fetchone()
        )
        return self._pending_row(row) if row else None

    def pending_all(self) -> list[Pending]:
        rows = (
            self._db()
            .execute(
                "SELECT case_id, owner, attempt, body, idem_owner, idem_key, fp, "
                "staged_at FROM pending ORDER BY staged_at"
            )
            .fetchall()
        )
        return [self._pending_row(r) for r in rows]

    def pending_for_idem(self, owner: str, key: str) -> Pending | None:
        row = (
            self._db()
            .execute(
                "SELECT case_id, owner, attempt, body, idem_owner, idem_key, fp, "
                "staged_at FROM pending WHERE idem_owner = ? AND idem_key = ?",
                (owner, key),
            )
            .fetchone()
        )
        return self._pending_row(row) if row else None

    def idem_get(self, owner: str, key: str, now: float | None = None) -> tuple[str, str] | None:
        now = time.time() if now is None else now
        row = (
            self._db()
            .execute(
                "SELECT case_id, fp FROM idem WHERE owner = ? AND key = ? AND at >= ?",
                (owner, key, now - IDEM_TTL_SECONDS),
            )
            .fetchone()
        )
        return (row[0], row[1]) if row else None

    # Состояние разбора: проверка версии и запись под одной ``BEGIN IMMEDIATE``.
    def review_get(self, case_id: str) -> tuple[dict[str, Any], int] | None:
        row = self._db().execute("SELECT body, version FROM reviews WHERE case_id = ?", (case_id,)).fetchone()
        return (json.loads(row[0]), int(row[1])) if row else None

    def review_put(self, case_id: str, state: dict[str, Any], expected_version: int) -> int:
        body = json.dumps(state, ensure_ascii=False)
        with self._tx() as db:
            row = db.execute("SELECT version FROM reviews WHERE case_id = ?", (case_id,)).fetchone()
            current = int(row[0]) if row else 0
            if current != expected_version:
                raise ReviewVersionConflict(case_id, expected_version, current)
            db.execute(
                "INSERT INTO reviews (case_id, version, body) VALUES (?, ?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET version = excluded.version, body = excluded.body",
                (case_id, current + 1, body),
            )
            return current + 1

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        rows = self._db().execute("SELECT case_id, body, version FROM reviews ORDER BY case_id").fetchall()
        return [(str(r[0]), json.loads(r[1]), int(r[2])) for r in rows]


class RedisStore:
    """Окно лимита — сортированное множество на клиента, отчёты — строки JSON, порядок — ещё одно множество."""

    name = "redis"

    # Проверка и запись в одном скрипте: между ними не вклинится другой воркер.
    _ALLOW = """
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', '(' .. (ARGV[1] - ARGV[2]))
    if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
    redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
    redis.call('EXPIRE', KEYS[1], math.ceil(ARGV[2]))
    return 1
    """

    # Порядок задаёт счётчик, а не часы: у двух записей в одну миллисекунду он всё равно разный.
    _PUT = """
    local p = KEYS[1]
    local seq = redis.call('INCR', p .. 'seq')
    redis.call('SET', p .. 'report:' .. ARGV[1], ARGV[2])
    redis.call('ZADD', p .. 'reports', seq, ARGV[1])
    local extra = redis.call('ZCARD', p .. 'reports') - tonumber(ARGV[3])
    if extra > 0 then
        for _, old in ipairs(redis.call('ZRANGE', p .. 'reports', 0, extra - 1)) do
            redis.call('DEL', p .. 'report:' .. old)
            redis.call('ZREM', p .. 'reports', old)
        end
    end
    return 1
    """

    # Подтверждение ожидающей записи: проверка номера попытки и публикация отчёта в одном скрипте.
    _COMMIT = """
    local p = KEYS[1]
    local pk = p .. 'pending:' .. ARGV[1]
    local raw = redis.call('GET', pk)
    if not raw then return 0 end
    local pend = cjson.decode(raw)
    if pend.attempt ~= ARGV[2] then return 0 end
    local seq = redis.call('INCR', p .. 'seq')
    redis.call('SET', p .. 'report:' .. ARGV[1], pend.body)
    redis.call('ZADD', p .. 'reports', seq, ARGV[1])
    local extra = redis.call('ZCARD', p .. 'reports') - tonumber(ARGV[3])
    if extra > 0 then
        for _, old in ipairs(redis.call('ZRANGE', p .. 'reports', 0, extra - 1)) do
            redis.call('DEL', p .. 'report:' .. old)
            redis.call('ZREM', p .. 'reports', old)
        end
    end
    redis.call('DEL', pk)
    redis.call('SREM', p .. 'pendingset', ARGV[1])
    if pend.idem_key ~= '' then
        local ik = pend.idem_owner .. ':' .. pend.idem_key
        redis.call('SET', p .. 'idem:' .. ik, cjson.encode({case_id = ARGV[1], fp = pend.fp}), 'EX', tonumber(ARGV[4]))
        redis.call('DEL', p .. 'pendidem:' .. ik)
    end
    return 1
    """

    _DISCARD = """
    local p = KEYS[1]
    local pk = p .. 'pending:' .. ARGV[1]
    local raw = redis.call('GET', pk)
    if not raw then return 0 end
    local pend = cjson.decode(raw)
    if pend.attempt ~= ARGV[2] then return 0 end
    redis.call('DEL', pk)
    redis.call('SREM', p .. 'pendingset', ARGV[1])
    if pend.idem_key ~= '' then
        redis.call('DEL', p .. 'pendidem:' .. pend.idem_owner .. ':' .. pend.idem_key)
    end
    return 1
    """

    # CAS по версии разбора: сравнение и запись в одном скрипте. -1 — версия не совпала.
    _REVIEW_PUT = """
    local k = KEYS[1]
    local cur = tonumber(redis.call('HGET', k, 'version') or '0')
    if cur ~= tonumber(ARGV[1]) then return {-1, cur} end
    redis.call('HSET', k, 'version', cur + 1, 'body', ARGV[2])
    redis.call('SADD', KEYS[2], ARGV[3])
    return {cur + 1, cur}
    """

    def __init__(self, client: Any, prefix: str = "mandat:") -> None:
        self.r = client
        self.prefix = prefix
        self._allow = client.register_script(self._ALLOW)
        self._put = client.register_script(self._PUT)
        self._commit = client.register_script(self._COMMIT)
        self._discard = client.register_script(self._DISCARD)
        self._review_put = client.register_script(self._REVIEW_PUT)

    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        member = f"{now:.6f}:{os.urandom(4).hex()}"
        keys = [f"{self.prefix}hits:{client}"]
        return bool(self._allow(keys=keys, args=[now, WINDOW_SECONDS, limit, member]))

    def get(self, case_id: str) -> tuple[dict[str, Any], str] | None:
        raw = self.r.get(f"{self.prefix}report:{case_id}")
        if raw is None:
            return None
        packed = json.loads(raw)
        return packed["report"], packed["owner"]

    def put(self, case_id: str, report: dict[str, Any], owner: str, max_reports: int) -> None:
        body = json.dumps({"report": report, "owner": owner}, ensure_ascii=False)
        self._put(keys=[self.prefix], args=[case_id, body, max_reports])

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        return bool(self.r.set(f"{self.prefix}seen:{key}", "1", nx=True, ex=max(1, int(ttl))))

    def release(self, key: str) -> None:
        self.r.delete(f"{self.prefix}seen:{key}")

    def ping(self) -> None:
        self.r.ping()

    def stage(
        self,
        case_id: str,
        report: dict[str, Any],
        owner: str,
        attempt: str,
        idem: Idem | None,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        body = json.dumps({"report": report, "owner": owner}, ensure_ascii=False)
        pend = {
            "attempt": attempt,
            "body": body,
            "idem_owner": idem.owner if idem else "",
            "idem_key": idem.key if idem else "",
            "fp": idem.fingerprint if idem else "",
            "staged_at": now,
        }
        pipe = self.r.pipeline()
        pipe.set(f"{self.prefix}pending:{case_id}", json.dumps(pend, ensure_ascii=False))
        pipe.sadd(f"{self.prefix}pendingset", case_id)
        if idem:
            pipe.set(f"{self.prefix}pendidem:{idem.owner}:{idem.key}", case_id)
        pipe.execute()

    def commit(self, case_id: str, attempt: str, max_reports: int, now: float | None = None) -> bool:
        return bool(self._commit(keys=[self.prefix], args=[case_id, attempt, max_reports, int(IDEM_TTL_SECONDS)]))

    def discard(self, case_id: str, attempt: str) -> bool:
        return bool(self._discard(keys=[self.prefix], args=[case_id, attempt]))

    def _pending_from(self, case_id: str, raw: Any) -> Pending | None:
        if raw is None:
            return None
        pend = json.loads(raw)
        packed = json.loads(pend["body"])
        idem = Idem(pend["idem_owner"], pend["idem_key"], pend["fp"]) if pend["idem_key"] else None
        return Pending(case_id, packed["report"], packed["owner"], pend["attempt"], idem, float(pend["staged_at"]))

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    def pending(self, case_id: str) -> Pending | None:
        return self._pending_from(case_id, self.r.get(f"{self.prefix}pending:{case_id}"))

    def pending_all(self) -> list[Pending]:
        found = [self.pending(self._text(c)) for c in self.r.smembers(f"{self.prefix}pendingset")]
        return sorted((p for p in found if p is not None), key=lambda p: p.staged_at)

    def pending_for_idem(self, owner: str, key: str) -> Pending | None:
        case_id = self.r.get(f"{self.prefix}pendidem:{owner}:{key}")
        return None if case_id is None else self.pending(self._text(case_id))

    def idem_get(self, owner: str, key: str) -> tuple[str, str] | None:
        raw = self.r.get(f"{self.prefix}idem:{owner}:{key}")
        if raw is None:
            return None
        packed = json.loads(raw)
        return packed["case_id"], packed["fp"]

    def review_get(self, case_id: str) -> tuple[dict[str, Any], int] | None:
        found = self.r.hmget(f"{self.prefix}review:{case_id}", "body", "version")
        if found[0] is None:
            return None
        return json.loads(found[0]), int(found[1])

    def review_put(self, case_id: str, state: dict[str, Any], expected_version: int) -> int:
        body = json.dumps(state, ensure_ascii=False)
        new, current = self._review_put(
            keys=[f"{self.prefix}review:{case_id}", f"{self.prefix}reviews"], args=[expected_version, body, case_id]
        )
        if int(new) < 0:
            raise ReviewVersionConflict(case_id, expected_version, int(current))
        return int(new)

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        out: list[tuple[str, dict[str, Any], int]] = []
        for raw in sorted(self._text(c) for c in self.r.smembers(f"{self.prefix}reviews")):
            found = self.review_get(raw)
            if found is not None:
                out.append((raw, found[0], found[1]))
        return out


def open_store(spec: str | None = None) -> Store:
    """Хранилище по строке ``MANDAT_API_STORE``. Пусто значит память одного процесса."""
    value = (spec if spec is not None else os.environ.get("MANDAT_API_STORE", "")).strip()
    if value in ("", "memory"):
        return MemoryStore()
    if value.startswith("sqlite:///"):
        return SQLiteStore(value[len("sqlite:///") :])
    if value.startswith(("redis://", "rediss://", "unix://")):
        try:
            import redis
        except ModuleNotFoundError as exc:
            raise ValueError("для redis:// установите extra: pip install 'mandat[redis]'") from exc
        return RedisStore(redis.Redis.from_url(value))
    raise ValueError("MANDAT_API_STORE: memory, sqlite:///путь или redis://хост:порт/база")
