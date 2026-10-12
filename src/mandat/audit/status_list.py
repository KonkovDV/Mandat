"""Отзыв и приостановка удостоверений «Мандата» по W3C Bitstring Status List v1.0.

Удостоверение о проверке полномочия живёт дольше самой проверки: МЧД могут отменить,
руководитель может смениться. Поэтому в каждом удостоверении есть ``credentialStatus``
с номером бита в двух списках — «отозвано» и «приостановлено». Мониторинг выставляет
бит по событию, проверяющая сторона читает подписанный список и видит актуальный статус
без обращения к базе «Мандата».

Формат по спецификации (Рекомендация W3C от 15.05.2025): битовая строка не короче
131 072 бит (16 КБ), бит с индексом 0 — старший бит первого байта, строка сжимается
GZIP и кодируется multibase base64url без выравнивания (префикс ``u``).
Номера выдаются случайно, чтобы по номеру нельзя было судить о порядке выдачи.
"""

from __future__ import annotations

import base64
import gzip
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mandat.audit import attestation

MIN_BITS = 131_072
PURPOSES = ("revocation", "suspension")
BASE_URL = "https://mandat.example/status"


def encode_bits(bits: bytearray) -> str:
    return "u" + base64.urlsafe_b64encode(gzip.compress(bytes(bits), mtime=0)).decode().rstrip("=")


def decode_bits(encoded: str) -> bytearray:
    if not encoded.startswith("u"):
        raise ValueError("ожидалась multibase-строка base64url с префиксом u")
    body = encoded[1:]
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    return bytearray(gzip.decompress(raw))


def get_bit(bits: bytearray, index: int) -> bool:
    return bool(bits[index // 8] >> (7 - index % 8) & 1)


def set_bit(bits: bytearray, index: int, value: bool = True) -> None:
    mask = 1 << (7 - index % 8)
    if value:
        bits[index // 8] |= mask
    else:
        bits[index // 8] &= ~mask & 0xFF


def list_url(purpose: str) -> str:
    return f"{BASE_URL}/{purpose}"


@dataclass
class StatusRegistry:
    """Состояние списков и сведения о выданных номерах. Хранится в каталоге ``status/``."""

    directory: Path
    size: int = MIN_BITS
    epoch: str = ""
    lists: dict[str, bytearray] = field(default_factory=dict)
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)  # индекс → сведения об удостоверении

    @classmethod
    def open(cls, directory: Path) -> StatusRegistry:
        state = directory / "registry.json"
        if not state.exists():
            reg = cls(directory, epoch=secrets.token_hex(8))
            reg.lists = {p: bytearray(MIN_BITS // 8) for p in PURPOSES}
            return reg
        payload = json.loads(state.read_text(encoding="utf-8"))
        reg = cls(directory, size=int(payload["size"]), epoch=str(payload.get("epoch") or secrets.token_hex(8)))
        reg.lists = {p: decode_bits(payload["lists"][p]) for p in PURPOSES}
        reg.entries = {str(k): v for k, v in payload["entries"].items()}
        return reg

    def allocate(self, meta: dict[str, Any]) -> int:
        while True:
            index = secrets.randbelow(self.size)
            if str(index) not in self.entries:
                self.entries[str(index)] = dict(meta)
                return index

    def credential_status(self, index: int) -> list[dict[str, str]]:
        return [
            {
                "id": f"{list_url(p)}#{index}",
                "type": "BitstringStatusListEntry",
                "statusPurpose": p,
                "statusListIndex": str(index),
                "statusListCredential": list_url(p),
                "epoch": self.epoch,
            }
            for p in PURPOSES
        ]

    def mark(self, index: int, purpose: str, reason: str) -> bool:
        """Выставляет бит; возвращает True, если статус изменился."""
        bits = self.lists[purpose]
        if get_bit(bits, index):
            return False
        set_bit(bits, index)
        self.entries[str(index)].setdefault("history", []).append({"purpose": purpose, "reason": reason})
        return True

    def find(self, *, org_inn: str, poa_number: str | None = None) -> list[int]:
        """Номера удостоверений организации, по которым выданы или рекомендованы права (не опровергнутые)."""
        return sorted(
            int(i)
            for i, meta in self.entries.items()
            if meta.get("org_inn") == org_inn
            and meta.get("verdict") != "refuted"
            and (poa_number is None or poa_number in meta.get("poa_numbers", []))
        )

    def save(self, key: Ed25519PrivateKey, *, now: datetime | None = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        state = {
            "size": self.size,
            "epoch": self.epoch,
            "lists": {p: encode_bits(b) for p, b in self.lists.items()},
            "entries": self.entries,
        }
        (self.directory / "registry.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        for purpose in PURPOSES:
            cred = status_list_credential(purpose, self.lists[purpose], key, epoch=self.epoch, now=now)
            (self.directory / f"{purpose}.json").write_text(attestation.dump(cred), encoding="utf-8")


def status_list_credential(
    purpose: str,
    bits: bytearray,
    key: Ed25519PrivateKey,
    *,
    epoch: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    created = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    document = {
        "@context": attestation.CONTEXTS,
        "id": list_url(purpose),
        "type": ["VerifiableCredential", "BitstringStatusListCredential"],
        "issuer": attestation.ISSUER,
        "validFrom": created,
        "credentialSubject": {
            "id": f"{list_url(purpose)}#list",
            "type": "BitstringStatusList",
            "statusPurpose": purpose,
            "epoch": epoch,
            "encodedList": encode_bits(bits),
        },
    }
    return attestation.sign(document, key, now=now)


def check_status(credential: dict[str, Any], status_dir: Path, public_key: Ed25519PublicKey) -> dict[str, bool | str]:
    """Статус удостоверения по спискам из каталога. Подпись каждого списка проверяется."""
    result: dict[str, bool | str] = {}
    for entry in credential.get("credentialStatus") or []:
        purpose = entry["statusPurpose"]
        path = status_dir / f"{purpose}.json"
        status_cred = json.loads(path.read_text(encoding="utf-8"))
        if status_cred.get("id") != entry["statusListCredential"]:
            raise ValueError(f"список {path} не тот, на который ссылается удостоверение")
        if not attestation.verify(status_cred, public_key):
            raise ValueError(f"подпись списка {path} не верна")
        subject = status_cred.get("credentialSubject") or {}
        if entry.get("epoch") and subject.get("epoch") != entry.get("epoch"):
            raise ValueError(f"список {path} из другой эпохи, чем удостоверение")
        issued = datetime.fromisoformat(str(status_cred.get("validFrom")))
        if issued.tzinfo is None:
            raise ValueError(f"у списка {path} нет часового пояса")
        bits = decode_bits(subject["encodedList"])
        result[purpose] = get_bit(bits, int(entry["statusListIndex"]))
        result[f"{purpose}_issued"] = issued.isoformat()
    if not result:
        return {"state": "без статуса"}
    issued_values = [
        datetime.fromisoformat(str(result[key]))
        for key in result
        if str(key).endswith("_issued")
    ]
    now = datetime.now(UTC)
    future = any(item > now + timedelta(minutes=5) for item in issued_values)
    stale = any(now - item > timedelta(days=7) for item in issued_values)
    revoked = bool(result.get("revocation"))
    suspended = bool(result.get("suspension"))
    if future:
        state = "список из будущего"
    elif stale and revoked:
        state = "отозвано, список устарел"
    elif stale:
        state = "список устарел"
    elif revoked:
        state = "отозвано"
    elif suspended:
        state = "приостановлено"
    else:
        state = "действует"
    return {**result, "state": state}
