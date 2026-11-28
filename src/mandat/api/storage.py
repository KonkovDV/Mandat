"""Общее состояние API: окно лимита частоты, отчёты с владельцами, однократные ключи.

Три реализации одного интерфейса ``Store``:

* ``MemoryStore`` — по умолчанию, один процесс. Для стенда и тестов.
* ``SQLiteStore`` — файл SQLite в режиме WAL: несколько воркеров на одной машине. Только stdlib.
* ``RedisStore`` — несколько машин. Нужен extra ``redis``.

Выбор: ``MANDAT_API_STORE`` = ``memory`` | ``sqlite:///путь/к/файлу.db`` | ``redis://хост:порт/база``.
Окно лимита скользящее: запрос старше 60 секунд уже не считается, ровно 60 — ещё
считается. Время берётся настенное (``time.time``): монотонные часы у
разных процессов не сравнимы.

Отчёты считаются по владельцу: ``max_reports`` — квота одного пользователя, а не общий потолок, поэтому поток
кейсов одного пользователя не вытесняет чужие. Сверх квоты уходят самые старые отчёты того же владельца, у которых
разбор закрыт (``auto_decided``, ``accepted``, ``rejected``, ``closed``). Кейс в разборе или без прочитанного
разбора не вытесняется: если места нет, новый отчёт не сохраняется (``ReportQuotaExceeded``).

Заключения юриста по уставам (``review/conclusions.py``) лежат рядом с разборами и так же пишутся только по
ожидаемой версии. Указатель «заключения организации» строится по полю ``ogrn`` тела записи; ОГРН записи не
меняется. Счётчики применений — отдельно от записи: проверка кейса не переписывает заключение.
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
    """Состояние разбора уже записано с другой версией: запрос основан на устаревшем снимке."""

    def __init__(self, case_id: str, expected: int, current: int) -> None:
        super().__init__(f"разбор {case_id}: ожидалась версия {expected}, сейчас {current}")
        self.case_id = case_id
        self.expected = expected
        self.current = current


class ReportQuotaExceeded(RuntimeError):
    """У владельца ``limit`` отчётов, и вытеснить нечего: все они в разборе. Новый отчёт не сохранён."""

    def __init__(self, owner: str, limit: int) -> None:
        super().__init__(f"квота отчётов владельца {owner}: {limit}, вытеснить нечего")
        self.owner = owner
        self.limit = limit


class ConclusionVersionConflict(RuntimeError):
    """Заключение уже записано с другой версией: действие основано на устаревшем снимке."""

    def __init__(self, conclusion_id: str, expected: int, current: int) -> None:
        super().__init__(f"заключение {conclusion_id}: ожидалась версия {expected}, сейчас {current}")
        self.conclusion_id = conclusion_id
        self.expected = expected
        self.current = current


def conclusion_ogrn(body: dict[str, Any]) -> str:
    """ОГРН записи заключения — ключ указателя «заключения организации». Без него запись не сохраняется."""
    ogrn = body.get("ogrn")
    if not isinstance(ogrn, str) or not ogrn:
        raise ValueError("у записи заключения нет ОГРН")
    return ogrn


# Состояния, в которых кейс ждёт человека (``api/review.OPEN_STATES``): отчёт такого кейса не вытесняется.
OPEN_REVIEW_STATES = frozenset({"queued", "in_review", "docs_requested"})


def review_holds(body: dict[str, Any]) -> bool:
    """Разбор держит отчёт в хранилище: он открыт или его состояние не прочитать.

    Зашифрованный разбор (``api/sealing.py``) несёт рядом с шифром признак ``open`` без ПДн; запись без признака
    держит отчёт, как и открытый разбор.
    """
    if body.get("sealed"):
        return body.get("open") is not False
    return body.get("state") in OPEN_REVIEW_STATES


class Store(Protocol):
    name: str

    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        """Учесть запрос клиента. False, если за последние 60 секунд уже ``limit`` запросов."""

    def get(self, case_id: str) -> tuple[dict[str, Any], str] | None:
        """Отчёт и имя владельца или None."""

    def put(self, case_id: str, report: dict[str, Any], owner: str, max_reports: int) -> None:
        """Сохранить отчёт последним по порядку. Сверх ``max_reports`` у владельца вытесняются самые старые его отчёты
        с закрытым разбором; вытеснить нечего — ``ReportQuotaExceeded``, отчёт не сохраняется."""

    def room(self, owner: str, case_id: str, max_reports: int) -> bool:
        """Хватит ли места отчёту ``case_id`` владельца: квота не выбрана или есть что вытеснить. Только чтение."""

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        """True ровно один раз за ``ttl`` секунд для данного ключа (защита от повтора вебхука)."""

    def claim(self, key: str, value: str, ttl: float, now: float | None = None) -> str | None:
        """Атомарно занять ключ ``first_seen`` значением ``value`` на ``ttl`` секунд.

        ``None`` — ключ был свободен и теперь занят этим значением. Иначе — чем он уже занят: значение
        прежнего ``claim`` или пустая строка, если ключ занял ``first_seen`` без значения. Ключи общие
        с ``first_seen``: ``nonce``, израсходованный до появления ``claim``, остаётся израсходованным.
        """

    def release(self, key: str) -> None:
        """Снять ключ ``first_seen`` раньше срока: заявка на создание кейса завершена."""

    def ping(self) -> None:
        """Проверка живости хранилища для /readyz; исключение значит «не готово»."""

    # --- Двухфазная запись отчёта: stage → журнал → commit. GET видит только закоммиченное. ---
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

    def commit(
        self,
        case_id: str,
        attempt: str,
        max_reports: int,
        now: float | None = None,
        evicted: list[str] | None = None,
    ) -> bool:
        """Атомарно сделать ожидающий отчёт видимым, если номер попытки совпал; False — попытку вытеснили.

        Квота владельца — как у ``put``; вытесненные ради отчёта кейсы дописываются в ``evicted``. Вытеснить нечего —
        ожидающая запись снимается и поднимается ``ReportQuotaExceeded``: сверка не опубликует её позже.
        """

    def discard(self, case_id: str, attempt: str) -> bool:
        """Убрать ожидающий отчёт, если номер попытки совпал."""

    def erase(self, case_id: str) -> bool:
        """Удалить отчёт кейса и его ожидающую запись (152-ФЗ, ст. 21). True — отчёт был."""

    def pending(self, case_id: str) -> Pending | None:
        """Ожидающая запись по кейсу или None."""

    def pending_all(self) -> list[Pending]:
        """Все ожидающие записи (для сверки после сбоя)."""

    def pending_for_idem(self, owner: str, key: str) -> Pending | None:
        """Ожидающая запись, оставленная запросом с этим Idempotency-Key."""

    def idem_get(self, owner: str, key: str) -> tuple[str, str] | None:
        """(case_id, отпечаток запроса) завершённого запроса с этим Idempotency-Key или None."""

    # --- Состояние разбора: запись только по ожидаемой версии (CAS). ---
    def review_get(self, case_id: str) -> tuple[dict[str, Any], int] | None:
        """(состояние разбора, версия) или None, если разбора нет."""

    def review_put(self, case_id: str, state: dict[str, Any], expected_version: int) -> int:
        """Записать состояние, если текущая версия равна ``expected_version`` (0 — разбора ещё нет).

        Возвращает новую версию (``expected_version + 1``); иначе ``ReviewVersionConflict``.
        """

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        """Все разборы: (case_id, состояние, версия), по case_id. Для очереди разбора."""

    # --- High-watermark списков статусов для допуска операции: значение только растёт. ---
    def watermark_get(self, key: str) -> int | None:
        """Наибольший виденный номер списка статусов ``key`` или None."""

    def watermark_raise(self, key: str, value: int) -> int:
        """Атомарно поднять watermark до ``value``, если он меньше; вернуть значение после записи."""

    # --- Реестр заключений юриста: запись только по ожидаемой версии (CAS), указатель по ОГРН. ---
    def conclusion_get(self, conclusion_id: str) -> tuple[dict[str, Any], int] | None:
        """(заключение, версия) или None."""

    def conclusion_put(self, conclusion_id: str, body: dict[str, Any], expected_version: int) -> int:
        """Записать заключение, если текущая версия равна ``expected_version`` (0 — записи ещё нет).

        Возвращает новую версию; иначе ``ConclusionVersionConflict``. ОГРН берётся из ``body["ogrn"]`` и не меняется.
        """

    def conclusion_list(self, ogrn: str) -> list[tuple[str, dict[str, Any], int]]:
        """Заключения организации: (номер, заключение, версия), по номеру."""

    def conclusion_tally(self, conclusion_id: str, cleared: bool) -> None:
        """Учесть применение заключения в проверке; ``cleared`` — устав закрыт и кейс не ушёл к юристу."""

    def conclusion_tallies(self, conclusion_ids: list[str]) -> dict[str, tuple[int, int]]:
        """Сколько раз заключения применены и сколько операций закрыли: номер → (применено, закрыто)."""


class MemoryStore:
    name = "memory"

    def ping(self) -> None:
        return None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._reports: OrderedDict[str, tuple[dict[str, Any], str]] = OrderedDict()
        self._seen: dict[str, float] = {}
        self._seen_values: dict[str, str] = {}
        self._pending: dict[str, Pending] = {}
        self._idem: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._reviews: dict[str, tuple[dict[str, Any], int]] = {}
        self._watermarks: dict[str, int] = {}
        self._conclusions: dict[str, tuple[dict[str, Any], int]] = {}
        self._tallies: dict[str, tuple[int, int]] = {}

    def watermark_get(self, key: str) -> int | None:
        with self._lock:
            return self._watermarks.get(key)

    def watermark_raise(self, key: str, value: int) -> int:
        with self._lock:
            self._watermarks[key] = max(value, self._watermarks.get(key, value))
            return self._watermarks[key]

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
            if self._room(case_id, owner, max_reports, evict=True) is None:
                raise ReportQuotaExceeded(owner, max_reports)
            self._reports.pop(case_id, None)
            self._reports[case_id] = (report, owner)

    def room(self, owner: str, case_id: str, max_reports: int) -> bool:
        with self._lock:
            return self._room(case_id, owner, max_reports, evict=False) is not None

    def _room(self, case_id: str, owner: str, limit: int, *, evict: bool) -> list[str] | None:
        """Под блокировкой: самые старые отчёты владельца с закрытым разбором, которые уступают место ``case_id``.

        ``None`` — столько вытеснить нельзя. С ``evict`` они удаляются.
        """
        mine = [cid for cid, (_, who) in self._reports.items() if who == owner and cid != case_id]
        extra = len(mine) + 1 - limit
        if extra <= 0:
            return []
        gone = [cid for cid in mine if cid in self._reviews and not review_holds(self._reviews[cid][0])][:extra]
        if len(gone) < extra:
            return None
        if evict:
            for cid in gone:
                del self._reports[cid]
        return gone

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            self._drop_stale(now)
            if self._seen.get(key, 0.0) > now:
                return False
            self._seen[key] = now + ttl
            self._seen_values.pop(key, None)
            return True

    def claim(self, key: str, value: str, ttl: float, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        with self._lock:
            self._drop_stale(now)
            if self._seen.get(key, 0.0) > now:
                return self._seen_values.get(key, "")
            self._seen[key] = now + ttl
            self._seen_values[key] = value
            return None

    def _drop_stale(self, now: float) -> None:
        if len(self._seen) > MAX_CLIENTS:
            for stale in [k for k, until in self._seen.items() if until <= now]:
                del self._seen[stale]
                self._seen_values.pop(stale, None)

    def release(self, key: str) -> None:
        with self._lock:
            self._seen.pop(key, None)
            self._seen_values.pop(key, None)

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

    def commit(
        self,
        case_id: str,
        attempt: str,
        max_reports: int,
        now: float | None = None,
        evicted: list[str] | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            pend = self._pending.get(case_id)
            if pend is None or pend.attempt != attempt:
                return False
            del self._pending[case_id]
            gone = self._room(case_id, pend.owner, max_reports, evict=True)
            if gone is None:
                raise ReportQuotaExceeded(pend.owner, max_reports)
            self._reports.pop(case_id, None)
            self._reports[case_id] = (pend.report, pend.owner)
            if evicted is not None:
                evicted.extend(gone)
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

    def erase(self, case_id: str) -> bool:
        with self._lock:
            self._pending.pop(case_id, None)
            return self._reports.pop(case_id, None) is not None

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

    def conclusion_get(self, conclusion_id: str) -> tuple[dict[str, Any], int] | None:
        with self._lock:
            found = self._conclusions.get(conclusion_id)
            return (json.loads(json.dumps(found[0])), found[1]) if found else None

    def conclusion_put(self, conclusion_id: str, body: dict[str, Any], expected_version: int) -> int:
        ogrn = conclusion_ogrn(body)
        copy = json.loads(json.dumps(body, ensure_ascii=False))
        with self._lock:
            old, current = self._conclusions.get(conclusion_id, ({}, 0))
            if current != expected_version:
                raise ConclusionVersionConflict(conclusion_id, expected_version, current)
            if old and old.get("ogrn") != ogrn:
                raise ValueError("ОГРН заключения не меняется")
            self._conclusions[conclusion_id] = (copy, current + 1)
            return current + 1

    def conclusion_list(self, ogrn: str) -> list[tuple[str, dict[str, Any], int]]:
        with self._lock:
            items = sorted((cid, row) for cid, row in self._conclusions.items() if row[0].get("ogrn") == ogrn)
        return [(cid, json.loads(json.dumps(body)), version) for cid, (body, version) in items]

    def conclusion_tally(self, conclusion_id: str, cleared: bool) -> None:
        with self._lock:
            applied, closed = self._tallies.get(conclusion_id, (0, 0))
            self._tallies[conclusion_id] = (applied + 1, closed + int(cleared))

    def conclusion_tallies(self, conclusion_ids: list[str]) -> dict[str, tuple[int, int]]:
        with self._lock:
            return {cid: self._tallies.get(cid, (0, 0)) for cid in conclusion_ids}


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
            CREATE INDEX IF NOT EXISTS reports_owner_seq ON reports (owner, seq);
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
                case_id TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL,
                held INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS watermarks (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conclusions (
                id TEXT PRIMARY KEY, ogrn TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS conclusions_ogrn ON conclusions (ogrn);
            CREATE TABLE IF NOT EXISTS conclusion_tallies (
                id TEXT PRIMARY KEY, applied INTEGER NOT NULL DEFAULT 0, cleared INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Файлы старого формата: у таблицы seen нет столбца value. Строки first_seen остаются со значением NULL.
        columns = {row[1] for row in self._db().execute("PRAGMA table_info(seen)")}
        if "value" not in columns:
            self._db().execute("ALTER TABLE seen ADD COLUMN value TEXT")
        if "held" not in self._columns("reviews"):
            self._add_held()

    def _columns(self, table: str) -> set[str]:
        return {str(row[1]) for row in self._db().execute(f"PRAGMA table_info({table})")}

    def _add_held(self) -> None:
        """Старый файл: признак «разбор держит отчёт» считается по телу разбора один раз, под блокировкой записи."""
        with self._tx() as db:
            if "held" in self._columns("reviews"):
                return  # другой процесс уже добавил столбец
            db.execute("ALTER TABLE reviews ADD COLUMN held INTEGER NOT NULL DEFAULT 1")
            rows = db.execute("SELECT case_id, body FROM reviews").fetchall()
            db.executemany(
                "UPDATE reviews SET held = ? WHERE case_id = ?",
                [(int(review_holds(json.loads(body))), case_id) for case_id, body in rows],
            )

    def ping(self) -> None:
        self._db().execute("SELECT 1 FROM reports LIMIT 1").fetchall()

    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=10000")
            # Удалённый отчёт с ПДн затирается нулями в файле базы, а не остаётся на свободных страницах.
            db.execute("PRAGMA secure_delete=ON")
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

    def watermark_get(self, key: str) -> int | None:
        row = self._db().execute("SELECT value FROM watermarks WHERE key = ?", (key,)).fetchone()
        return None if row is None else int(row[0])

    def watermark_raise(self, key: str, value: int) -> int:
        with self._tx() as db:
            db.execute(
                "INSERT INTO watermarks (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)",
                (key, value),
            )
            (stored,) = db.execute("SELECT value FROM watermarks WHERE key = ?", (key,)).fetchone()
            return int(stored)

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
            gone = self._room(db, case_id, owner, max_reports)
            if gone is None:
                raise ReportQuotaExceeded(owner, max_reports)
            db.executemany("DELETE FROM reports WHERE case_id = ?", [(c,) for c in gone])
            db.execute("DELETE FROM reports WHERE case_id = ?", (case_id,))
            db.execute("INSERT INTO reports (case_id, owner, body) VALUES (?, ?, ?)", (case_id, owner, body))

    def room(self, owner: str, case_id: str, max_reports: int) -> bool:
        return self._room(self._db(), case_id, owner, max_reports) is not None

    @staticmethod
    def _room(db: sqlite3.Connection, case_id: str, owner: str, limit: int) -> list[str] | None:
        """Самые старые отчёты владельца с закрытым разбором, которые уступают место ``case_id``; ``None`` — нельзя."""
        count_sql = "SELECT COUNT(*) FROM reports WHERE owner = ? AND case_id != ?"
        (count,) = db.execute(count_sql, (owner, case_id)).fetchone()
        extra = int(count) + 1 - limit
        if extra <= 0:
            return []
        rows = db.execute(
            "SELECT r.case_id FROM reports r JOIN reviews v ON v.case_id = r.case_id"
            " WHERE r.owner = ? AND r.case_id != ? AND v.held = 0 ORDER BY r.seq LIMIT ?",
            (owner, case_id, extra),
        ).fetchall()
        return [str(r[0]) for r in rows] if len(rows) == extra else None

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            db.execute("DELETE FROM seen WHERE until <= ?", (now,))
            cur = db.execute("INSERT OR IGNORE INTO seen (key, until) VALUES (?, ?)", (key, now + ttl))
            return cur.rowcount == 1

    def claim(self, key: str, value: str, ttl: float, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        with self._tx() as db:
            db.execute("DELETE FROM seen WHERE until <= ?", (now,))
            cur = db.execute("INSERT OR IGNORE INTO seen (key, until, value) VALUES (?, ?, ?)", (key, now + ttl, value))
            if cur.rowcount == 1:
                return None
            row = db.execute("SELECT value FROM seen WHERE key = ?", (key,)).fetchone()
            return "" if row is None or row[0] is None else str(row[0])

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

    def commit(
        self,
        case_id: str,
        attempt: str,
        max_reports: int,
        now: float | None = None,
        evicted: list[str] | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        refused: str | None = None
        with self._tx() as db:
            row = db.execute(
                "SELECT owner, body, idem_owner, idem_key, fp FROM pending WHERE case_id = ? AND attempt = ?",
                (case_id, attempt),
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM pending WHERE case_id = ?", (case_id,))
            gone = self._room(db, case_id, row[0], max_reports)
            if gone is None:
                refused = row[0]  # ожидающая запись снимается в той же транзакции
            else:
                db.executemany("DELETE FROM reports WHERE case_id = ?", [(c,) for c in gone])
                db.execute("DELETE FROM reports WHERE case_id = ?", (case_id,))
                db.execute("INSERT INTO reports (case_id, owner, body) VALUES (?, ?, ?)", (case_id, row[0], row[1]))
                if row[3]:
                    db.execute("DELETE FROM idem WHERE at < ?", (now - IDEM_TTL_SECONDS,))
                    db.execute(
                        "INSERT OR REPLACE INTO idem (owner, key, case_id, fp, at) VALUES (?, ?, ?, ?, ?)",
                        (row[2], row[3], case_id, row[4], now),
                    )
                if evicted is not None:
                    evicted.extend(gone)
        if refused is not None:
            raise ReportQuotaExceeded(refused, max_reports)
        return True

    def discard(self, case_id: str, attempt: str) -> bool:
        with self._tx() as db:
            return db.execute("DELETE FROM pending WHERE case_id = ? AND attempt = ?", (case_id, attempt)).rowcount == 1

    def erase(self, case_id: str) -> bool:
        with self._tx() as db:
            db.execute("DELETE FROM pending WHERE case_id = ?", (case_id,))
            found = db.execute("DELETE FROM reports WHERE case_id = ?", (case_id,)).rowcount == 1
        # Прежняя копия страницы с отчётом живёт в файле -wal до контрольной точки: переносим и обрезаем его сразу.
        # Занятый читателями файл обрежется на следующей контрольной точке; удаление от этого не отменяется.
        self._db().execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        return found

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
                "INSERT INTO reviews (case_id, version, body, held) VALUES (?, ?, ?, ?) ON CONFLICT(case_id) "
                "DO UPDATE SET version = excluded.version, body = excluded.body, held = excluded.held",
                (case_id, current + 1, body, int(review_holds(state))),
            )
            return current + 1

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        rows = self._db().execute("SELECT case_id, body, version FROM reviews ORDER BY case_id").fetchall()
        return [(str(r[0]), json.loads(r[1]), int(r[2])) for r in rows]

    # Заключения: проверка версии и запись под одной ``BEGIN IMMEDIATE``, как у разбора.
    def conclusion_get(self, conclusion_id: str) -> tuple[dict[str, Any], int] | None:
        row = self._db().execute("SELECT body, version FROM conclusions WHERE id = ?", (conclusion_id,)).fetchone()
        return (json.loads(row[0]), int(row[1])) if row else None

    def conclusion_put(self, conclusion_id: str, body: dict[str, Any], expected_version: int) -> int:
        ogrn = conclusion_ogrn(body)
        text = json.dumps(body, ensure_ascii=False)
        with self._tx() as db:
            row = db.execute("SELECT version, ogrn FROM conclusions WHERE id = ?", (conclusion_id,)).fetchone()
            current = int(row[0]) if row else 0
            if current != expected_version:
                raise ConclusionVersionConflict(conclusion_id, expected_version, current)
            if row is not None and row[1] != ogrn:
                raise ValueError("ОГРН заключения не меняется")
            db.execute(
                "INSERT INTO conclusions (id, ogrn, version, body) VALUES (?, ?, ?, ?) ON CONFLICT(id) "
                "DO UPDATE SET version = excluded.version, body = excluded.body",
                (conclusion_id, ogrn, current + 1, text),
            )
            return current + 1

    def conclusion_list(self, ogrn: str) -> list[tuple[str, dict[str, Any], int]]:
        rows = (
            self._db()
            .execute("SELECT id, body, version FROM conclusions WHERE ogrn = ? ORDER BY id", (ogrn,))
            .fetchall()
        )
        return [(str(r[0]), json.loads(r[1]), int(r[2])) for r in rows]

    def conclusion_tally(self, conclusion_id: str, cleared: bool) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO conclusion_tallies (id, applied, cleared) VALUES (?, 1, ?) ON CONFLICT(id) "
                "DO UPDATE SET applied = applied + 1, cleared = cleared + excluded.cleared",
                (conclusion_id, int(cleared)),
            )

    def conclusion_tallies(self, conclusion_ids: list[str]) -> dict[str, tuple[int, int]]:
        out = {cid: (0, 0) for cid in conclusion_ids}
        for cid in conclusion_ids:
            row = self._db().execute("SELECT applied, cleared FROM conclusion_tallies WHERE id = ?", (cid,)).fetchone()
            if row is not None:
                out[cid] = (int(row[0]), int(row[1]))
        return out


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

    # Квота владельца: отчёты владельца — сортированное множество owned:<владелец>, порядок задаёт счётчик, а не часы
    # (у двух записей в одну миллисекунду он всё равно разный). Вытесняются самые старые отчёты с закрытым разбором
    # (поле held = 0 у разбора); nil — вытеснить нечего.
    _QUOTA = """
    local function room(p, case_id, owner, limit, evict)
        local owned = p .. 'owned:' .. owner
        local count = redis.call('ZCARD', owned)
        if redis.call('ZSCORE', owned, case_id) then count = count - 1 end
        local extra = count + 1 - limit
        local gone = {}
        if extra <= 0 then return gone end
        for _, old in ipairs(redis.call('ZRANGE', owned, 0, -1)) do
            if #gone >= extra then break end
            if old ~= case_id and redis.call('HGET', p .. 'review:' .. old, 'held') == '0' then
                gone[#gone + 1] = old
            end
        end
        if #gone < extra then return nil end
        if evict then
            for _, old in ipairs(gone) do
                redis.call('DEL', p .. 'report:' .. old)
                redis.call('ZREM', owned, old)
                redis.call('HDEL', p .. 'owners', old)
            end
        end
        return gone
    end
    local function publish(p, case_id, owner, body)
        local prior = redis.call('HGET', p .. 'owners', case_id)
        if prior and prior ~= owner then redis.call('ZREM', p .. 'owned:' .. prior, case_id) end
        redis.call('SET', p .. 'report:' .. case_id, body)
        redis.call('ZADD', p .. 'owned:' .. owner, redis.call('INCR', p .. 'seq'), case_id)
        redis.call('HSET', p .. 'owners', case_id, owner)
    end
    """

    _ROOM = (
        _QUOTA
        + """
    if room(KEYS[1], ARGV[1], ARGV[2], tonumber(ARGV[3]), false) then return 1 end
    return 0
    """
    )

    _PUT = (
        _QUOTA
        + """
    local gone = room(KEYS[1], ARGV[1], ARGV[3], tonumber(ARGV[4]), true)
    if not gone then return {-1} end
    publish(KEYS[1], ARGV[1], ARGV[3], ARGV[2])
    return {1, unpack(gone)}
    """
    )

    # Подтверждение ожидающей записи: проверка номера попытки, квота и публикация отчёта в одном скрипте.
    # Вытеснить нечего — ожидающая запись снимается, ответ {-1, владелец}.
    _COMMIT = (
        _QUOTA
        + """
    local p = KEYS[1]
    local pk = p .. 'pending:' .. ARGV[1]
    local raw = redis.call('GET', pk)
    if not raw then return {0} end
    local pend = cjson.decode(raw)
    if pend.attempt ~= ARGV[2] then return {0} end
    local owner = pend.owner or cjson.decode(pend.body).owner
    local gone = room(p, ARGV[1], owner, tonumber(ARGV[3]), true)
    redis.call('DEL', pk)
    redis.call('SREM', p .. 'pendingset', ARGV[1])
    if not gone then
        if pend.idem_key ~= '' then redis.call('DEL', p .. 'pendidem:' .. pend.idem_owner .. ':' .. pend.idem_key) end
        return {-1, owner}
    end
    publish(p, ARGV[1], owner, pend.body)
    if pend.idem_key ~= '' then
        local ik = pend.idem_owner .. ':' .. pend.idem_key
        redis.call('SET', p .. 'idem:' .. ik, cjson.encode({case_id = ARGV[1], fp = pend.fp}), 'EX', tonumber(ARGV[4]))
        redis.call('DEL', p .. 'pendidem:' .. ik)
    end
    return {1, unpack(gone)}
    """
    )

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

    _ERASE = """
    local p = KEYS[1]
    local pk = p .. 'pending:' .. ARGV[1]
    local raw = redis.call('GET', pk)
    if raw then
        local pend = cjson.decode(raw)
        redis.call('DEL', pk)
        redis.call('SREM', p .. 'pendingset', ARGV[1])
        if pend.idem_key ~= '' then
            redis.call('DEL', p .. 'pendidem:' .. pend.idem_owner .. ':' .. pend.idem_key)
        end
    end
    local owner = redis.call('HGET', p .. 'owners', ARGV[1])
    if owner then
        redis.call('ZREM', p .. 'owned:' .. owner, ARGV[1])
        redis.call('HDEL', p .. 'owners', ARGV[1])
    end
    return redis.call('DEL', p .. 'report:' .. ARGV[1])
    """

    # CAS по версии разбора: сравнение и запись в одном скрипте. -1 — версия не совпала.
    # held — держит ли разбор отчёт в хранилище (review_holds): по нему квота не вытесняет кейс в разборе.
    _REVIEW_PUT = """
    local k = KEYS[1]
    local cur = tonumber(redis.call('HGET', k, 'version') or '0')
    if cur ~= tonumber(ARGV[1]) then return {-1, cur} end
    redis.call('HSET', k, 'version', cur + 1, 'body', ARGV[2], 'held', ARGV[4])
    redis.call('SADD', KEYS[2], ARGV[3])
    return {cur + 1, cur}
    """

    # CAS по версии заключения и указатель «заключения организации» в одном скрипте. -1 — версия не совпала,
    # -2 — у записи другой ОГРН (он не меняется).
    _CONCLUSION_PUT = """
    local k = KEYS[1]
    local cur = tonumber(redis.call('HGET', k, 'version') or '0')
    if cur ~= tonumber(ARGV[1]) then return {-1, cur} end
    local old = redis.call('HGET', k, 'ogrn')
    if old and old ~= ARGV[3] then return {-2, cur} end
    redis.call('HSET', k, 'version', cur + 1, 'body', ARGV[2], 'ogrn', ARGV[3])
    redis.call('SADD', KEYS[2], ARGV[4])
    return {cur + 1, cur}
    """

    # Watermark только растёт: сравнение и запись в одном скрипте.
    _WATERMARK = """
    local cur = redis.call('GET', KEYS[1])
    local value = tonumber(ARGV[1])
    if cur and tonumber(cur) >= value then return tonumber(cur) end
    redis.call('SET', KEYS[1], value)
    return value
    """

    # Занять ключ значением или узнать, чем он занят: SET NX и GET в одном скрипте. Ключ — тот же, что у
    # first_seen, поэтому nonce, израсходованный раньше, остаётся израсходованным (значение «1»).
    _CLAIM = """
    if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then return false end
    return redis.call('GET', KEYS[1]) or ''
    """

    def __init__(self, client: Any, prefix: str = "mandat:") -> None:
        self.r = client
        self.prefix = prefix
        self._claim = client.register_script(self._CLAIM)
        self._watermark = client.register_script(self._WATERMARK)
        self._allow = client.register_script(self._ALLOW)
        self._put = client.register_script(self._PUT)
        self._room = client.register_script(self._ROOM)
        self._commit = client.register_script(self._COMMIT)
        self._discard = client.register_script(self._DISCARD)
        self._erase = client.register_script(self._ERASE)
        self._review_put = client.register_script(self._REVIEW_PUT)
        self._conclusion_put = client.register_script(self._CONCLUSION_PUT)

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
        if int(self._put(keys=[self.prefix], args=[case_id, body, owner, max_reports])[0]) < 0:
            raise ReportQuotaExceeded(owner, max_reports)

    def room(self, owner: str, case_id: str, max_reports: int) -> bool:
        return bool(self._room(keys=[self.prefix], args=[case_id, owner, max_reports]))

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        return bool(self.r.set(f"{self.prefix}seen:{key}", "1", nx=True, ex=max(1, int(ttl))))

    def claim(self, key: str, value: str, ttl: float, now: float | None = None) -> str | None:
        held = self._claim(keys=[f"{self.prefix}seen:{key}"], args=[value, max(1, int(ttl))])
        if held is None:
            return None
        return held.decode("utf-8") if isinstance(held, bytes) else str(held)

    def release(self, key: str) -> None:
        self.r.delete(f"{self.prefix}seen:{key}")

    def watermark_get(self, key: str) -> int | None:
        raw = self.r.get(f"{self.prefix}watermark:{key}")
        return None if raw is None else int(raw)

    def watermark_raise(self, key: str, value: int) -> int:
        return int(self._watermark(keys=[f"{self.prefix}watermark:{key}"], args=[int(value)]))

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
            "owner": owner,
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

    def commit(
        self,
        case_id: str,
        attempt: str,
        max_reports: int,
        now: float | None = None,
        evicted: list[str] | None = None,
    ) -> bool:
        code, *rest = self._commit(keys=[self.prefix], args=[case_id, attempt, max_reports, int(IDEM_TTL_SECONDS)])
        if int(code) < 0:
            raise ReportQuotaExceeded(self._text(rest[0]), max_reports)  # отказ несёт владельца попытки
        if evicted is not None:
            evicted.extend(self._text(g) for g in rest)
        return int(code) == 1

    def discard(self, case_id: str, attempt: str) -> bool:
        return bool(self._discard(keys=[self.prefix], args=[case_id, attempt]))

    def erase(self, case_id: str) -> bool:
        return bool(self._erase(keys=[self.prefix], args=[case_id]))

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
            keys=[f"{self.prefix}review:{case_id}", f"{self.prefix}reviews"],
            args=[expected_version, body, case_id, int(review_holds(state))],
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

    def conclusion_get(self, conclusion_id: str) -> tuple[dict[str, Any], int] | None:
        found = self.r.hmget(f"{self.prefix}conclusion:{conclusion_id}", "body", "version")
        if found[0] is None:
            return None
        return json.loads(found[0]), int(found[1])

    def conclusion_put(self, conclusion_id: str, body: dict[str, Any], expected_version: int) -> int:
        ogrn = conclusion_ogrn(body)
        new, current = self._conclusion_put(
            keys=[f"{self.prefix}conclusion:{conclusion_id}", f"{self.prefix}conclusions:{ogrn}"],
            args=[expected_version, json.dumps(body, ensure_ascii=False), ogrn, conclusion_id],
        )
        if int(new) == -2:
            raise ValueError("ОГРН заключения не меняется")
        if int(new) < 0:
            raise ConclusionVersionConflict(conclusion_id, expected_version, int(current))
        return int(new)

    def conclusion_list(self, ogrn: str) -> list[tuple[str, dict[str, Any], int]]:
        out: list[tuple[str, dict[str, Any], int]] = []
        for cid in sorted(self._text(c) for c in self.r.smembers(f"{self.prefix}conclusions:{ogrn}")):
            body, version, owner = self.r.hmget(f"{self.prefix}conclusion:{cid}", "body", "version", "ogrn")
            # Номер в указателе чужой организации запись не отдаёт: ОГРН сверяется с самой записью.
            if body is not None and self._text(owner) == ogrn:
                out.append((cid, json.loads(body), int(version)))
        return out

    def conclusion_tally(self, conclusion_id: str, cleared: bool) -> None:
        key = f"{self.prefix}conclusion-tally:{conclusion_id}"
        pipe = self.r.pipeline()
        pipe.hincrby(key, "applied", 1)
        pipe.hincrby(key, "cleared", int(cleared))
        pipe.execute()

    def conclusion_tallies(self, conclusion_ids: list[str]) -> dict[str, tuple[int, int]]:
        pipe = self.r.pipeline()
        for cid in conclusion_ids:
            pipe.hmget(f"{self.prefix}conclusion-tally:{cid}", "applied", "cleared")
        rows = pipe.execute() if conclusion_ids else []
        return {cid: (int(row[0] or 0), int(row[1] or 0)) for cid, row in zip(conclusion_ids, rows, strict=True)}


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
