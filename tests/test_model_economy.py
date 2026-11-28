"""Учёт модели советника: предохранитель, дневной бюджет по Москве, кэш принятых ответов, счётчики и их показ.

Модель — подставной OpenAI-совместимый сервер на 127.0.0.1, настоящая сеть не нужна. Часы подставные: пауза
предохранителя, срок кэша и смена дня бюджета считаются без ожидания.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
import time
import urllib.error
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from mandat.advisory import economy, llm
from mandat.advisory.economy import AnswerCache, Breaker, DailyBudget, Economy, ModelPaused, Settings, Usage
from mandat.api.app import create_app
from mandat.api.storage import MemoryStore
from mandat.console import advisor
from mandat.console.demo import demo_manifest
from mandat.intake.case_loader import load_case_inline

MSK = economy.MOSCOW
SECRET = "sk-or-v1-0000-test-key-never-shown"


class Clock:
    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def _at(moment: str) -> float:
    """Момент по московскому времени, например «2026-11-28T23:59:30»."""
    return datetime.fromisoformat(moment).replace(tzinfo=MSK).timestamp()


def _case(name: str = "09_charter_limit"):
    return load_case_inline(demo_manifest(name))


# --- предохранитель ------------------------------------------------------------------------------------------


def test_breaker_opens_after_consecutive_failures_and_lets_one_probe_through():
    breaker = Breaker(3, 120)
    assert breaker.allow(0) and not breaker.failure(0) and not breaker.failure(1)
    breaker.success()  # удача обнуляет счёт: нужны три неудачи подряд
    assert [breaker.failure(t) for t in (2, 3, 4)] == [False, False, True]
    assert breaker.state(4) == "open" and not breaker.allow(60) and breaker.retry_in(64) == 60
    assert not breaker.failure(100)  # неудача вызова, начатого раньше, паузу не продлевает
    assert breaker.state(124) == "half_open" and breaker.retry_in(124) is None
    assert breaker.allow(124) is True  # пробный вызов
    assert breaker.allow(125) is False  # остальные ждут его исхода
    assert breaker.failure(126) is True and breaker.state(127) == "open"  # пробный неудачен: снова пауза
    assert breaker.allow(246) is True
    breaker.success()
    assert breaker.state(247) == "closed" and breaker.allow(247) and breaker.failures == 0


def test_breaker_replaces_a_probe_that_never_reported():
    breaker = Breaker(1, 10)
    assert breaker.failure(0) is True
    assert breaker.allow(10) and not breaker.allow(19)
    assert breaker.allow(20)  # исход первого пробного вызова так и не записали: через срок — новый


def test_economy_refuses_while_open_and_counts_opens():
    clock = Clock(1000.0)
    meter = Economy(Settings(breaker_failures=2, breaker_seconds=60), clock=clock, monotonic=clock)
    for _ in range(2):
        meter.admit()
        meter.failed("network")
    with pytest.raises(ModelPaused) as paused:
        meter.admit()
    assert paused.value.reason == "breaker" and "через 60 с" in paused.value.note
    assert "только правила" in paused.value.note and meter.paused() is not None
    clock.tick(60)
    assert meter.paused() is None
    meter.admit()  # пробный вызов
    with pytest.raises(ModelPaused, match="пробным вызовом"):
        meter.admit()
    meter.failed("ungrounded")
    snap = meter.snapshot()
    assert snap["breaker_opens"] == 2 and snap["breaker"]["state"] == "open"
    assert snap["breaker"]["retry_in_seconds"] == 60 and snap["refused"] == {"breaker": 2}
    assert snap["failure_kinds"] == {"network": 2, "ungrounded": 1} and snap["calls"] == 3


# --- дневной бюджет ------------------------------------------------------------------------------------------


def test_budget_cuts_the_model_off_until_moscow_midnight_and_the_next_day_starts_fresh():
    clock = Clock(_at("2026-11-28T23:58:00"))
    meter = Economy(Settings(budget_usd_per_day=0.01), clock=clock, monotonic=clock)
    meter.admit()
    meter.charge(Usage(1200, 300, 0.006))
    meter.admit()  # 0,006 из 0,01: ещё можно
    meter.charge(Usage(1200, 300, 0.006))
    with pytest.raises(ModelPaused) as paused:
        meter.admit()
    assert paused.value.reason == "budget" and "до 00:00 по Москве ответ дают только правила" in paused.value.note
    assert paused.value.until == datetime(2026, 11, 29, tzinfo=MSK)
    today = meter.snapshot()["today"]
    assert today["exhausted"] is True and today["left_usd"] == 0 and today["day"] == "2026-11-28"
    assert today["resumes_at"] == "2026-11-29T00:00:00+03:00"
    clock.now = _at("2026-11-29T00:00:01")
    meter.admit()
    snap = meter.snapshot()
    assert snap["today"]["day"] == "2026-11-29" and snap["today"]["cost_usd"] == 0 and not snap["today"]["exhausted"]
    assert snap["cost_usd"] == pytest.approx(0.012) and snap["calls"] == 3 and snap["refused"] == {"budget": 1}


def test_budget_day_is_moscow_not_utc():
    budget = DailyBudget(1.0)
    before_midnight = datetime(2026, 11, 28, 20, 59, tzinfo=UTC).timestamp()  # 23:59 по Москве
    budget.add(1.0, before_midnight)
    assert budget.exhausted(before_midnight) and budget.left(before_midnight) == 0
    assert not budget.exhausted(before_midnight + 120)  # 21:01 UTC — уже 00:01 следующего дня по Москве


def test_zero_budget_keeps_the_model_off():
    with pytest.raises(ModelPaused):
        Economy(Settings(budget_usd_per_day=0)).admit()


# --- кэш -----------------------------------------------------------------------------------------------------


def test_cache_hit_miss_ttl_and_lru_bound():
    cache = AnswerCache(entries=2, seconds=60)
    assert cache.get("a", 0) is None
    assert cache.put("a", "A", 0) and cache.put("b", "B", 1)
    assert cache.get("a", 2) == "A"  # «a» теперь нужнее «b»
    cache.put("c", "C", 3)  # сверх двух записей уходит давно не нужная «b»
    assert cache.get("b", 4) is None and cache.get("a", 4) == "A" and len(cache) == 2
    assert cache.get("c", 63) is None  # срок 60 с вышел
    assert not cache.put("big", "x" * (economy.MAX_CACHED_CHARS + 1), 5)
    assert not AnswerCache(0, 60).put("a", "A", 0)  # 0 записей — кэш выключен


def test_economy_cache_counts_hits_and_follows_new_limits():
    clock = Clock(0.0)
    meter = Economy(Settings(cache_entries=3, cache_seconds=100), clock=clock, monotonic=clock)
    assert meter.cached("k1") is None
    for key in ("k1", "k2", "k3"):
        assert meter.remember(key, key.upper())
    assert meter.cached("k1") == "K1" and meter.snapshot()["cache_hits"] == 1
    meter.configure(Settings(cache_entries=1, cache_seconds=100))
    assert meter.snapshot()["cache"] == {"entries": 1, "capacity": 1, "ttl_seconds": 100}
    assert meter.cached("k1") == "K1"  # осталась самая нужная запись
    clock.tick(100)
    assert meter.cached("k1") is None and meter.snapshot()["cache"]["entries"] == 0


# --- счётчики ------------------------------------------------------------------------------------------------


def test_counters_are_exact_under_parallel_use():
    meter = Economy(Settings(breaker_failures=10**9, budget_usd_per_day=1000))

    def work() -> None:
        for _ in range(200):
            meter.admit()
            meter.charge(Usage(10, 2, 0.0005))
            meter.failed("network")
            meter.remember("k", "v")
            meter.cached("k")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    snap = meter.snapshot()
    assert snap["calls"] == 1600 and snap["failures"] == 1600 and snap["failure_kinds"] == {"network": 1600}
    assert snap["prompt_tokens"] == 16000 and snap["completion_tokens"] == 3200 and snap["cache_hits"] == 1600
    assert snap["cost_usd"] == pytest.approx(0.8) and snap["today"]["cost_usd"] == pytest.approx(0.8)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"prompt_tokens": 812, "completion_tokens": 64, "cost": 0.000173}, Usage(812, 64, 0.000173)),
        ({"prompt_tokens": 10, "total_tokens": 10}, Usage(10, 0, 0.0)),
        ({"prompt_tokens": True, "completion_tokens": -5, "cost": "0.1"}, Usage()),
        ({"cost": 10**400}, Usage()),
        ({"cost": -1}, Usage()),
        (None, Usage()),
        ([1, 2], Usage()),
    ],
)
def test_usage_takes_only_sane_numbers(raw, expected):
    assert Usage.of(raw) == expected


LLM_SETTINGS = (
    "MANDAT_LLM_BREAKER_FAILURES",
    "MANDAT_LLM_BREAKER_SECONDS",
    "MANDAT_LLM_BUDGET_USD_PER_DAY",
    "MANDAT_LLM_CACHE_ENTRIES",
    "MANDAT_LLM_CACHE_SECONDS",
)


def test_settings_come_from_env_and_bad_values_fall_back_to_defaults(monkeypatch):
    for name in LLM_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    assert llm.economy_settings() == Settings() == Settings(3, 120.0, 1.0, 256, 86400.0)
    for name, value in zip(LLM_SETTINGS, ("5", "30.5", "0.25", "0", "600"), strict=True):
        monkeypatch.setenv(name, value)
    assert llm.economy_settings() == Settings(5, 30.5, 0.25, 0, 600.0)
    for name, bad in zip(LLM_SETTINGS, ("0", "nan", "-1", "много", "inf"), strict=True):
        monkeypatch.setenv(name, bad)
    assert llm.economy_settings() == Settings()
    meter = llm.shared_economy()
    meter.admit()
    monkeypatch.setenv("MANDAT_LLM_BREAKER_FAILURES", "1")
    assert llm.shared_economy() is meter and meter.breaker.threshold == 1 and meter.snapshot()["calls"] == 1


# --- подставная модель ---------------------------------------------------------------------------------------


class _Provider(http.server.BaseHTTPRequestHandler):
    """OpenAI-совместимый сервер: ответ по режиму, расход в ``usage``, счёт запросов."""

    mode = "honest"
    hits = 0

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).hits += 1
        mode = type(self).mode
        if mode == "slow":
            time.sleep(0.5)
        if mode == "fail":
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        line = next((x for x in body["messages"][-1]["content"].splitlines() if "руб" in x), "")
        honest = {"kind": "approval_threshold", "value": 300000, "quote": line, "confidence": 0.9}
        invented = {"kind": "joint_signature", "value": None, "quote": "выдуманная цитата", "confidence": 0.9}
        facts = {"partial": [honest, invented], "ungrounded": [invented], "empty": []}.get(mode, [honest])
        content = "Ограничений не нашёл." if mode == "prose" else json.dumps({"facts": facts}, ensure_ascii=False)
        payload = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 40, "cost": 0.0004},
        }
        data = b"<html>gateway</html>" if mode == "garbage" else json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # клиент с коротким сроком закрыл соединение раньше ответа


@pytest.fixture
def provider(monkeypatch):
    server = _Server(("127.0.0.1", 0), _Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Provider.mode, _Provider.hits = "honest", 0
    _Provider.url = f"http://127.0.0.1:{server.server_port}/v1"
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", _Provider.url)
    monkeypatch.setenv("MANDAT_LLM_MODEL", "stand-model")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("MANDAT_LLM_API_KEY", SECRET)
    for name in LLM_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    yield _Provider
    server.shutdown()
    server.server_close()


def _client(meter: Economy) -> llm.OpenAICompatClient:
    return llm.OpenAICompatClient(_Provider.url, "stand-model", economy=meter)


# --- клиент модели: учёт в единственной точке выхода -----------------------------------------------------------


def test_client_counts_calls_tokens_and_cost_and_refuses_without_network(provider):
    meter = Economy(Settings(budget_usd_per_day=0.0004))
    client = _client(meter)
    assert json.loads(client.complete(llm.case_charter_messages(_case())))["facts"]
    snap = meter.snapshot()
    assert (snap["calls"], snap["prompt_tokens"], snap["completion_tokens"]) == (1, 400, 40)
    assert snap["cost_usd"] == 0.0004 and snap["today"]["exhausted"] is True
    with pytest.raises(ModelPaused):
        client.complete(llm.case_charter_messages(_case()))
    assert provider.hits == 1  # исчерпанный бюджет: запрос в сеть не ушёл


def test_client_failures_are_classified(provider):
    meter = Economy(Settings(breaker_failures=10))
    sealed = llm.case_charter_messages(_case())
    provider.mode = "fail"
    with pytest.raises(urllib.error.HTTPError):
        _client(meter).complete(sealed)
    with pytest.raises(urllib.error.URLError):
        llm.OpenAICompatClient("http://127.0.0.1:9/v1", "m", economy=meter).complete(sealed)
    provider.mode = "garbage"
    with pytest.raises(ValueError):
        _client(meter).complete(sealed)
    provider.mode = "slow"
    with pytest.raises(OSError):
        _client(meter).complete(sealed, timeout=0.05)
    snap = meter.snapshot()
    assert snap["failure_kinds"] == {"format": 1, "http": 1, "network": 1, "timeout": 1} and snap["calls"] == 4
    assert llm.failure_kind(urllib.error.URLError(TimeoutError())) == "timeout"
    assert llm.failure_kind(KeyError("choices")) == "format"


def test_client_without_economy_calls_the_model_as_before(provider):
    client = llm.OpenAICompatClient(provider.url, "m")
    assert client.economy is None and "facts" in client.complete(llm.case_charter_messages(_case()))


def test_request_key_is_sha256_of_the_exact_messages_and_model():
    sealed = llm.case_charter_messages(_case())
    client = llm.OpenAICompatClient("http://127.0.0.1:9/v1", "m")
    exact = json.dumps(
        {"model": "m", "messages": sealed.messages}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert client.request_key(sealed) == hashlib.sha256(exact.encode("utf-8")).hexdigest()
    assert client.request_key(sealed) == client.request_key(llm.case_charter_messages(_case()))
    assert client.request_key(sealed) != llm.OpenAICompatClient("http://127.0.0.1:9/v1", "m2").request_key(sealed)
    assert client.request_key(sealed) != client.request_key(llm.case_charter_messages(_case("02_director_without_poa")))
    with pytest.raises(TypeError):
        client.request_key([{"role": "user", "content": "x"}])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "facts"),
    [
        ('{"facts": []}', []),
        ('Ответ: ```json\n{"facts": [{"kind": "joint_signature"}]}\n```', [{"kind": "joint_signature"}]),
        ('{"facts": ["строка вместо объекта"]}', None),
        ('{"facts": {"kind": "joint_signature"}}', None),
        ('{"fact": []}', None),
        ("Ограничений не нашёл.", None),
        ('{"facts": [' * 70 + "]}" * 70, None),
    ],
)
def test_read_facts_tells_a_well_formed_answer(raw, facts):
    assert llm.read_facts(raw) == facts


def test_parse_facts_still_keeps_what_looks_like_a_fact():
    assert llm.parse_facts('{"facts": ["строка", {"kind": "joint_signature"}]}') == [{"kind": "joint_signature"}]
    assert llm.read_facts('{"facts": ["строка", {"kind": "joint_signature"}]}') is None


# --- консультант: исход ответа, кэш, предохранитель и бюджет --------------------------------------------------


def test_only_answers_the_gate_accepted_whole_are_cached(provider):
    meter = Economy()
    client, case = _client(meter), _case()
    provider.mode = "partial"
    partial = advisor.model_advice(case, client)
    assert len(partial["accepted"]) == 1 and partial["dropped"] == ["цитата не найдена в документе"]
    provider.mode = "ungrounded"
    assert advisor.model_advice(case, client)["accepted"] == []
    provider.mode = "prose"
    assert advisor.model_advice(case, client)["proposed"] == 0
    snap = meter.snapshot()
    assert provider.hits == 3 and snap["cache"]["entries"] == 0
    assert snap["failure_kinds"] == {"non_json": 1, "ungrounded": 1} and snap["breaker"]["failures_in_row"] == 2
    provider.mode = "honest"
    first = advisor.model_advice(case, client)
    assert "cached" not in first and meter.snapshot()["breaker"]["failures_in_row"] == 0
    again = advisor.model_advice(case, client)
    assert again["cached"] is True and again["accepted"] == first["accepted"] and provider.hits == 4
    assert advisor.cached_advice(case, client)["cached"] is True
    empty_case = _case("02_director_without_poa")
    provider.mode = "empty"  # «ограничений нет» — тоже ответ, который шлюз принял целиком
    assert advisor.model_advice(empty_case, client)["proposed"] == 0
    assert advisor.model_advice(empty_case, client)["cached"] is True and provider.hits == 5
    assert meter.snapshot()["cache_hits"] == 3


def test_late_answer_counts_as_timeout_but_is_kept_when_accepted(provider):
    meter = Economy()
    client, case = _client(meter), _case()
    advice = advisor.model_advice(case, client, deadline=0.0)
    assert advice["accepted"] and meter.snapshot()["failure_kinds"] == {"timeout": 1}
    assert advisor.model_advice(case, client, deadline=0.0)["cached"] is True and provider.hits == 1


def test_open_breaker_answers_by_the_rules_at_once_and_the_probe_closes_it(provider):
    clock = Clock(_at("2026-11-28T12:00:00"))
    meter = Economy(Settings(breaker_failures=2, breaker_seconds=60), clock=clock, monotonic=clock)
    client, case = _client(meter), _case()
    provider.mode = "ungrounded"
    advisor.model_advice(case, client)
    advisor.model_advice(case, client)
    started = time.perf_counter()
    paused = advisor.model_advice(case, client)
    assert time.perf_counter() - started < 1.0 and provider.hits == 2
    assert paused["paused"] == "breaker" and "ответ дают только правила" in paused["error"] and "sent" not in paused
    clock.tick(60)
    provider.mode = "honest"
    probe = advisor.model_advice(case, client)
    assert probe["accepted"] and provider.hits == 3 and meter.snapshot()["breaker"]["state"] == "closed"


def test_spent_budget_switches_the_model_off_until_the_next_moscow_day(provider):
    clock = Clock(_at("2026-11-28T23:50:00"))
    meter = Economy(Settings(budget_usd_per_day=0.0008), clock=clock, monotonic=clock)
    client, case = _client(meter), _case()
    provider.mode = "partial"  # такой ответ не кэшируется: каждый вызов идёт к модели
    advisor.model_advice(case, client)
    advisor.model_advice(case, client)
    off = advisor.model_advice(case, client)
    assert off["paused"] == "budget" and "до 00:00 по Москве ответ дают только правила" in off["error"]
    assert provider.hits == 2 and meter.paused() is not None
    provider.mode = "honest"
    clock.now = _at("2026-11-29T00:00:05")
    assert meter.paused() is None and advisor.model_advice(case, client)["accepted"] and provider.hits == 3
    meter.configure(Settings(budget_usd_per_day=0))
    assert advisor.model_advice(case, client)["cached"] is True  # принятый ответ из кэша ничего не стоит


def test_busy_slots_and_cases_the_model_must_not_read(provider, monkeypatch):
    meter = Economy()
    client = _client(meter)
    monkeypatch.setattr(advisor, "_slots", threading.BoundedSemaphore(1))
    advisor._slots.acquire()
    assert advisor.model_advice(_case(), client)["busy"] is True and provider.hits == 0
    advisor._slots.release()
    manifest = demo_manifest("09_charter_limit")
    for doc in manifest["documents"]:
        if doc["kind"] == "charter_text":
            doc["content"] = doc["content"].replace("директором", "дирzктором")
    mixed = load_case_inline(manifest)
    assert "вперемешку" in advisor.model_advice(mixed, client)["error"] and advisor.cached_advice(mixed, client) is None
    no_charter = demo_manifest("09_charter_limit")
    no_charter["documents"] = [d for d in no_charter["documents"] if d["kind"] != "charter_text"]
    assert "нет устава" in advisor.model_advice(load_case_inline(no_charter), client)["error"]
    assert provider.hits == 0 and meter.snapshot()["calls"] == 0


# --- показ: строки блока «Расход модели», /console/api/stats и /health ------------------------------------------


def test_usage_rows_render_each_state():
    clock = Clock(_at("2026-11-28T12:00:00"))
    meter = Economy(
        Settings(breaker_failures=2, breaker_seconds=90, budget_usd_per_day=1), clock=clock, monotonic=clock
    )
    rows = {r["label"]: r["value"] for r in advisor.usage_rows(meter.snapshot())}
    assert rows == {
        "Вызовы модели": "0",
        "Ответы из кэша": "0",
        "Расход сегодня": "0 $",
        "Осталось на день": "1 $ из 1 $",
        "Токены: запрос и ответ": "0 и 0",
        "Предохранитель": "в норме",
    }
    meter.admit()
    meter.charge(Usage(12345, 678, 0.0042))
    meter.failed("timeout")
    meter.remember("k", "v")
    meter.cached("k")
    rows = {r["label"]: r["value"] for r in advisor.usage_rows(meter.snapshot())}
    assert rows["Вызовы модели"] == "1, неудачных 1" and rows["Ответы из кэша"] == "1"
    assert rows["Расход сегодня"] == "0,0042 $" and rows["Осталось на день"] == "0,9958 $ из 1 $"
    assert rows["Токены: запрос и ответ"] == "12 345 и 678"
    assert rows["Предохранитель"] == "в норме, неудач подряд: 1 из 2"
    meter.failed("timeout")
    rows = {r["label"]: r["value"] for r in advisor.usage_rows(meter.snapshot())}
    assert rows["Предохранитель"] == "пауза после сбоев подряд, следующая попытка через 90 с"
    clock.tick(90)
    rows = {r["label"]: r["value"] for r in advisor.usage_rows(meter.snapshot())}
    assert rows["Предохранитель"] == "пробный вызов: модель проверяется после паузы"
    meter.charge(Usage(0, 0, 1.0))
    rows = {r["label"]: r["value"] for r in advisor.usage_rows(meter.snapshot())}
    assert rows["Расход сегодня"] == "1 $"
    assert rows["Осталось на день"] == "0 $ из 1 $: до 00:00 по Москве ответ дают только правила"
    meter.configure(Settings(budget_usd_per_day=2.5))
    rows = {r["label"]: r["value"] for r in advisor.usage_rows(meter.snapshot())}
    assert rows["Осталось на день"] == "1,5 $ из 2,5 $" and rows["Расход сегодня"] == "1 $"
    assert advisor._usd(0.000173) == "0,000173 $" and advisor._usd(0.25) == "0,25 $"


@pytest.fixture
def stand(provider):
    client = TestClient(create_app(console=True, store=MemoryStore(), rate_per_min=0))
    assert client.post("/console/api/session", json={}).status_code == 200
    return client


def _consult(client: TestClient, demo: str = "09_charter_limit") -> dict:
    case_id = client.post("/console/api/check", json={"demo": demo}).json()["case_id"]
    reply = client.post("/console/api/consult", json={"case_id": case_id, "use_model": True})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_stats_block_counts_calls_cache_hits_and_cost_without_secrets(stand, provider):
    assert "cached" not in _consult(stand)["advice"]
    assert _consult(stand)["advice"]["cached"] is True and provider.hits == 1
    reply = stand.get("/console/api/stats")
    assert reply.status_code == 200
    data = reply.json()
    rows = {r["label"]: r["value"] for r in data["rows"]}
    assert rows["Вызовы модели"] == "1" and rows["Ответы из кэша"] == "1"
    assert rows["Расход сегодня"] == "0,0004 $" and rows["Осталось на день"] == "0,9996 $ из 1 $"
    assert rows["Предохранитель"] == "в норме" and rows["Токены: запрос и ответ"] == "400 и 40"
    assert data["usage"]["calls"] == 1 and data["model"]["model"] == "stand-model" and data["model"]["paused"] is None
    assert "Ключ, запросы и ответы модели здесь не показываются" in data["note"]
    health = stand.get("/health").json()["model"]
    assert health["enabled"] is True and health["cache_hits"] == 1 and health["today"]["cost_usd"] == 0.0004
    for text in (reply.text, stand.get("/health").text):
        assert SECRET not in text and llm.CHARTER_PROMPT[:40] not in text and "300 000" not in text
    assert TestClient(stand.app).get("/console/api/stats").status_code == 401


def test_health_has_model_usage_only_on_the_stand(provider):
    assert "model" not in TestClient(create_app(token="t" * 40, store=MemoryStore())).get("/health").json()
    stand = TestClient(create_app(console=True, store=MemoryStore()))
    model = stand.get("/health").json()["model"]
    assert model["enabled"] and model["calls"] == 0 and model["breaker"]["state"] == "closed"


def test_health_survives_a_broken_usage_counter(stand, monkeypatch):
    def broken() -> dict:
        raise RuntimeError("счётчик сломан")

    monkeypatch.setattr(llm, "shared_economy", broken)
    reply = stand.get("/health")
    assert reply.status_code == 200 and reply.json()["model"] == {"error": "счётчики модели недоступны"}


def test_console_says_the_rules_answer_when_the_budget_is_spent(stand, provider, monkeypatch):
    monkeypatch.setenv("MANDAT_LLM_BUDGET_USD_PER_DAY", "0.0004")
    provider.mode = "partial"
    assert _consult(stand)["advice"]["accepted"]
    reply = _consult(stand)
    assert reply["advice"]["paused"] == "budget" and "ответ дают только правила" in reply["advice"]["error"]
    assert reply["explanation"] and reply["model"]["paused"] == "budget" and provider.hits == 1
    session = stand.post("/console/api/session", json={}).json()["model"]
    assert session["enabled"] is True and session["paused"] == "budget" and "до 00:00" in session["note"]
    stats = stand.get("/console/api/stats").json()
    rows = {r["label"]: r["value"] for r in stats["rows"]}
    assert rows["Осталось на день"].endswith("ответ дают только правила") and stats["note"].startswith("Дневной")
    assert stand.get("/health").json()["model"]["today"]["exhausted"] is True


def test_console_answers_at_once_while_the_breaker_is_open(stand, provider, monkeypatch):
    monkeypatch.setenv("MANDAT_LLM_BREAKER_FAILURES", "1")
    provider.mode = "prose"
    assert _consult(stand)["advice"]["proposed"] == 0
    reply = _consult(stand)
    assert reply["advice"]["paused"] == "breaker" and reply["explanation"] and provider.hits == 1
    rows = {r["label"]: r["value"] for r in stand.get("/console/api/stats").json()["rows"]}
    assert rows["Предохранитель"].startswith("пауза после сбоев подряд")


def test_console_mixed_alphabet_charter_gets_a_note_not_a_server_error(stand, provider):
    docs = stand.get("/console/api/cases/09_charter_limit").json()
    upload = {
        "poas": docs["poas"],
        "egrul": docs["egrul"],
        "charter": docs["charter"].replace("директором", "дирzктором"),
        "operation": docs["operation"],
        "requester": docs["requester"],
        "registry_status": docs["registry_status"],
    }
    case_id = stand.post("/console/api/check", json={"upload": upload}).json()["case_id"]
    reply = stand.post("/console/api/consult", json={"case_id": case_id, "use_model": True})
    assert reply.status_code == 200 and "вперемешку" in reply.json()["advice"]["error"] and provider.hits == 0
