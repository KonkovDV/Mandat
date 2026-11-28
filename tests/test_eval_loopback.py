"""Офлайн-оценка модели (``eval_complete``): https, а http — только к модели на этой же машине.

Правило то же, что у клиента продукта с ``MANDAT_LLM_ALLOW_INSECURE_LOOPBACK``: 127.0.0.1, localhost, ::1.
Запрос к loopback идёт мимо прокси окружения, в защищённых режимах оценка не запускается вовсе.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from mandat.advisory import llm

MESSAGES = [{"role": "user", "content": "Директор подписывает сделки"}]


class _LocalModel(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict[str, Any]]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seen.append((self.path, body))
        reply = json.dumps({"choices": [{"message": {"content": "ответ"}}], "usage": {"total_tokens": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def local_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    _LocalModel.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("MANDAT_ENV", "dev")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    # Недоступный прокси окружения без исключений для loopback: запрос через него не дошёл бы до модели.
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_loopback_http_reaches_the_local_model_past_the_env_proxy(local_model: int):
    out = llm.eval_complete(f"http://127.0.0.1:{local_model}/v1", "t-lite", "", MESSAGES, timeout=5)
    assert out == {"text": "ответ", "usage": {"total_tokens": 3}}
    path, body = _LocalModel.seen[0]
    assert path == "/v1/chat/completions"
    assert (body["model"], body["temperature"], body["messages"]) == ("t-lite", 0, MESSAGES)


@pytest.mark.parametrize("host", ["localhost", "[::1]"])
def test_other_loopback_names_pass_the_scheme_check(monkeypatch: pytest.MonkeyPatch, host: str):
    monkeypatch.setenv("MANDAT_ENV", "dev")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "localhost,::1")
    # Порт закрыт: схема и хост приняты, отказ приходит уже от сети, а не ValueError.
    with pytest.raises(OSError):
        llm.eval_complete(f"http://{host}:9/v1", "m", "", MESSAGES, timeout=2)


@pytest.mark.parametrize(
    "url",
    [
        "http://model.internal/v1",
        "http://10.0.0.5:11434/v1",
        "http://127.0.0.1.nip.io/v1",
        "http://openrouter.ai/api/v1",
        "ftp://127.0.0.1/v1",
    ],
)
def test_http_is_refused_unless_loopback(monkeypatch: pytest.MonkeyPatch, url: str):
    monkeypatch.setenv("MANDAT_ENV", "dev")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "model.internal,10.0.0.5,127.0.0.1.nip.io,openrouter.ai,127.0.0.1")
    with pytest.raises(ValueError, match="https"):
        llm.eval_complete(url, "m", "k", MESSAGES)


def test_loopback_still_needs_an_allowed_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "dev")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "openrouter.ai")
    with pytest.raises(ValueError, match="MANDAT_LLM_ALLOWED_HOSTS"):
        llm.eval_complete("http://127.0.0.1:11434/v1", "m", "", MESSAGES)


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_protected_modes_refuse_even_loopback(monkeypatch: pytest.MonkeyPatch, env: str):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", "1")
    with pytest.raises(llm.LlmDisabled):
        llm.eval_complete("http://127.0.0.1:11434/v1", "m", "", MESSAGES)
