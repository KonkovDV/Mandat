"""Секреты из окружения: в защищённом режиме — только случайные ключи, а не пароли.

HMAC журнала и псевдонимов и HKDF ключа хранилища перебор не замедляют. Пароль из 32 букв
подбирается по словарю, а для ключа ПДн это прямой путь к ИНН по псевдониму: пространство ИНН
маленькое. Поэтому в pilot и prod ключ — не меньше 32 случайных байтов в hex или base64, как дают
``openssl rand -hex 32`` и ``python -c "import secrets; print(secrets.token_urlsafe(48))"``.

Проверка ловит пароли, повторы и короткие ключи, но не доказывает случайность: ключ берут из
генератора. Вывод ключа не меняется — HKDF и HMAC берут ту же строку, что и раньше, поэтому
записи, зашифрованные прежним случайным ключом, читаются.
"""

from __future__ import annotations

import base64
import binascii
import re

MIN_RANDOM_BYTES = 32
# У 32 случайных байтов в среднем около 30 разных значений; меньше 16 — не выход генератора.
MIN_DISTINCT_BYTES = 16
_HEX = re.compile(r"(?:[0-9a-fA-F]{2}){32,}")
_B64 = re.compile(r"[A-Za-z0-9+/_-]{43,}={0,2}")
HOW = "openssl rand -hex 32"


def decode_random(value: str) -> bytes | None:
    """Байты ключа из hex или base64 (обычного и для URL, с выравниванием и без). Иначе ``None``."""
    if _HEX.fullmatch(value):
        return bytes.fromhex(value)
    if not _B64.fullmatch(value):
        return None
    text = value.rstrip("=").replace("+", "-").replace("/", "_")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


def weak_key(name: str, value: str) -> str | None:
    """Почему значение переменной ``name`` — не случайный ключ, или ``None``."""
    raw = decode_random(value)
    if raw is None:
        return (
            f"{name}: нужен случайный ключ — не меньше {MIN_RANDOM_BYTES} байт в hex или base64 ({HOW}), "
            "а не пароль: HMAC и HKDF перебор не замедляют"
        )
    if len(raw) < MIN_RANDOM_BYTES:
        return f"{name}: после декодирования {len(raw)} байт, нужно не меньше {MIN_RANDOM_BYTES} ({HOW})"
    # Пароль из одних строчных или одних заглавных букв тоже проходит как base64. У случайной строки base64
    # из 43 знаков нет заглавных или нет строчных с вероятностью порядка 10⁻⁹.
    if not _HEX.fullmatch(value) and not (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value)):
        return f"{name}: похоже на слово или пароль, а не на base64 из генератора ({HOW})"
    if len(set(raw)) < MIN_DISTINCT_BYTES:
        return f"{name}: в ключе мало разных байтов — это не выход генератора случайных чисел ({HOW})"
    return None
