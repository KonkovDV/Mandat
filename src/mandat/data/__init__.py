"""Данные пакета: пакет правил, каталог операций, соответствие полей формата МЧД."""

from __future__ import annotations

import hashlib
import json
from functools import cache
from importlib import resources
from typing import Any


@cache
def load_json(name: str) -> dict[str, Any]:
    text = resources.files(__name__).joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


def canonical_json(payload: Any) -> str:
    """Каноническая запись: сортировка ключей, без пробелов (совпадает с RFC 8785 для строк и целых)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
