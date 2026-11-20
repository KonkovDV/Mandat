"""Шифрование записей хранилища (P5-01): отчёт и разбор кейса лежат в SQLite и Redis зашифрованными.

Хранилище копируют: резервные копии, снимки Redis (RDB, AOF), реплики, файл SQLite на диске. Каждая копия —
та же база ПДн, что и рабочая. Зашифрованная запись в копии без ключа бесполезна, а уничтожение ключа
уничтожает данные сразу во всех копиях. Второе свойство — целостность. AES-256-GCM с привязкой к виду записи,
``case_id``, владельцу отчёта и версии разбора не даёт тайно поменять вердикт в базе, переставить отчёт одного
кейса под другой, отдать отчёт другому пользователю, переписав поле владельца рядом с шифром (P6-01), или вернуть
разбор к прежней версии без смены её номера. Запись без шифра при заданном ключе тоже отказ: иначе подделку
достаточно было бы записать открытым текстом. Записи прежнего формата ``mandat.sealed.v1`` (владелец не привязан)
не читаются: кейс создают заново.

Это не СКЗИ. Если модель угроз банка требует криптографической защиты хранения (приказ ФСБ России № 378),
её даёт сертифицированное средство с ГОСТ Р 34.12-2015. Замена — другой ``Sealer`` с теми же методами.

Ключ — ``MANDAT_STORE_KEY``, не короче 32 байт, отдельный от ключей журнала и псевдонимизации. В pilot и
prod без него ``mandat serve`` не стартует. Для смены ключа прежний кладут в ``MANDAT_STORE_KEY_PREVIOUS``
(несколько — через запятую): им записи только читаются, а при следующей записи шифруются новым ключом.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mandat.api.storage import Pending, Store

MIN_KEY_BYTES = 32
# v2 (P6-01): привязка отчёта включает владельца. v1 его не включал, и такие записи не читаются.
ENVELOPE = "mandat.sealed.v2"
UNBOUND_ENVELOPES = frozenset({"mandat.sealed.v1"})
NONCE_BYTES = 12


class StoreKeyMissing(RuntimeError):
    """Режим требует ключ хранилища, а ``MANDAT_STORE_KEY`` не задан или не годится."""


class SealError(ValueError):
    """Запись хранилища не прошла проверку: не зашифрована, изменена или зашифрована неизвестным ключом."""


def _derive(material: str) -> bytes:
    raw = material.encode("utf-8")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"mandat.store.v1", info=b"aes-256-gcm")
    return hkdf.derive(raw)


def _kid(key: bytes) -> str:
    return hashlib.sha256(b"kid" + key).hexdigest()[:16]


class Sealer:
    """AES-256-GCM. Текущий ключ шифрует и читает, прежние только читают."""

    def __init__(self, current: str, previous: list[str] | None = None) -> None:
        if len(current.encode("utf-8")) < MIN_KEY_BYTES:
            raise StoreKeyMissing(f"MANDAT_STORE_KEY короче {MIN_KEY_BYTES} байт")
        key = _derive(current)
        self.kid = _kid(key)
        self._keys: dict[str, AESGCM] = {self.kid: AESGCM(key)}
        for old in previous or []:
            if len(old.encode("utf-8")) < MIN_KEY_BYTES:
                raise StoreKeyMissing(f"MANDAT_STORE_KEY_PREVIOUS: ключ короче {MIN_KEY_BYTES} байт")
            old_key = _derive(old)
            self._keys.setdefault(_kid(old_key), AESGCM(old_key))

    def seal(self, value: dict[str, Any], aad: str) -> dict[str, Any]:
        nonce = os.urandom(NONCE_BYTES)
        plain = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        data = self._keys[self.kid].encrypt(nonce, plain, aad.encode("utf-8"))
        return {
            "sealed": ENVELOPE,
            "kid": self.kid,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "data": base64.b64encode(data).decode("ascii"),
        }

    def open(self, value: Any, aad: str) -> dict[str, Any]:
        if isinstance(value, dict) and value.get("sealed") in UNBOUND_ENVELOPES:
            raise SealError(
                f"запись в формате {value.get('sealed')}: владелец не привязан к шифру (P6-01), создайте кейс заново"
            )
        if not isinstance(value, dict) or value.get("sealed") != ENVELOPE:
            raise SealError("запись хранилища не зашифрована")
        cipher = self._keys.get(str(value.get("kid")))
        if cipher is None:
            raise SealError("запись зашифрована неизвестным ключом")
        try:
            nonce = base64.b64decode(str(value["nonce"]), validate=True)
            data = base64.b64decode(str(value["data"]), validate=True)
            plain = cipher.decrypt(nonce, data, aad.encode("utf-8"))
        except (KeyError, ValueError, InvalidTag) as exc:
            raise SealError("запись хранилища изменена или относится к другому кейсу") from exc
        loaded = json.loads(plain)
        if not isinstance(loaded, dict):
            raise SealError("запись хранилища не объект")
        return loaded


def _env_keys() -> tuple[str, list[str]]:
    current = os.environ.get("MANDAT_STORE_KEY", "").strip()
    previous = [k.strip() for k in os.environ.get("MANDAT_STORE_KEY_PREVIOUS", "").split(",") if k.strip()]
    return current, previous


def load_sealer() -> Sealer | None:
    """Шифр по ``MANDAT_STORE_KEY``. Без ключа — ``None`` на стенде и ``StoreKeyMissing`` в pilot и prod."""
    from mandat.audit.journal import mode
    from mandat.services.env_policy import protected

    current, previous = _env_keys()
    if not current:
        if protected():
            raise StoreKeyMissing(
                f"MANDAT_ENV={mode()}: отчёты с ПДн не хранятся открытым текстом, задайте MANDAT_STORE_KEY"
            )
        return None
    for other in ("MANDAT_JOURNAL_KEY", "MANDAT_PII_KEY"):
        if current == os.environ.get(other, "").strip():
            raise StoreKeyMissing(f"MANDAT_STORE_KEY совпадает с {other}: у каждого назначения свой ключ")
    return Sealer(current, previous)


def report_aad(case_id: str, owner: str) -> str:
    """Привязка отчёта: вид записи, кейс и владелец. По владельцу API решает, кто видит отчёт (P6-01)."""
    return f"report\x00{case_id}\x00{owner}"


def review_aad(case_id: str, version: int) -> str:
    return f"review\x00{case_id}\x00{version}"


class SealedStore:
    """Хранилище, которое шифрует отчёты и разборы до записи и проверяет их при чтении.

    Остальное — лимиты, ``nonce``, Idempotency-Key, high-watermark — без ПДн и передаётся как есть.
    """

    def __init__(self, inner: Store, sealer: Sealer) -> None:
        self.inner = inner
        self.sealer = sealer
        self.name = inner.name

    # --- отчёты ---
    def get(self, case_id: str) -> tuple[dict[str, Any], str] | None:
        found = self.inner.get(case_id)
        if found is None:
            return None
        return self.sealer.open(found[0], report_aad(case_id, found[1])), found[1]

    def put(self, case_id: str, report: dict[str, Any], owner: str, max_reports: int) -> None:
        self.inner.put(case_id, self.sealer.seal(report, report_aad(case_id, owner)), owner, max_reports)

    def stage(
        self,
        case_id: str,
        report: dict[str, Any],
        owner: str,
        attempt: str,
        idem: Any,
        now: float | None = None,
    ) -> None:
        self.inner.stage(case_id, self.sealer.seal(report, report_aad(case_id, owner)), owner, attempt, idem, now)

    def _opened(self, pend: Pending | None) -> Pending | None:
        if pend is None:
            return None
        return pend._replace(report=self.sealer.open(pend.report, report_aad(pend.case_id, pend.owner)))

    def pending(self, case_id: str) -> Pending | None:
        return self._opened(self.inner.pending(case_id))

    def pending_all(self) -> list[Pending]:
        return [p for p in (self._opened(p) for p in self.inner.pending_all()) if p is not None]

    def pending_for_idem(self, owner: str, key: str) -> Pending | None:
        return self._opened(self.inner.pending_for_idem(owner, key))

    def commit(self, case_id: str, attempt: str, max_reports: int, now: float | None = None) -> bool:
        return self.inner.commit(case_id, attempt, max_reports, now)

    def discard(self, case_id: str, attempt: str) -> bool:
        return self.inner.discard(case_id, attempt)

    def erase(self, case_id: str) -> bool:
        return self.inner.erase(case_id)

    # --- разбор: версия входит в привязку, поэтому прежнее состояние под новым номером не читается ---
    def review_get(self, case_id: str) -> tuple[dict[str, Any], int] | None:
        found = self.inner.review_get(case_id)
        if found is None:
            return None
        body, version = found
        return self.sealer.open(body, review_aad(case_id, version)), version

    def review_put(self, case_id: str, state: dict[str, Any], expected_version: int) -> int:
        sealed = self.sealer.seal(state, review_aad(case_id, expected_version + 1))
        return self.inner.review_put(case_id, sealed, expected_version)

    def review_list(self) -> list[tuple[str, dict[str, Any], int]]:
        return [
            (case_id, self.sealer.open(body, review_aad(case_id, version)), version)
            for case_id, body, version in self.inner.review_list()
        ]

    # --- без ПДн ---
    def allow(self, client: str, limit: int, now: float | None = None) -> bool:
        return self.inner.allow(client, limit, now)

    def first_seen(self, key: str, ttl: float, now: float | None = None) -> bool:
        return self.inner.first_seen(key, ttl, now)

    def claim(self, key: str, value: str, ttl: float, now: float | None = None) -> str | None:
        return self.inner.claim(key, value, ttl, now)

    def release(self, key: str) -> None:
        self.inner.release(key)

    def ping(self) -> None:
        self.inner.ping()

    def idem_get(self, owner: str, key: str) -> tuple[str, str] | None:
        return self.inner.idem_get(owner, key)

    def watermark_get(self, key: str) -> int | None:
        return self.inner.watermark_get(key)

    def watermark_raise(self, key: str, value: int) -> int:
        return self.inner.watermark_raise(key, value)


def sealed(store: Store) -> Store:
    """Обернуть хранилище шифром, если ключ задан; уже обёрнутое не оборачивается второй раз."""
    if isinstance(store, SealedStore):
        return store
    sealer = load_sealer()
    return store if sealer is None else SealedStore(store, sealer)
