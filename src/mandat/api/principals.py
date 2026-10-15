"""Пользователи API: токен → имя и роль.

Токены хранятся только хешем SHA-256, сам токен знает лишь его владелец. Список задаётся
переменной ``MANDAT_API_USERS``: JSON-массив или путь к JSON-файлу с таким массивом::

    [{"name": "ivanova", "role": "operator", "token_sha256": "<64 hex>",
      "expires_at": "2027-01-31T00:00:00+00:00", "revoked": false}]

Хеш и новый токен печатает ``mandat api-user --name ivanova --role operator --expires-in-days 90``.

Жизненный цикл токена (M-12): ``expires_at`` (ISO 8601 с часовым поясом) — после этого момента токен
не принимается; ``revoked: true`` — токен не принимается сразу. Проверка срока и отзыва действует в
ЛЮБОМ режиме, если поля заданы. Обязательность ``expires_at`` (и потолок срока ``MAX_TOKEN_TTL_DAYS``
от момента загрузки списка) — только в pilot/prod и любом защищённом режиме: иначе список не
загружается и ``mandat serve`` не стартует. ``expires_at: null`` равно отсутствию поля. Отозванную запись
потолок срока не проверяет (она всё равно не действует). Время берётся с часов сервера без допуска
на расхождение: часы должны вести NTP. Если ``MANDAT_API_USERS`` — путь к файлу,
каталог перечитывает его при смене файла (отзыв без рестарта); файл нужно обновлять только записью
во временный файл и ``rename`` (атомарно), не правкой на месте; нечитаемый файл закрывает доступ
всем (fail closed), а не оставляет последний удачный список. Общий ``MANDAT_API_TOKEN`` срока жизни
не имеет — это режим стенда. Это не OIDC и не сессии: проверка идёт по хешу при каждом запросе.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Кто что может. Видеть чужие кейсы могут только аудитор и администратор.
ROLES = {
    "operator": {"write": True, "see_all": False},
    "reviewer": {"write": True, "see_all": False},
    "auditor": {"write": False, "see_all": True},
    "admin": {"write": True, "see_all": True},
}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str

    @property
    def can_write(self) -> bool:
        return bool(ROLES[self.role]["write"])

    @property
    def sees_all(self) -> bool:
        return bool(ROLES[self.role]["see_all"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


MAX_TOKEN_TTL_DAYS = 366
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserEntry:
    principal: Principal
    token_sha256: str
    expires_at: datetime | None = None
    revoked: bool = False

    def usable(self, now: datetime) -> bool:
        """Токен действует: не отозван и срок не вышел (граница ``expires_at`` уже не действует)."""
        if self.revoked:
            return False
        return self.expires_at is None or now < self.expires_at


def _parse_expiry(name: str, value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"MANDAT_API_USERS: у {name} expires_at должен быть строкой ISO 8601")
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"MANDAT_API_USERS: у {name} expires_at не читается как ISO 8601") from exc
    if moment.tzinfo is None:
        raise ValueError(f"MANDAT_API_USERS: у {name} expires_at без часового пояса")
    return moment.astimezone(UTC)


def parse_users(raw: Any, *, require_expiry: bool | None = None, now: datetime | None = None) -> tuple[UserEntry, ...]:
    if require_expiry is None:
        from mandat.services.env_policy import protected

        require_expiry = protected()
    if not isinstance(raw, list):
        raise ValueError("MANDAT_API_USERS: ожидался JSON-массив пользователей")
    ceiling = (now or datetime.now(UTC)) + timedelta(days=MAX_TOKEN_TTL_DAYS)
    users: list[UserEntry] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("MANDAT_API_USERS: каждый пользователь задаётся объектом")
        name, role, digest = (
            str(item.get("name") or ""),
            str(item.get("role") or ""),
            str(item.get("token_sha256") or ""),
        )
        if not name or role not in ROLES:
            raise ValueError(f"MANDAT_API_USERS: у пользователя {name or '?'} нет имени или роль не из {sorted(ROLES)}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError(f"MANDAT_API_USERS: у {name} token_sha256 не похож на SHA-256")
        if name in seen:
            raise ValueError(f"MANDAT_API_USERS: имя {name} встречается дважды")
        if digest.lower() in {entry.token_sha256 for entry in users}:
            # Один токен у двух имён: запросы и записи журнала достались бы не тому пользователю.
            raise ValueError(f"MANDAT_API_USERS: токен {name} совпадает с токеном другого пользователя")
        revoked = item.get("revoked", False)
        if not isinstance(revoked, bool):
            raise ValueError(f"MANDAT_API_USERS: у {name} revoked должен быть true или false")
        expires: datetime | None = None
        if item.get("expires_at") is not None:
            expires = _parse_expiry(name, item["expires_at"])
        elif require_expiry:
            raise ValueError(f"MANDAT_API_USERS: у {name} нет expires_at (обязателен в pilot/prod)")
        if expires is not None and not revoked and expires > ceiling:
            message = f"у {name} expires_at позже {MAX_TOKEN_TTL_DAYS} дн. от сегодня"
            if require_expiry:
                raise ValueError(f"MANDAT_API_USERS: {message} (потолок срока в pilot/prod)")
            log.warning("MANDAT_API_USERS: %s", message)
        seen.add(name)
        users.append(UserEntry(Principal(name, role), digest.lower(), expires, revoked))
    return tuple(users)


def load_users(value: str | None = None) -> tuple[UserEntry, ...]:
    text = (value if value is not None else os.environ.get("MANDAT_API_USERS", "")).strip()
    if not text:
        return ()
    if not text.startswith("["):
        text = Path(text).read_text(encoding="utf-8")
    return parse_users(json.loads(text))


class Directory:
    """Ищет пользователя по токену. Один ``MANDAT_API_TOKEN`` без списка — режим стенда: администратор.

    Если задан ``MANDAT_API_USERS``, ``MANDAT_API_TOKEN`` не действует. Отозванные и просроченные
    токены не находятся. Если список пришёл из файла (``reload_path``), он перечитывается при смене
    файла; ошибка чтения закрывает доступ всем пользователям (fail closed).
    """

    def __init__(
        self,
        users: tuple[UserEntry, ...],
        legacy_token: str = "",
        *,
        reload_path: Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.users = users
        # Со списком пользователей общий токен не работает: иначе рядом с ролями жил бы безымянный администратор.
        self._had_users = bool(users) or reload_path is not None
        self.legacy_token = "" if self._had_users else legacy_token.strip()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reload_path = reload_path
        # Метка пуста: первый же запрос перечитает файл, так что правка между load_users и стартом не теряется.
        self._digest: str | None = None
        self._stamp: tuple[int, ...] | None = None
        self._lock = threading.Lock()

    # Файл считается «свежим», пока его mtime моложе этого окна: грубая гранулярность mtime и правка на
    # месте того же размера не должны оставить отозванный токен в силе, поэтому в окне сверяется хеш содержимого.
    RECENT_SECONDS = 2.0

    def _file_stamp(self) -> tuple[int, ...]:
        assert self._reload_path is not None
        try:
            st = self._reload_path.stat()
        except OSError:
            return (-1,)
        return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)

    def _refresh(self) -> None:
        if self._reload_path is None:
            return
        stamp = self._file_stamp()
        if stamp == self._stamp and not self._recent(stamp):
            return
        with self._lock:
            stamp = self._file_stamp()
            if stamp == self._stamp and not self._recent(stamp):
                return
            try:
                raw = self._reload_path.read_bytes()
            except OSError:
                raw = None
            digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
            if stamp == self._stamp and digest == self._digest:
                return  # метка та же и содержимое то же
            try:
                if raw is None:
                    raise OSError("нет файла")
                self.users = parse_users(json.loads(raw.decode("utf-8")))
            except (ValueError, OSError):
                self.users = ()  # fail closed: нечитаемый список не оставляет прежних токенов в силе
            self._stamp, self._digest = stamp, digest

    @staticmethod
    def _recent(stamp: tuple[int, ...]) -> bool:
        return len(stamp) > 2 and time.time() - stamp[2] / 1e9 < Directory.RECENT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.users or self.legacy_token or self._had_users)

    @property
    def multi_user(self) -> bool:
        return self._had_users

    def find(self, token: str) -> Principal | None:
        self._refresh()
        presented = hash_token(token)
        now = self._clock()
        found = None
        for entry in self.users:
            if hmac.compare_digest(presented, entry.token_sha256) and entry.usable(now):
                found = entry.principal
        if self.legacy_token and hmac.compare_digest(hash_token(token), hash_token(self.legacy_token)):
            found = Principal("default", "admin")
        return found


MIN_LEGACY_TOKEN_BYTES = 32


def access_problem() -> str | None:
    """Почему API нельзя запускать с текущим окружением. ``None``, если доступ настроен."""
    try:
        users = load_users()
    except (ValueError, OSError) as exc:
        return f"MANDAT_API_USERS не читается: {exc}"
    if not users and not os.environ.get("MANDAT_API_TOKEN", "").strip():
        return "задайте MANDAT_API_TOKEN (режим стенда) или MANDAT_API_USERS (пользователи с ролями)"
    from mandat.services.env_policy import protected

    token = os.environ.get("MANDAT_API_TOKEN", "").strip()
    if not users and protected() and len(token.encode("utf-8")) < MIN_LEGACY_TOKEN_BYTES:
        # Общий токен даёт роль admin: в pilot и prod короткий токен перебирается.
        return f"MANDAT_API_TOKEN короче {MIN_LEGACY_TOKEN_BYTES} байт: в защищённом режиме нужен длинный токен"
    return None
