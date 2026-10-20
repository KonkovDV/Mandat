"""Строгий разбор JSON для внешних входов.

JCS (RFC 8785) определён только для I-JSON (RFC 7493): без повторов ключей, без NaN и
бесконечности. Обычный ``json.loads`` при двух одинаковых ключах оставляет последний, поэтому
подпись и прикладной код могли прочитать один документ по-разному. Глубина ограничена тем же
пределом, что и тело HTTP: сверхглубокий документ — ошибка входа, а не падение.
"""

from __future__ import annotations

import json
from typing import Any

# Тот же предел, что у HTTP API: глубже этого документ не читается.
MAX_JSON_DEPTH = 64


class StrictJsonError(json.JSONDecodeError):
    """Вход не является строгим JSON."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "", 0)


def depth_exceeds(body: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Глубина скобок ``[`` и ``{`` вне строк превышает ``limit``. Линейный проход без разбора JSON."""
    depth = 0
    in_string = escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # обратная косая черта
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > limit:
                return True
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return False


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if key in seen:
            raise StrictJsonError("повторяющийся ключ")
        seen.add(key)
    return dict(pairs)


def _reject_constant(token: str) -> Any:
    raise StrictJsonError("NaN и бесконечность в JSON не допускаются")


def strict_loads(text: str | bytes) -> Any:
    """Разобрать JSON. Повтор ключа, NaN, Infinity и глубина больше предела — ``StrictJsonError``."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if depth_exceeds(raw):
        raise StrictJsonError(f"слишком глубокая вложенность JSON (более {MAX_JSON_DEPTH})")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError("JSON не в UTF-8") from exc
    return json.loads(decoded, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
