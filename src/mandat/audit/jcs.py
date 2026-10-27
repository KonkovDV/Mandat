"""Каноническая запись JSON по RFC 8785 (JSON Canonicalization Scheme).

- ключи объектов сортируются по кодовым единицам UTF-16 (RFC 8785, разд. 3.2.3);
- числа записываются как в ECMAScript ``Number.prototype.toString`` (разд. 3.2.2.3);
- строки экранируются как в ``JSON.stringify``: ``\\b \\t \\n \\f \\r \\" \\\\``, прочие символы
  младше U+0020 — ``\\u00xx`` строчными; остальное без экранирования (разд. 3.2.2.2);
- NaN и бесконечность — ошибка (разд. 3.2.2.3); одиночные суррогаты — ошибка, их нет в UTF-8.

Целое вне ±(2**53 − 1) тоже ошибка: JCS работает с числами двойной точности, а RFC 7493 (разд. 2.2)
считает точными только целые из этого диапазона. Уже 2**53 и 2**53 + 1 дают одно и то же число IEEE 754;
независимая реализация (пакет ``rfc8785``) такое целое тоже отвергает. Проверено на примерах из текста
RFC 8785 (``tests/test_jcs.py``).
"""

from __future__ import annotations

import math
from decimal import Context, Decimal
from typing import Any

MAX_SAFE = 2**53 - 1  # Number.MAX_SAFE_INTEGER

_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


class JcsError(ValueError):
    """Значение нельзя записать по RFC 8785."""


def _utf16_key(text: str) -> bytes:
    return text.encode("utf-16-be", "surrogatepass")


def _string(text: str) -> str:
    out = ['"']
    for char in text:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise JcsError("одиночный суррогат в строке")
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


# repr даёт не больше 17 значащих цифр; своя точность не зависит от decimal-контекста вызывающего кода.
_EXACT = Context(prec=40)


def _number(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        raise JcsError("NaN и бесконечность в JSON не допускаются")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    digits_tuple, exponent = Decimal(repr(abs(value))).normalize(_EXACT).as_tuple()[1:]
    digits = "".join(str(d) for d in digits_tuple)
    k = len(digits)
    n = int(exponent) + k
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        body = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + body


def canonicalize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE:
            raise JcsError(f"целое {value} вне ±(2**53 − 1)")
        return _number(float(value))
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise JcsError("ключ объекта не строка")
        keys = sorted(value, key=_utf16_key)
        return "{" + ",".join(_string(k) + ":" + canonicalize(value[k]) for k in keys) + "}"
    raise JcsError(f"тип {type(value).__name__} не JSON")
