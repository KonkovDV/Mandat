"""Подпись исходящих событий по Standard Webhooks 1.0.

Спецификация: https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md

* Подписывается строка ``{webhook-id}.{webhook-timestamp}.{тело}`` алгоритмом HMAC-SHA256.
* Секрет в формате ``whsec_<base64>``, после декодирования от 24 до 64 байт. Строка без
  префикса принимается как раньше (байты UTF-8), чтобы не ломать уже настроенные контуры.
* Для смены ключа без простоя задаются несколько секретов через пробел: первый текущий,
  остальные старые. Заголовок ``webhook-signature`` несёт подписи всеми ключами через пробел,
  получатель принимает сообщение, если сошлась любая подпись с любым его ключом.
* Асимметричная подпись ``v1a`` (Ed25519): ключ подписи ``whsk_<base64>`` (32 байта seed или 64 байта
  seed‖открытый ключ), ключ проверки ``whpk_<base64>`` (32 байта). Получателю не нужен секрет: даже тот,
  кто прочитал его настройки, не подделает подпись. Ключи разных типов можно смешивать в одном списке.
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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from mandat.audit.journal import mode
from mandat.services.env_policy import protected

SKEW_SECONDS = 300
PREFIX = "whsec_"
SIGN_PREFIX, VERIFY_PREFIX = "whsk_", "whpk_"
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


def _b64(body: str) -> bytes:
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
    except binascii.Error as exc:
        raise ValueError("ключ вебхука должен быть в base64") from exc


@dataclass(frozen=True)
class Ed25519Key:
    """Ключ ``v1a``: ``private`` есть только у отправителя."""

    public: bytes
    private: bytes | None = None


def _ed25519(item: str) -> Ed25519Key:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if item.startswith(VERIFY_PREFIX):
        raw = _b64(item[len(VERIFY_PREFIX) :])
        if len(raw) != 32:
            raise ValueError("ключ whpk_ — 32 байта Ed25519")
        return Ed25519Key(raw)
    raw = _b64(item[len(SIGN_PREFIX) :])
    if len(raw) not in (32, 64):
        raise ValueError("ключ whsk_ — 32 байта seed или 64 байта seed и открытый ключ")
    public = (
        ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    if len(raw) == 64 and raw[32:] != public:
        raise ValueError("в ключе whsk_ открытая часть не от этого seed")
    return Ed25519Key(public, raw[:32])


def parse_keys(value: str | Iterable[str]) -> list[bytes | Ed25519Key]:
    """Секреты HMAC (``whsec_`` и старый формат) и ключи Ed25519 (``whsk_``, ``whpk_``) в одном списке."""
    items = value.split() if isinstance(value, str) else [s for v in value for s in v.split()]
    keys: list[bytes | Ed25519Key] = [
        _ed25519(s) if s.startswith((SIGN_PREFIX, VERIFY_PREFIX)) else decode_secret(s) for s in items
    ]
    if not keys:
        raise ValueError("не задан ни один секрет вебхука")
    return keys


def new_signing_key() -> tuple[str, str]:
    """Пара ``(whsk_…, whpk_…)``: ключ подписи — отправителю, ключ проверки — получателю."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    encode = base64.b64encode
    return SIGN_PREFIX + encode(seed + public).decode(), VERIFY_PREFIX + encode(public).decode()


def canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


log = logging.getLogger(__name__)


def _sign(key: bytes, delivery_id: str, stamp: str, body: bytes) -> str:
    mac = hmac.new(key, f"{delivery_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


def _sign_any(key: bytes | Ed25519Key, delivery_id: str, stamp: str, body: bytes) -> str:
    if isinstance(key, bytes):
        return _sign(key, delivery_id, stamp, body)
    if key.private is None:
        raise ValueError("ключом проверки whpk_ нельзя подписать: нужен whsk_")
    from cryptography.hazmat.primitives.asymmetric import ed25519

    signature = ed25519.Ed25519PrivateKey.from_private_bytes(key.private).sign(
        f"{delivery_id}.{stamp}.".encode() + body
    )
    return "v1a," + base64.b64encode(signature).decode("ascii")


def _matches(keys: Iterable[bytes | Ed25519Key], delivery_id: str, stamp: str, body: bytes, offered: list[str]) -> bool:
    """Сошлась ли хоть одна подпись: ``v1`` — HMAC с постоянным временем сравнения, ``v1a`` — Ed25519."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    message = f"{delivery_id}.{stamp}.".encode() + body
    matched = False
    for key in keys:
        for got in offered:
            if isinstance(key, bytes):
                if got.startswith("v1,"):
                    # Все пары без досрочного выхода: время не подсказывает, какой ключ сошёлся.
                    matched |= hmac.compare_digest(_sign(key, delivery_id, stamp, body).encode(), got.encode())
            elif got.startswith("v1a,"):
                try:
                    raw = base64.b64decode(got[4:], validate=True)
                    ed25519.Ed25519PublicKey.from_public_bytes(key.public).verify(raw, message)
                    matched = True
                except Exception:  # noqa: BLE001, S112 - чужая подпись, мусор в base64 — просто не совпала
                    continue
    return matched


def _offered(header: str) -> list[str]:
    return [s for s in header.split(" ") if s.startswith(("v1,", "v1a,"))]


def sign_headers(secret: str | Iterable[str], delivery_id: str, body: bytes, now: datetime) -> dict[str, str]:
    if not _ID.fullmatch(delivery_id):
        raise ValueError("webhook-id: латиница, цифры, _ и -, без точки")
    stamp = str(int(now.timestamp()))
    return {
        "webhook-id": delivery_id,
        "webhook-timestamp": stamp,
        "webhook-signature": " ".join(_sign_any(k, delivery_id, stamp, body) for k in parse_keys(secret)),
        "Content-Type": "application/json",
    }


# Секунды Unix: 10 цифр сейчас, 12 хватает до 33 658 года. Длиннее — не метка, а попытка уронить проверку:
# 400 цифр не переводятся во float (OverflowError), 5000 — в int (предел цифр интерпретатора).
_STAMP = re.compile(r"[0-9]{1,12}")


def _fresh(stamp: str, now: datetime, skew: int) -> bool:
    return _STAMP.fullmatch(stamp) is not None and abs(now.timestamp() - int(stamp)) <= skew


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
    offered = _offered(lower.get("webhook-signature") or "")
    if not _ID.fullmatch(delivery_id) or not offered or not _fresh(stamp, now, skew):
        return False
    if not _matches(parse_keys(secret), delivery_id, stamp, body, offered):
        return False
    # Повтор того же сообщения в пределах окна отвергается. Ключ живёт два окна: метка может уйти в обе стороны.
    return seen is None or seen.first_seen(f"webhook:{delivery_id}", 2 * skew, now.timestamp())


def response_signature_ok(
    secret: str | Iterable[str], headers: Mapping[str, str], body: bytes, now: datetime, expected_id: str
) -> bool:
    """Ответ получателя подписан и привязан к запросу: ``webhook-id`` равен ``expected_id``.

    Подпись — тем же секретом (``v1``) или ключом получателя ``whsk_`` (``v1a``, проверка его ``whpk_``).

    Хранилище повторов не нужно: идентификатор случайный и одноразовый, старый ответ к новому запросу не подойдёт.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    if lower.get("webhook-id") != expected_id:
        return False
    stamp = lower.get("webhook-timestamp") or ""
    offered = _offered(lower.get("webhook-signature") or "")
    if not offered or not _fresh(stamp, now, SKEW_SECONDS):
        return False
    return _matches(parse_keys(secret), expected_id, stamp, body, offered)


def delivery(payload: dict[str, Any], secret: str | Iterable[str], *, now: datetime | None = None) -> dict[str, Any]:
    """Тело и заголовки. Идентификатор доставки зависит только от тела: повтор доставки — тот же id."""
    moment = now or datetime.now(UTC)
    body = canonical_body(payload)
    delivery_id = "msg_" + hashlib.sha256(body).hexdigest()[:16]
    return {"body": body.decode("utf-8"), "headers": sign_headers(secret, delivery_id, body, moment)}
