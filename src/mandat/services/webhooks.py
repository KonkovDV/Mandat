"""Подпись события для контура банка по Standard Webhooks.

Перенесено из fsp-talent (``backend/app/services/webhooks.py``): подпись v1 над
``{id}.{timestamp}.{body}``. Ядро не отправляет HTTP и не следует редиректам:
доставку делает адаптер банка, у которого редиректы выключены. Без секрета
событие не подписывается.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

SKEW_SECONDS = 300


def canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_headers(secret: str, delivery_id: str, body: bytes, now: datetime) -> dict[str, str]:
    stamp = str(int(now.timestamp()))
    signed = f"{delivery_id}.{stamp}.".encode() + body
    mac = base64.b64encode(hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()).decode("ascii")
    return {
        "webhook-id": delivery_id,
        "webhook-timestamp": stamp,
        "webhook-signature": f"v1,{mac}",
        "Content-Type": "application/json",
    }


def signature_ok(
    secret: str,
    headers: dict[str, str],
    body: bytes,
    now: datetime,
    *,
    skew: int = SKEW_SECONDS,
) -> bool:
    delivery_id = headers.get("webhook-id")
    stamp = headers.get("webhook-timestamp")
    offered = headers.get("webhook-signature") or ""
    if not delivery_id or not stamp or not offered.startswith("v1,"):
        return False
    try:
        sent = int(stamp)
    except ValueError:
        return False
    if abs(now.timestamp() - sent) > skew:
        return False
    expected = sign_headers(secret, delivery_id, body, datetime.fromtimestamp(sent, UTC))["webhook-signature"]
    return hmac.compare_digest(expected, offered)


def delivery(payload: dict[str, Any], secret: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Тело и заголовки. Идентификатор доставки зависит только от тела."""
    moment = now or datetime.now(UTC)
    body = canonical_body(payload)
    delivery_id = "msg_" + hashlib.sha256(body).hexdigest()[:16]
    return {"body": body.decode("utf-8"), "headers": sign_headers(secret, delivery_id, body, moment)}
