"""HTTP-протокол uvicorn с TCP_NODELAY на каждом соединении (R2-PERF-3).

С ``--workers N`` uvicorn сам создаёт слушающий сокет с ``proto=0``, и asyncio не включает на
принятых соединениях TCP_NODELAY (``base_events._set_nodelay`` проверяет ``proto == IPPROTO_TCP``).
Ответ уходит двумя сегментами (заголовки и тело), и на keep-alive второй ждёт delayed ACK клиента:
~40 мс на запрос — 10 RPS на одного клиента вместо ~100. Протокол ниже ставит опцию явно.
"""

from __future__ import annotations

import socket
from typing import Any

from uvicorn.protocols.http.h11_impl import H11Protocol


def set_nodelay(transport: Any) -> None:
    sock = transport.get_extra_info("socket")
    if sock is not None and sock.family in (socket.AF_INET, socket.AF_INET6):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


class NoDelayH11Protocol(H11Protocol):
    def connection_made(self, transport: Any) -> None:
        set_nodelay(transport)
        super().connection_made(transport)
