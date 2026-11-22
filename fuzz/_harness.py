"""Общая обвязка фазз-целей: atheris в ClusterFuzzLite, обычный вызов в pytest.

Каждая цель — функция ``test_one_input(data: bytes)``. Допустимые ошибки разбора перечислены в самой цели;
любое другое исключение или зависание фаззер считает находкой. Без atheris (тесты, обычная машина) модуль
импортируется как есть, и цель можно вызвать на своём корпусе.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from typing import Any

try:
    import atheris
except ImportError:  # pytest и обычная установка: atheris нужен только самому фаззеру
    atheris = None


def instrumented() -> contextlib.AbstractContextManager[Any]:
    """Инструментировать импорты кода «Мандата», если цель запущена фаззером."""
    return atheris.instrument_imports() if atheris is not None else contextlib.nullcontext()


def run(target: Callable[[bytes], None]) -> None:
    if atheris is None:
        raise SystemExit("нужен atheris: pip install atheris (Python 3.11) или запуск в ClusterFuzzLite")
    atheris.Setup(sys.argv, target)
    atheris.Fuzz()
