"""Сетевой вызов модели есть только в ``advisory/llm.py`` и принимает только ``RedactedMessages``.

Статическая проверка по AST модулей ``src``, ``scripts`` и ``tools``:

* SDK моделей (openai, gigachat, litellm и т. п.) не импортируются нигде;
* вне ``llm.py`` нет адресов API моделей (``/chat/completions`` и др.) и переменных ``MANDAT_LLM_*``,
  нет импорта клиента модели и печати ``_SEAL``;
* HTTP-клиенты (urllib.request, http.client, requests, httpx, aiohttp, socket) импортируют только модули из
  ``HTTP_ALLOWED`` с указанной целью: новый сетевой модуль не пройдёт CI, пока его не добавят сюда явно.

Строки документации и комментарии не проверяются: упоминание адреса в тексте — не вызов.
"""

from __future__ import annotations

import ast
import copy
import pickle
from pathlib import Path

import pytest

from mandat.advisory import llm
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline

ROOT = Path(__file__).resolve().parents[1]
LLM_MODULE = Path("src/mandat/advisory/llm.py")
SCANNED = ("src", "scripts", "tools")
LLM_SDKS = {
    "openai",
    "gigachat",
    "langchain",
    "langchain_core",
    "langchain_community",
    "litellm",
    "llama_cpp",
    "vllm",
    "ollama",
    "mistralai",
    "cohere",
    "transformers",
    "yandex_cloud_ml_sdk",
    "google.generativeai",
    "google.genai",
}
LLM_ENDPOINTS = ("/chat/completions", "/v1/completions", "/v1/messages", "/embeddings", "/api/generate", "/api/chat")
LLM_ONLY_NAMES = {"OpenAICompatClient", "RedactedMessages", "_SEAL", "_require_redacted"}
HTTP_CLIENTS = {"urllib.request", "http.client", "requests", "httpx", "aiohttp", "socket", "urllib3"}
# Модули с сетевыми вызовами не к модели и их цель.
HTTP_ALLOWED = {
    "src/mandat/advisory/llm.py": "модель",
    "src/mandat/api/nodelay.py": "TCP_NODELAY на сокете сервера API",
    "src/mandat/audit/anchor.py": "якорь журнала (RFC 3161 TSA)",
    "src/mandat/audit/revocation.py": "OCSP и CRL",
    "src/mandat/audit/signing.py": "метка времени подписи",
    "src/mandat/audit/tlog.py": "журнал прозрачности (Rekor)",
    "scripts/pilot_smoke.py": "проверка стенда по HTTP",
    "scripts/tlog_live_check.py": "живая проверка журнала прозрачности",
    "tools/load/run_load.py": "нагрузочный тест API",
}


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for stmt in body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                    ids.add(id(stmt.value))
    return ids


def _matches(module: str, names: set[str]) -> str | None:
    for name in names:
        if module == name or module.startswith(name + "."):
            return name
    return None


def violations(source: str, rel: str) -> list[str]:
    """Нарушения в одном модуле; ``rel`` — путь от корня репозитория."""
    tree = ast.parse(source)
    is_llm = rel == LLM_MODULE.as_posix()
    docs = _docstrings(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            if node.module == "mandat.advisory.llm" and not is_llm:
                out += [f"{rel}: импорт {a.name} из llm" for a in node.names if a.name in LLM_ONLY_NAMES]
        for module in modules:
            if _matches(module, LLM_SDKS):
                out.append(f"{rel}: SDK модели {module}")
            if _matches(module, HTTP_CLIENTS) and rel not in HTTP_ALLOWED:
                out.append(f"{rel}: HTTP-клиент {module} вне HTTP_ALLOWED")
        if is_llm:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if any(e in node.value for e in LLM_ENDPOINTS):
                out.append(f"{rel}: адрес API модели {node.value!r}")
            if "MANDAT_LLM_" in node.value:
                out.append(f"{rel}: настройка модели {node.value!r}")
        if isinstance(node, ast.Attribute) and node.attr in LLM_ONLY_NAMES:
            out.append(f"{rel}: обращение к {node.attr}")
    return sorted(set(out))


def _files() -> list[Path]:
    return sorted(p for top in SCANNED for p in (ROOT / top).rglob("*.py") if ".venv" not in p.parts)


def test_only_llm_module_talks_to_the_model():
    found = [v for p in _files() for v in violations(p.read_text(encoding="utf-8"), p.relative_to(ROOT).as_posix())]
    assert found == []


def test_http_allowlist_is_current():
    # Модуль убрали или перестал ходить в сеть — убрать из списка, чтобы список не разрастался.
    for rel in HTTP_ALLOWED:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert any(_matches(m, HTTP_CLIENTS) for m in imported), rel


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import openai", "SDK модели openai"),
        ("from langchain_core.messages import HumanMessage", "SDK модели langchain_core"),
        ("import google.generativeai as genai", "SDK модели google.generativeai"),
        ("import urllib.request", "HTTP-клиент urllib.request"),
        ("from urllib import request", "HTTP-клиент urllib.request"),
        ("import httpx", "HTTP-клиент httpx"),
        ("URL = base + '/chat/completions'", "адрес API модели"),
        ("import os\nos.environ['MANDAT_LLM_BASE_URL']", "настройка модели"),
        ("from mandat.advisory.llm import OpenAICompatClient", "импорт OpenAICompatClient"),
        ("from mandat.advisory import llm\nllm.OpenAICompatClient.from_env()", "обращение к OpenAICompatClient"),
        ("from mandat.advisory import llm\nllm.RedactedMessages([], _seal=llm._SEAL)", "обращение к _SEAL"),
    ],
)
def test_scanner_catches(source, expected):
    assert any(expected in v for v in violations(source, "src/mandat/new_module.py"))


@pytest.mark.parametrize(
    "source",
    [
        '"""Модель вызывается через /chat/completions в llm.py."""',
        "def f():\n    '''MANDAT_LLM_BASE_URL задаёт адрес.'''\n    return 1",
        "from urllib.parse import urlparse",
        "import http.server",
        "complete = True\nresult = task.complete()",
        "from mandat.advisory.llm import case_charter_messages, parse_facts",
        "path = '/v1/cases'",
    ],
)
def test_scanner_has_no_false_positives(source):
    assert violations(source, "src/mandat/new_module.py") == []


def test_llm_module_itself_may_call_the_model():
    assert violations((ROOT / LLM_MODULE).read_text(encoding="utf-8"), LLM_MODULE.as_posix()) == []


# --- RedactedMessages ---


def _sealed() -> llm.RedactedMessages:
    return llm.case_charter_messages(load_case_inline(synth.build("ok", 1).inline_manifest()))


@pytest.mark.parametrize(
    "raw",
    ["текст", [{"role": "user", "content": "x"}], ({"role": "user", "content": "x"},), None],
)
def test_client_rejects_anything_but_redacted_messages(raw):
    client = llm.OpenAICompatClient("http://127.0.0.1:8000/v1", "local")
    with pytest.raises(TypeError, match="RedactedMessages"):
        client.complete(raw)
    with pytest.raises(TypeError, match="RedactedMessages"):
        client.body(raw)


def test_redacted_messages_cannot_be_built_or_forged():
    with pytest.raises(TypeError):
        llm.RedactedMessages([{"role": "user", "content": "x"}])
    with pytest.raises(TypeError):
        llm.RedactedMessages([{"role": "user", "content": "x"}], _seal=object())

    class Fake(llm.RedactedMessages):
        pass

    with pytest.raises(TypeError):
        Fake([], _seal=None)
    sealed = _sealed()
    with pytest.raises(AttributeError):
        sealed._messages = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        pickle.dumps(sealed)
    assert copy.deepcopy(sealed) is sealed
    view = sealed.messages
    view[1]["content"] = "подмена"
    assert sealed.messages[1]["content"] != "подмена"


def test_only_case_charter_messages_produces_redacted_messages():
    assert type(_sealed()) is llm.RedactedMessages
    assert type(llm.charter_messages("Директор подписывает сделки")) is list
    body = llm.OpenAICompatClient("http://127.0.0.1:8000/v1", "local").body(_sealed())
    assert body["messages"][0]["content"] == llm.CHARTER_PROMPT
