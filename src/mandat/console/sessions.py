"""Сессии браузера консоли и их демо-учётные записи.

У каждой сессии три учётные записи API с ограниченными ролями: ``operator`` создаёт кейсы, ``reviewer`` со
специализацией ``lawyer`` разбирает их, ``dbo`` запрашивает допуск операции для получателя стенда. Ролей ``admin``
и ``auditor`` у сессии нет: удалить кейс или прочитать чужой нельзя. Токены случайные, хранятся на сервере
хешем и в браузер не уходят: браузер держит только номер сессии в cookie с HttpOnly и SameSite=Strict.

Имя учётной записи несёт метку сессии, поэтому владение кейсом в API разводит сессии: кейс одной сессии другой
отвечает 404. Число сессий, их срок и число кейсов ограничены: стенд открыт без входа.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mandat.api.principals import Directory, Principal, UserEntry, hash_token

# Получатель удостоверений стенда: для него выпускает демо-издатель и только его принимает учётная запись dbo.
AUDIENCE = "stand-dbo-msb"
REVIEWER_SPECIALIZATION = "lawyer"
SESSION_ROLES = ("operator", "reviewer", "dbo")
MAX_SESSIONS = 300
IDLE_SECONDS = 2 * 3600
LIFETIME_SECONDS = 8 * 3600
MAX_CASES = 30
# Номер сессии в cookie: 43 знака base64url. Иное значение — не номер, а мусор, и в словарь не идёт.
SESSION_ID_CHARS = 43


@dataclass
class DboState:
    """Удостоверение сессии для вкладки «ДБО»: на какой кейс, сумму и одноразовый nonce выпущено."""

    credential: dict[str, Any]
    nonce: str
    index: int
    case_id: str
    amount_rub: int
    organization_inn: str
    representative_inn: str
    operation: str
    operation_date: str
    suspended: bool = False


@dataclass
class Session:
    sid: str
    tag: str
    tokens: dict[str, str]
    created: float
    seen: float
    cases: list[dict[str, str]] = field(default_factory=list)
    # Документы последней загрузки: консультант строит по ним кейс заново. Демо-кейс хранится одним именем.
    sources: dict[str, Any] = field(default_factory=dict)
    dbo: DboState | None = None
    issued: int = 0

    def principal_name(self, role: str) -> str:
        return f"stand-{self.tag}-{role}"

    def token(self, role: str) -> str:
        return self.tokens[role]

    def remember(self, case_id: str, title: str, source: Any) -> None:
        """Кейс сессии: новые сверху, не больше ``MAX_CASES``; источник — для консультанта."""
        self.cases = [c for c in self.cases if c["case_id"] != case_id]
        self.cases.insert(0, {"case_id": case_id, "title": title[:200]})
        del self.cases[MAX_CASES:]
        self.sources[case_id] = source
        for stale in [k for k in self.sources if k not in {c["case_id"] for c in self.cases}]:
            del self.sources[stale]

    def owns(self, case_id: str) -> bool:
        return any(c["case_id"] == case_id for c in self.cases)


class Sessions:
    """Сессии консоли в памяти процесса. Стенд работает одним воркером: сессии не делятся между процессами."""

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        idle_seconds: float = IDLE_SECONDS,
        lifetime_seconds: float = LIFETIME_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_sessions, self.idle, self.lifetime, self.clock = max_sessions, idle_seconds, lifetime_seconds, clock
        self._by_sid: OrderedDict[str, Session] = OrderedDict()
        self._by_token: dict[str, tuple[Principal, str]] = {}
        self._lock = threading.Lock()

    def _alive(self, session: Session, now: float) -> bool:
        return now - session.seen < self.idle and now - session.created < self.lifetime

    def _drop(self, sid: str) -> None:
        session = self._by_sid.pop(sid, None)
        if session is not None:
            for token in session.tokens.values():
                self._by_token.pop(hash_token(token), None)

    def _sweep(self, now: float) -> None:
        for sid in [sid for sid, s in self._by_sid.items() if not self._alive(s, now)]:
            self._drop(sid)

    def create(self) -> Session:
        now = self.clock()
        with self._lock:
            self._sweep(now)
            while len(self._by_sid) >= self.max_sessions:
                self._drop(next(iter(self._by_sid)))  # самая давняя по последнему обращению
            tag = secrets.token_hex(5)
            session = Session(
                sid=secrets.token_urlsafe(32),
                tag=tag,
                tokens={role: secrets.token_urlsafe(32) for role in SESSION_ROLES},
                created=now,
                seen=now,
            )
            self._by_sid[session.sid] = session
            for role, token in session.tokens.items():
                self._by_token[hash_token(token)] = (self._principal(session, role), session.sid)
            return session

    @staticmethod
    def _principal(session: Session, role: str) -> Principal:
        if role == "reviewer":
            return Principal(session.principal_name(role), role, REVIEWER_SPECIALIZATION)
        if role == "dbo":
            return Principal(session.principal_name(role), role, None, (AUDIENCE,))
        return Principal(session.principal_name(role), role)

    def get(self, sid: str | None) -> Session | None:
        """Сессия по номеру из cookie; обращение продлевает её до ``idle``, но не дольше ``lifetime``."""
        if not sid or len(sid) != SESSION_ID_CHARS:
            return None
        now = self.clock()
        with self._lock:
            found = next((s for key, s in self._by_sid.items() if hmac.compare_digest(key, sid)), None)
            if found is None:
                return None
            if not self._alive(found, now):
                self._drop(found.sid)
                return None
            found.seen = now
            self._by_sid.move_to_end(found.sid)
            return found

    def principal(self, token: str) -> Principal | None:
        """Учётная запись по токену. Токен истёкшей или вытесненной сессии не действует."""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self.clock()
        with self._lock:
            found = self._by_token.get(digest)
            if found is None:
                return None
            principal, sid = found
            session = self._by_sid.get(sid)
            if session is None or not self._alive(session, now):
                return None
            return principal

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_sid)


class StandDirectory(Directory):
    """Каталог учётных записей стенда: сессии консоли и пользователи из ``MANDAT_API_USERS``.

    Общий токен ``MANDAT_API_TOKEN`` (роль admin) на стенде не действует: консоль открыта без входа, и
    безымянного администратора рядом с ней быть не должно. Режим всегда многопользовательский: ``case_id``
    выдаёт сервер, кейс видит только создавшая его учётная запись.
    """

    def __init__(self, users: tuple[UserEntry, ...], sessions: Sessions, *, reload_path: Path | None = None) -> None:
        super().__init__(users, "", reload_path=reload_path)
        self._had_users = True
        self.legacy_token = ""
        self.sessions = sessions

    @property
    def configured(self) -> bool:
        return True

    def find(self, token: str) -> Principal | None:
        return self.sessions.principal(token) or super().find(token)
