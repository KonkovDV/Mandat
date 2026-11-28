"""Расход и устойчивость модели-советника: предохранитель, дневной бюджет, кэш принятых ответов и счётчики.

* Предохранитель. После ``breaker_failures`` неудач подряд — сбой сети, тайм-аут, ответ не JSON или ответ, ни один
  факт которого не прошёл шлюз, — модель считается недоступной ``breaker_seconds`` секунд: вызовы за это время не
  уходят в сеть и сразу получают ``ModelPaused``, консультант отвечает по правилам. Затем идёт один пробный вызов:
  удачный закрывает предохранитель, неудачный открывает его снова.
* Дневной бюджет. Стоимость вызова — поле ``usage.cost`` ответа провайдера (OpenRouter присылает его при
  ``"usage": {"include": true}``). Когда расход за день по Москве доходит до потолка, модель выключена до полуночи
  по Москве. Вызов, начатый до потолка, завершается: превышение — не больше стоимости вызовов, которые уже шли.
* Кэш. Ответ, который шлюз принял целиком, хранится в памяти по SHA-256 точных сообщений и модели: такой же запрос
  получает его без вызова модели. Ответ не JSON, с отброшенным фактом или ошибка в кэш не попадают.
* Счётчики: вызовы, неудачи, открытия предохранителя, попадания в кэш, токены и стоимость — без ключей, текста
  запросов и ответов.

Здесь нет сети и чтения окружения: настройки ``MANDAT_LLM_*`` читает ``advisory/llm.py``, там же живёт общий на
процесс экземпляр ``Economy``. Всё хранится в памяти процесса: перезапуск службы обнуляет счётчики, расход дня и кэш.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
# Ответ модели длиннее этого в кэш не кладётся: память кэша ограничена и числом записей, и размером каждой.
MAX_CACHED_CHARS = 64 * 1024
CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


@dataclass(frozen=True)
class Settings:
    """Пороги учёта. Значения по умолчанию — для стенда; ``advisory/llm.py`` берёт их из ``MANDAT_LLM_*``."""

    breaker_failures: int = 3
    breaker_seconds: float = 120.0
    budget_usd_per_day: float = 1.0
    cache_entries: int = 256
    cache_seconds: float = 24 * 3600.0


def _count(raw: object) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0


def _money(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    try:
        value = float(raw)
    except OverflowError:
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


@dataclass(frozen=True)
class Usage:
    """Расход одного вызова из поля ``usage`` ответа провайдера. Поля нет или оно не число — ноль."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @classmethod
    def of(cls, raw: object) -> Usage:
        if not isinstance(raw, dict):
            return cls()
        return cls(_count(raw.get("prompt_tokens")), _count(raw.get("completion_tokens")), _money(raw.get("cost")))


class ModelPaused(RuntimeError):
    """Вызов модели не выполнен: открыт предохранитель или исчерпан бюджет дня. Ответ дают правила.

    ``reason`` — ``breaker`` или ``budget``, ``until`` — когда модель снова можно вызвать, ``note`` — фраза для
    сотрудника со строчной буквы.
    """

    def __init__(self, reason: str, until: datetime, note: str) -> None:
        super().__init__(note)
        self.reason, self.until, self.note = reason, until, note


class Breaker:
    """Предохранитель: ``closed`` → ``open`` после ``threshold`` неудач подряд → ``half_open`` через ``seconds``.

    В ``half_open`` проходит один пробный вызов. Пробный вызов, исход которого так и не записали, через ``seconds``
    заменяется новым: зависший вызывающий не держит предохранитель открытым навсегда. Неудача, записанная, пока
    предохранитель открыт, срок не продлевает: это вызов, начатый раньше. Удача закрывает предохранитель всегда.
    """

    def __init__(self, threshold: int, seconds: float) -> None:
        self.threshold, self.seconds = threshold, seconds
        self.failures = 0
        self.opened_at: float | None = None
        self.probe_at: float | None = None

    def state(self, now: float) -> str:
        if self.opened_at is None:
            return CLOSED
        return OPEN if now - self.opened_at < self.seconds else HALF_OPEN

    def allow(self, now: float) -> bool:
        """Можно ли вызвать модель сейчас. В ``half_open`` первый вызывающий становится пробным вызовом."""
        state = self.state(now)
        if state == CLOSED:
            return True
        if state == OPEN or (self.probe_at is not None and now - self.probe_at < self.seconds):
            return False
        self.probe_at = now
        return True

    def success(self) -> None:
        self.failures, self.opened_at, self.probe_at = 0, None, None

    def failure(self, now: float) -> bool:
        """Учесть неудачу. ``True`` — предохранитель открылся: впервые или снова после пробного вызова."""
        self.failures += 1
        state = self.state(now)
        if state == OPEN:
            return False
        if state == HALF_OPEN or self.failures >= self.threshold:
            self.opened_at, self.probe_at = now, None
            return True
        return False

    def retry_in(self, now: float) -> float | None:
        """Сколько секунд до пробного вызова, если предохранитель открыт."""
        if self.state(now) != OPEN or self.opened_at is None:
            return None
        return max(0.0, self.opened_at + self.seconds - now)


def moscow_day(now: float) -> date:
    return datetime.fromtimestamp(now, MOSCOW).date()


def next_midnight(now: float) -> datetime:
    """Ближайшая полночь по Москве после момента ``now``."""
    tomorrow = moscow_day(now) + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=MOSCOW)


class DailyBudget:
    """Расход за день по Москве и его потолок. С новым днём расход начинается с нуля."""

    def __init__(self, cap_usd: float) -> None:
        self.cap = cap_usd
        self.day: date | None = None
        self.spent = 0.0

    def _roll(self, now: float) -> None:
        today = moscow_day(now)
        if today != self.day:
            self.day, self.spent = today, 0.0

    def add(self, cost_usd: float, now: float) -> None:
        self._roll(now)
        self.spent += cost_usd

    def exhausted(self, now: float) -> bool:
        self._roll(now)
        return self.spent >= self.cap

    def left(self, now: float) -> float:
        self._roll(now)
        return max(0.0, self.cap - self.spent)


class AnswerCache:
    """Принятые ответы модели по ключу запроса: не больше ``entries`` записей, каждая живёт ``seconds``.

    Порядок LRU: попадание переносит запись в конец, сверх ``entries`` уходит самая давно нужная.
    """

    def __init__(self, entries: int, seconds: float) -> None:
        self.entries, self.seconds = entries, seconds
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str, now: float) -> str | None:
        found = self._items.get(key)
        if found is None:
            return None
        if now - found[0] >= self.seconds:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return found[1]

    def put(self, key: str, text: str, now: float) -> bool:
        if self.entries <= 0 or len(text) > MAX_CACHED_CHARS:
            return False
        self._items[key] = (now, text)
        self._items.move_to_end(key)
        self.trim(now)
        return True

    def trim(self, now: float) -> None:
        for key in [k for k, (at, _) in self._items.items() if now - at >= self.seconds]:
            del self._items[key]
        while len(self._items) > max(self.entries, 0):
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


def _seconds(value: float) -> str:
    return f"{math.ceil(value)} с"


class Economy:
    """Учёт модели на процесс: порог перед вызовом, исход вызова, кэш и счётчики. Все методы потокобезопасны.

    ``clock`` — настенное время (день бюджета по Москве), ``monotonic`` — для предохранителя и срока кэша.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings or Settings()
        self._clock, self._monotonic = clock, monotonic
        self._lock = threading.Lock()
        self.breaker = Breaker(self.settings.breaker_failures, self.settings.breaker_seconds)
        self.budget = DailyBudget(self.settings.budget_usd_per_day)
        self.cache = AnswerCache(self.settings.cache_entries, self.settings.cache_seconds)
        self.calls = self.failures = self.breaker_opens = self.cache_hits = 0
        self.prompt_tokens = self.completion_tokens = 0
        self.cost_usd = 0.0
        self.failure_kinds: dict[str, int] = {}
        self.refused: dict[str, int] = {}

    def configure(self, settings: Settings) -> None:
        """Новые пороги без потери счётчиков, расхода дня и кэша."""
        with self._lock:
            if settings == self.settings:
                return
            self.settings = settings
            self.breaker.threshold, self.breaker.seconds = settings.breaker_failures, settings.breaker_seconds
            self.budget.cap = settings.budget_usd_per_day
            self.cache.entries, self.cache.seconds = settings.cache_entries, settings.cache_seconds
            self.cache.trim(self._monotonic())

    def _budget_pause(self, now: float) -> ModelPaused:
        cap = f"{self.budget.cap:g}"
        note = f"дневной бюджет модели ({cap} $) исчерпан: до 00:00 по Москве ответ дают только правила"
        return ModelPaused("budget", next_midnight(now), note)

    def _breaker_pause(self, now: float, tick: float) -> ModelPaused:
        wait = self.breaker.retry_in(tick)
        if wait is None:  # half_open: пробный вызов уже идёт
            note = "модель проверяется пробным вызовом после сбоев подряд: пока ответ дают только правила"
            return ModelPaused("breaker", datetime.fromtimestamp(now, MOSCOW), note)
        after = _seconds(wait)
        note = f"модель на паузе после сбоев подряд: ответ дают только правила, следующая попытка через {after}"
        return ModelPaused("breaker", datetime.fromtimestamp(now + wait, MOSCOW), note)

    def admit(self) -> None:
        """Перед вызовом модели: бюджет дня и предохранитель. Отказ — ``ModelPaused``, и в сеть ничего не уходит."""
        with self._lock:
            now, tick = self._clock(), self._monotonic()
            if self.budget.exhausted(now):
                self.refused["budget"] = self.refused.get("budget", 0) + 1
                raise self._budget_pause(now)
            if not self.breaker.allow(tick):
                self.refused["breaker"] = self.refused.get("breaker", 0) + 1
                raise self._breaker_pause(now, tick)
            self.calls += 1

    def paused(self) -> ModelPaused | None:
        """Почему модель сейчас не вызывается, без пробного вызова: для строки состояния консоли."""
        with self._lock:
            now, tick = self._clock(), self._monotonic()
            if self.budget.exhausted(now):
                return self._budget_pause(now)
            if self.breaker.state(tick) == OPEN:
                return self._breaker_pause(now, tick)
            return None

    def charge(self, usage: Usage) -> None:
        """Расход ответа провайдера: токены и стоимость идут в счётчики и в бюджет дня."""
        with self._lock:
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
            self.cost_usd += usage.cost_usd
            self.budget.add(usage.cost_usd, self._clock())

    def failed(self, kind: str) -> None:
        """Неудача вызова: ``network``, ``http``, ``timeout``, ``format``, ``non_json`` или ``ungrounded``."""
        with self._lock:
            self.failures += 1
            self.failure_kinds[kind] = self.failure_kinds.get(kind, 0) + 1
            if self.breaker.failure(self._monotonic()):
                self.breaker_opens += 1

    def succeeded(self) -> None:
        with self._lock:
            self.breaker.success()

    def cached(self, key: str) -> str | None:
        """Принятый ответ на тот же запрос, если он ещё в кэше; попадание считается."""
        with self._lock:
            text = self.cache.get(key, self._monotonic())
            if text is not None:
                self.cache_hits += 1
            return text

    def remember(self, key: str, text: str) -> bool:
        """Положить в кэш ответ, который шлюз принял целиком. Решает вызывающий: учёт шлюза не знает."""
        with self._lock:
            return self.cache.put(key, text, self._monotonic())

    def snapshot(self) -> dict[str, Any]:
        """Счётчики для /health и консоли: только числа и состояния, без ключей, запросов и ответов."""
        with self._lock:
            now, tick = self._clock(), self._monotonic()
            exhausted = self.budget.exhausted(now)
            self.cache.trim(tick)
            state = self.breaker.state(tick)
            retry = self.breaker.retry_in(tick)
            day = self.budget.day or moscow_day(now)
            return {
                "calls": self.calls,
                "failures": self.failures,
                "failure_kinds": dict(sorted(self.failure_kinds.items())),
                "breaker_opens": self.breaker_opens,
                "cache_hits": self.cache_hits,
                "refused": dict(sorted(self.refused.items())),
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "today": {
                    "day": day.isoformat(),
                    "cost_usd": round(self.budget.spent, 6),
                    "cap_usd": self.budget.cap,
                    "left_usd": round(self.budget.left(now), 6),
                    "exhausted": exhausted,
                    "resumes_at": next_midnight(now).isoformat() if exhausted else None,
                },
                "breaker": {
                    "state": state,
                    "failures_in_row": self.breaker.failures,
                    "threshold": self.breaker.threshold,
                    "open_seconds": self.breaker.seconds,
                    "retry_in_seconds": None if retry is None else math.ceil(retry),
                },
                "cache": {
                    "entries": len(self.cache),
                    "capacity": self.cache.entries,
                    "ttl_seconds": self.cache.seconds,
                },
            }
