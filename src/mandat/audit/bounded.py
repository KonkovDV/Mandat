"""Чтение ответа внешней службы с пределом размера (P6-06).

Метка времени RFC 3161, Rekor v1 и v2, тайлы и checkpoint прозрачного журнала, Vault Transit: адреса задаёт
администратор, но на этом стыке ответ может прийти от подменённого или сломанного узла (прокси, балансировщик,
взломанный сервис). Ответ сверх предела — ``OSError``, как у OCSP и CRL (``audit/revocation.py``): якорь не
подтверждён или ключ не загружен, а процесс не падает по памяти. Предел щедрый: самый большой законный ответ —
тайл журнала, 256 хешей по 32 байта.
"""

from __future__ import annotations

from typing import Any

MAX_EXTERNAL_RESPONSE = 4 * 1024 * 1024


def read_bounded(response: Any, limit: int = MAX_EXTERNAL_RESPONSE) -> bytes:
    """Тело ответа не длиннее ``limit`` байт; длиннее — ``OSError``. Читается не больше ``limit + 1`` байт."""
    data = bytes(response.read(limit + 1))
    if len(data) > limit:
        raise OSError(f"ответ внешней службы больше {limit} байт")
    return data
