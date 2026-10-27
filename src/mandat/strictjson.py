"""Строгий разбор JSON для внешних входов.

JCS (RFC 8785) определён только для I-JSON (RFC 7493): без повторов ключей, без NaN и
бесконечности. Обычный ``json.loads`` при двух одинаковых ключах оставляет последний, поэтому
подпись и прикладной код могли прочитать один документ по-разному. Глубина ограничена тем же
пределом, что и тело HTTP: сверхглубокий документ — ошибка входа, а не падение.

RFC 7493, разд. 2.1: в именах и строках нет одиночных суррогатов, в том числе записанных через ``\\u``.
Такой символ не кодируется в UTF-8: манифест с ``"\\ud800"`` проходил разбор и ронял API с 500 при записи
отчёта. Пара суррогатов — один символ, она допустима. Не-символы (U+FFFE и подобные) RFC 7493 тоже запрещает,
но Unicode Corrigendum #9 (2013) разрешает их в обмене, JCS записывает их однозначно, а этот же разбор читает
журнал: основание решения с U+FFFE уже могло попасть туда через API разбора. Поэтому они остаются.
Разд. 2.2: число вне IEEE 754 (``1e309``) — ошибка входа, а не бесконечность (#122).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

# Тот же предел, что у HTTP API: глубже этого документ не читается.
MAX_JSON_DEPTH = 64

# После разбора в строке остаются только одиночные суррогаты: пары json уже собрал в один символ.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


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


def _finite(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise StrictJsonError("NaN и бесконечность в JSON не допускаются: число не помещается в IEEE 754")
    return value


def _check_text(value: Any) -> None:
    """Имена и строки без одиночных суррогатов. Рекурсия не глубже ``MAX_JSON_DEPTH``: её проверили раньше."""
    if isinstance(value, str):
        if _LONE_SURROGATE.search(value):
            raise StrictJsonError("одиночный суррогат Юникода в строке JSON (RFC 7493, разд. 2.1)")
    elif isinstance(value, list):
        for item in value:
            _check_text(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_text(key)
            _check_text(item)


def strict_loads(text: str | bytes) -> Any:
    """Разобрать JSON. Повтор ключа, NaN, бесконечность, одиночный суррогат и глубина больше предела —
    ``StrictJsonError``."""
    try:
        raw = text.encode("utf-8") if isinstance(text, str) else text
    except UnicodeEncodeError as exc:
        raise StrictJsonError("суррогат Юникода в тексте JSON (RFC 7493, разд. 2.1)") from exc
    if depth_exceeds(raw):
        raise StrictJsonError(f"слишком глубокая вложенность JSON (более {MAX_JSON_DEPTH})")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError("JSON не в UTF-8") from exc
    try:
        value = json.loads(
            decoded, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant, parse_float=_finite
        )
    except json.JSONDecodeError:
        raise
    except ValueError as exc:
        # Целое длиннее предела цифр интерпретатора (sys.get_int_max_str_digits) — ошибка входа, а не ValueError.
        raise StrictJsonError("число в JSON не разбирается: слишком много цифр") from exc
    _check_text(value)
    return value
