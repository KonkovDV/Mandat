"""Пользователи API: токен → имя и роль.

Токены хранятся только хешем SHA-256, сам токен знает лишь его владелец. Список задаётся
переменной ``MANDAT_API_USERS``: JSON-массив или путь к JSON-файлу с таким массивом::

    [{"name": "ivanova", "role": "operator", "token_sha256": "<64 hex>"}]

Хеш и новый токен печатает ``mandat api-user --name ivanova --role operator``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class UserEntry:
    principal: Principal
    token_sha256: str


def parse_users(raw: Any) -> tuple[UserEntry, ...]:
    if not isinstance(raw, list):
        raise ValueError("MANDAT_API_USERS: ожидался JSON-массив пользователей")
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
        seen.add(name)
        users.append(UserEntry(Principal(name, role), digest.lower()))
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

    Если задан ``MANDAT_API_USERS``, ``MANDAT_API_TOKEN`` не действует.
    """

    def __init__(self, users: tuple[UserEntry, ...], legacy_token: str = ""):
        self.users = users
        # Со списком пользователей общий токен не работает: иначе рядом с ролями жил бы безымянный администратор.
        self.legacy_token = "" if users else legacy_token.strip()

    @property
    def configured(self) -> bool:
        return bool(self.users or self.legacy_token)

    @property
    def multi_user(self) -> bool:
        return bool(self.users)

    def find(self, token: str) -> Principal | None:
        presented = hash_token(token)
        found = None
        for entry in self.users:
            if hmac.compare_digest(presented, entry.token_sha256):
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
