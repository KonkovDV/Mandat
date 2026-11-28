"""Вызов маршрута HTTP API изнутри процесса, со всеми его проверками.

Консоль не повторяет логику API: она отправляет запрос в то же ASGI-приложение, как это сделал бы клиент, с токеном
демо-учётной записи сессии. Запрос проходит звено ``Guard`` (лимит частоты, токен, размер и глубина тела), роли,
владение кейсом, журнал аудита и строгий JSON. Лимит частоты считается по сессии: адрес клиента в запросе —
метка сессии, а не общий адрес стенда.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from mandat.strictjson import strict_loads

AsgiApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[MutableMapping[str, Any]]], Callable[..., Awaitable[None]]],
    Awaitable[None],
]


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        try:
            return strict_loads(self.body)
        except (ValueError, UnicodeDecodeError):
            return {"detail": "ответ API не JSON"}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


async def call(
    app: AsgiApp,
    method: str,
    path: str,
    *,
    token: str,
    client: str,
    body: Any = None,
    query: dict[str, str] | None = None,
) -> Reply:
    """Один запрос к API внутри процесса. Исключение маршрута — ответ 500, а не падение консоли."""
    payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"host", b"stand.internal"),
        (b"authorization", f"Bearer {token}".encode("latin-1")),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": urlencode(query or {}).encode("ascii"),
        "root_path": "",
        "headers": headers,
        "client": (client, 0),
        "server": ("stand.internal", 80),
    }
    delivered = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    status = 500
    chunks: list[bytes] = []
    reply_headers: dict[str, str] = {}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
            for key, value in message.get("headers", []):
                reply_headers[key.decode("latin-1").lower()] = value.decode("latin-1")
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))

    try:
        await app(scope, receive, send)
    except Exception:  # noqa: BLE001 — обработчик ошибок приложения уже отправил 500 и пробросил исключение дальше
        if not chunks:
            status = 500
    return Reply(status, b"".join(chunks), reply_headers)
