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
from typing import Any, Protocol

WINDOW_SECONDS = 60.0
MAX_CLIENTS = 10_000


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


class MemoryStore:
    name = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._reports: OrderedDict[str, tuple[dict[str, Any], str]] = OrderedDict()
        self._seen: dict[str, float] = {}

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
            """
        )

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

    def __init__(self, client: Any, prefix: str = "mandat:") -> None:
        self.r = client
        self.prefix = prefix
        self._allow = client.register_script(self._ALLOW)
        self._put = client.register_script(self._PUT)

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
