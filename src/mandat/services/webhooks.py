"""Подпись исходящих событий по Standard Webhooks 1.0.

Спецификация: https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md

* Подписывается строка ``{webhook-id}.{webhook-timestamp}.{тело}`` алгоритмом HMAC-SHA256.
* Секрет в формате ``whsec_<base64>``, после декодирования от 24 до 64 байт. Строка без
  префикса принимается как раньше (байты UTF-8), чтобы не ломать уже настроенные контуры.
* Для смены ключа без простоя задаются несколько секретов через пробел: первый текущий,
  остальные старые. Заголовок ``webhook-signature`` несёт подписи всеми ключами через пробел,
  получатель принимает сообщение, если сошлась любая подпись с любым его ключом.
* Получатель отвергает метку времени вне окна (по умолчанию 5 минут) и, если ему дали хранилище,
  повтор того же ``webhook-id``.

Ядро не отправляет HTTP и не следует редиректам: доставку делает адаптер банка.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from mandat.audit.journal import mode
from mandat.services.env_policy import protected

SKEW_SECONDS = 300
PREFIX = "whsec_"
MIN_KEY, MAX_KEY = 24, 64
_ID = re.compile(r"[A-Za-z0-9_\-]{1,128}")


class SeenStore(Protocol):
    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool: ...


def decode_secret(secret: str) -> bytes:
    """``whsec_<base64>`` → ключ. Строка без префикса — старый формат, байты UTF-8."""
    secret = secret.strip()
    if not secret:
        raise ValueError("пустой секрет вебхука")
    if not secret.startswith(PREFIX):
        key = secret.encode("utf-8")
        # Старый формат без префикса: в защищённом режиме короткий секрет не принимается.
        if len(key) < MIN_KEY and protected():
            raise ValueError(f"секрет без whsec_ короче {MIN_KEY} байт запрещён при MANDAT_ENV={mode()}")
        return key
    try:
        body = secret[len(PREFIX) :]
        # Эталонная библиотека принимает base64 и без выравнивания «=», принимаем и мы.
        key = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
    except binascii.Error as exc:
        raise ValueError("секрет whsec_ должен быть в base64") from exc
    if not MIN_KEY <= len(key) <= MAX_KEY:
        raise ValueError(f"ключ whsec_ должен быть от {MIN_KEY} до {MAX_KEY} байт, а не {len(key)}")
    return key


def parse_secrets(value: str | Iterable[str]) -> list[bytes]:
    """Один секрет или несколько через пробел; порядок сохраняется, первый — текущий."""
    items = value.split() if isinstance(value, str) else [s for v in value for s in v.split()]
    keys = [decode_secret(s) for s in items]
    if not keys:
        raise ValueError("не задан ни один секрет вебхука")
    return keys


def new_secret() -> str:
    return PREFIX + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


log = logging.getLogger(__name__)


def _sign(key: bytes, delivery_id: str, stamp: str, body: bytes) -> str:
    mac = hmac.new(key, f"{delivery_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


def sign_headers(secret: str | Iterable[str], delivery_id: str, body: bytes, now: datetime) -> dict[str, str]:
    if not _ID.fullmatch(delivery_id):
        raise ValueError("webhook-id: латиница, цифры, _ и -, без точки")
    stamp = str(int(now.timestamp()))
    return {
        "webhook-id": delivery_id,
        "webhook-timestamp": stamp,
        "webhook-signature": " ".join(_sign(k, delivery_id, stamp, body) for k in parse_secrets(secret)),
        "Content-Type": "application/json",
    }


def signature_ok(
    secret: str | Iterable[str],
    headers: Mapping[str, str],
    body: bytes,
    now: datetime,
    *,
    skew: int = SKEW_SECONDS,
    seen: SeenStore | None = None,
) -> bool:
    if seen is None and protected():
        # Без общего хранилища повтор того же сообщения не отличить от нового: это отказ, а не ошибка 500.
        log.warning("MANDAT_ENV=%s: проверка вебхука без хранилища повторов отклонена", mode())
        return False
    lower = {k.lower(): v for k, v in headers.items()}
    delivery_id = lower.get("webhook-id") or ""
    stamp = lower.get("webhook-timestamp") or ""
    offered = [s for s in (lower.get("webhook-signature") or "").split(" ") if s.startswith("v1,")]
    if not _ID.fullmatch(delivery_id) or not stamp.isascii() or not stamp.isdigit() or not offered:
        return False
    if abs(now.timestamp() - int(stamp)) > skew:
        return False
    expected = [_sign(k, delivery_id, stamp, body) for k in parse_secrets(secret)]
    # Сравниваем все пары, не выходя досрочно: время не подсказывает, какой ключ сошёлся.
    matched = False
    for want in expected:
        for got in offered:
            matched |= hmac.compare_digest(want.encode(), got.encode())
    if not matched:
        return False
    # Повтор того же сообщения в пределах окна отвергается. Ключ живёт два окна: метка может уйти в обе стороны.
    return seen is None or seen.first_seen(f"webhook:{delivery_id}", 2 * skew, now.timestamp())


def response_signature_ok(
    secret: str | Iterable[str], headers: Mapping[str, str], body: bytes, now: datetime, expected_id: str
) -> bool:
    """Ответ получателя подписан тем же секретом и привязан к запросу: ``webhook-id`` равен ``expected_id``.

    Хранилище повторов не нужно: идентификатор случайный и одноразовый, старый ответ к новому запросу не подойдёт.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    if lower.get("webhook-id") != expected_id:
        return False
    stamp = lower.get("webhook-timestamp") or ""
    offered = [s for s in (lower.get("webhook-signature") or "").split(" ") if s.startswith("v1,")]
    if not stamp.isascii() or not stamp.isdigit() or not offered or abs(now.timestamp() - int(stamp)) > SKEW_SECONDS:
        return False
    matched = False
    for want in (_sign(k, expected_id, stamp, body) for k in parse_secrets(secret)):
        for got in offered:
            matched |= hmac.compare_digest(want.encode(), got.encode())
    return matched


def delivery(payload: dict[str, Any], secret: str | Iterable[str], *, now: datetime | None = None) -> dict[str, Any]:
    """Тело и заголовки. Идентификатор доставки зависит только от тела: повтор доставки — тот же id."""
    moment = now or datetime.now(UTC)
    body = canonical_body(payload)
    delivery_id = "msg_" + hashlib.sha256(body).hexdigest()[:16]
    return {"body": body.decode("utf-8"), "headers": sign_headers(secret, delivery_id, body, moment)}
