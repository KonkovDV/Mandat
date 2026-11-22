"""Допуск операции по удостоверению через HTTP: контракт для ДБО и других систем банка.

Второй шаг после проверки кейса. Система банка присылает удостоверение «Мандата» и запрос одной операции,
решение принимает ``attestation.authorize_operation`` без ослаблений: подлинность и срок удостоверения, статус
по спискам, вердикт ``confirmed``, организация, день операции по Москве, операция, сумма не больше проверенной,
представитель, получатель (``audience``) и одноразовый ``nonce``. Момент проверки — часы сервера, не поле запроса.

Одноразовость ``nonce`` держит общее хранилище: ``claim`` атомарен в памяти, SQLite и Redis, поэтому из
двух одновременных запросов с одним ``nonce`` проходит один. Вместе с ``nonce`` хранится отпечаток запроса и время
допуска: если ответ ДБО потерялся и тот же запрос пришёл снова, он получает отказ с признаком
``already_admitted`` и временем первого допуска. Второго допуска нет никогда; другой запрос с тем же ``nonce`` —
отказ без признака. High-watermark списков статусов тоже живёт в
хранилище и только растёт: подложенный после отзыва старый, но правильно подписанный список не принимается.

Идентификация клиента, антифрод и договор ДБО остаются процессами банка: ответ — допуск по удостоверению
«Мандата», а не разрешение операции целиком (docs/READINESS.md).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from mandat.api.storage import Store
from mandat.audit import attestation
from mandat.audit.signing import Keyring
from mandat.strictjson import strict_loads

KEYRING_ENV = "MANDAT_ATTESTATION_KEYRING"
STATUS_DIR_ENV = "MANDAT_STATUS_DIR"
REPRESENTATIVE_KEY_ENV = "MANDAT_REPRESENTATIVE_KEY_FILE"
# Удостоверение действует не дольше VALID_FOR: после этого его отвергает проверка срока, и nonce помнить не нужно.
NONCE_TTL_SECONDS = (attestation.VALID_FOR + timedelta(days=1)).total_seconds()
NONCE_OTHER_REQUEST = "nonce уже использован другим запросом"


class AdmissionConfigError(ValueError):
    """Допуск настроен не полностью или файлы не читаются."""


@dataclass(frozen=True)
class AdmissionConfig:
    """Чем проверяется удостоверение: связка ключей издателя, каталог списков статусов, ключ HMAC представителя."""

    keyring: Keyring
    status_dir: Path
    pseudonym_key: bytes | None

    @classmethod
    def from_env(cls, *, protected: bool) -> AdmissionConfig | None:
        """``None``, если допуск не включён (``MANDAT_ATTESTATION_KEYRING`` не задан)."""
        raw_keyring = os.environ.get(KEYRING_ENV, "").strip()
        if not raw_keyring:
            return None
        raw_status = os.environ.get(STATUS_DIR_ENV, "").strip()
        if not raw_status:
            raise AdmissionConfigError(f"{STATUS_DIR_ENV}: нужен каталог списков статусов удостоверений")
        try:
            keyring = Keyring.parse(strict_loads(Path(raw_keyring).read_bytes()))
        except (OSError, ValueError) as exc:
            raise AdmissionConfigError(f"{KEYRING_ENV}: связка ключей издателя не читается ({exc})") from exc
        raw_key = os.environ.get(REPRESENTATIVE_KEY_ENV, "").strip()
        pseudonym_key = None
        if raw_key:
            try:
                pseudonym_key = attestation._read_pseudonym_key(Path(raw_key))
            except (OSError, ValueError) as exc:
                raise AdmissionConfigError(f"{REPRESENTATIVE_KEY_ENV}: {exc}") from exc
        elif protected:
            raise AdmissionConfigError(f"{REPRESENTATIVE_KEY_ENV}: без ключа HMAC представителя не сверить")
        return cls(keyring, Path(raw_status), pseudonym_key)


class StoreWatermarks(dict[str, int]):
    """``seen`` для ``check_status``: недостающий список читается из хранилища при первом обращении."""

    def __init__(self, store: Store) -> None:
        super().__init__()
        self._store = store

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self:
            stored = self._store.watermark_get(key)
            if stored is not None:
                super().__setitem__(key, stored)
        return super().get(key, default)

    def save(self) -> None:
        for key, value in self.items():
            self._store.watermark_raise(key, value)


def nonce_key(audience: str, nonce: str) -> str:
    digest = hashlib.sha256(f"{audience}\x1f{nonce}".encode("utf-8", "surrogatepass")).hexdigest()
    return f"admission-nonce:{digest}"


@dataclass(frozen=True)
class AdmissionRequest:
    credential: dict[str, Any]
    operation: attestation.OperationRequest
    operation_risk: str | None


@dataclass(frozen=True)
class AdmissionDecision:
    """Решение о допуске. ``already_admitted`` — этот же запрос уже допущен в ``admitted_at``; допуск не повторяется."""

    problems: list[str]
    already_admitted: bool = False
    admitted_at: str | None = None


def request_fingerprint(request: AdmissionRequest, key: bytes | None) -> str:
    """Отпечаток удостоверения и запроса: тот же запрос даёт тот же отпечаток.

    С ключом представителя — HMAC: ИНН представителя из отпечатка в хранилище не подобрать перебором.
    """
    payload = json.dumps(
        {"credential": request.credential, "request": asdict(request.operation), "risk": request.operation_risk},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", "surrogatepass")
    if key:
        return hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def _is_fingerprint(text: str) -> bool:
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def decide(
    request: AdmissionRequest,
    config: AdmissionConfig,
    store: Store,
    *,
    now: datetime,
    protected: bool,
    consume: Callable[[str], bool] | None = None,
) -> list[str]:
    """Причины отказа; пустой список — допуск. Нечитаемое удостоверение — отказ, а не исключение."""
    return decide_detailed(request, config, store, now=now, protected=protected, consume=consume).problems


def decide_detailed(
    request: AdmissionRequest,
    config: AdmissionConfig,
    store: Store,
    *,
    now: datetime,
    protected: bool,
    consume: Callable[[str], bool] | None = None,
) -> AdmissionDecision:
    """То же решение, что ``decide``, и признак повтора уже допущенного запроса."""
    audience = request.operation.audience
    seen = StoreWatermarks(store)
    fingerprint = request_fingerprint(request, config.pseudonym_key)
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    held: list[str] = []
    saved = False

    def consume_nonce(nonce: str) -> bool:
        nonlocal saved
        # High-watermark списков пишется до захвата nonce: после захвата хранилище больше не трогается.
        # Иначе сбой хранилища между захватом и записью давал ответ 503 без записи в журнале, а повтор того же
        # запроса получал «уже допущен» с временем допуска, которого не было.
        seen.save()
        saved = True
        existing = store.claim(nonce_key(audience, nonce), f"{fingerprint} {stamp}", NONCE_TTL_SECONDS)
        if existing is None:
            return True
        held.append(existing)
        return False

    try:
        problems = attestation.authorize_operation(
            request.credential,
            config.keyring,
            now=now,
            status_dir=config.status_dir,
            allow_demo=not protected,
            seen=seen,
            operation_risk=request.operation_risk,
            request=request.operation,
            pseudonym_key=config.pseudonym_key,
            consume_nonce=consume or consume_nonce,
        )
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        # Нечитаемое удостоверение — отказ с причиной, а не 503 «хранилище недоступно».
        return AdmissionDecision([f"удостоверение не проверить: {type(exc).__name__}"])
    if not saved:
        seen.save()
    if held and attestation.NONCE_USED in problems:
        prior, _, prior_at = held[0].partition(" ")
        if _is_fingerprint(prior) and hmac.compare_digest(prior, fingerprint):
            return AdmissionDecision(problems, already_admitted=True, admitted_at=prior_at or None)
        if _is_fingerprint(prior):
            problems = [NONCE_OTHER_REQUEST if p == attestation.NONCE_USED else p for p in problems]
    return AdmissionDecision(problems)


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("operation_date: календарная дата ГГГГ-ММ-ДД") from exc
