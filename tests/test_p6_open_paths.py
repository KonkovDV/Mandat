"""P6-04: открытые пути не читают тело запроса.

``/health`` и ``/readyz`` открыты без токена и без лимита частоты. ``Guard`` читал у них тело любого не-GET
запроса до 8 МБ, хотя оба пути отвечают только на GET: анонимный клиент без ограничения заставлял сервер
принимать и держать в памяти мегабайты (OWASP API4:2023). Теперь другой метод на открытом пути — 405 до чтения
тела.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.principals import Directory
from mandat.api.storage import MemoryStore

TOKEN = "t" * 40


def _guard_call(method: str, path: str, body: bytes) -> tuple[int, int]:
    """Статус ответа ``Guard`` и сколько раз он прочитал тело."""
    reads = 0
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def app(scope, receive, send) -> None:  # приложение за Guard: на открытом пути отвечает 200
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    guard = api.Guard(app, directory=Directory((), TOKEN), limit=120, store=MemoryStore())
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-length", str(len(body)).encode())],
        "client": ("203.0.113.7", 4000),
    }
    asyncio.run(guard(scope, receive, send))
    return sent[0]["status"], reads


def test_open_paths_answer_get_without_reading_a_body():
    assert _guard_call("GET", "/health", b"") == (200, 0)
    assert _guard_call("GET", "/readyz", b"") == (200, 0)


def test_other_methods_on_open_paths_are_refused_before_the_body_is_read():
    for path in ("/health", "/readyz"):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, reads = _guard_call(method, path, b"x" * 1_000_000)
            assert (status, reads) == (405, 0), (method, path)


def test_open_path_refusal_names_the_allowed_methods():
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore()))
    answer = client.post("/health", content=b"x" * 1024)
    assert answer.status_code == 405
    assert answer.headers["allow"] == "GET, HEAD"
    assert answer.headers["x-content-type-options"] == "nosniff"
