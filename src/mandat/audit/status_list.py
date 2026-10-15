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
import fcntl
import gzip
import json
import os
import secrets
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mandat.audit import attestation
from mandat.audit.signing import Signer
from mandat.domain.identifiers import normalize_poa_number

MIN_BITS = 131_072
# Предел распаковки: сжатый список в несколько килобайт не должен раздуваться в гигабайты.
MAX_LIST_BYTES = 1 << 20
PURPOSES = ("revocation", "suspension")
BASE_URL = "https://mandat.example/status"

# Максимальный возраст подписанного списка при проверке с ``now`` (дни). Банк задаёт
# ``MANDAT_STATUS_MAX_AGE_DAYS`` под риск операции; 0 = любой возраст недопустим (кроме exact now).
DEFAULT_STATUS_MAX_AGE_DAYS = 7


class StaleRegistryError(RuntimeError):
    """Конкурирующая запись: локальный снимок устарел относительно диска."""


def status_max_age_days() -> float:
    raw = os.environ.get("MANDAT_STATUS_MAX_AGE_DAYS", "").strip()
    if not raw:
        return float(DEFAULT_STATUS_MAX_AGE_DAYS)
    return float(raw)


def _lock_path(directory: Path) -> Path:
    return directory / ".registry.lock"


class _RegistryLock:
    """Межпроцессная блокировка каталога статусов (fcntl Exclusive)."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._path = _lock_path(directory)
        self._fh: Any = None

    def __enter__(self) -> _RegistryLock:
        self._fh = open(self._path, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._fh is not None
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def published_root(directory: Path) -> Path:
    """Каталог согласованного снимка: snapshots/{CURRENT} или плоский legacy."""
    current = directory / "CURRENT"
    if current.exists():
        seq = current.read_text(encoding="utf-8").strip()
        snap = directory / "snapshots" / seq
        if (snap / "registry.json").exists():
            return snap
    return directory


def encode_bits(bits: bytearray) -> str:
    return "u" + base64.urlsafe_b64encode(gzip.compress(bytes(bits), mtime=0)).decode().rstrip("=")


def decode_bits(encoded: str) -> bytearray:
    if not encoded.startswith("u"):
        raise ValueError("ожидалась multibase-строка base64url с префиксом u")
    body = encoded[1:]
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    inflater = zlib.decompressobj(wbits=31)
    try:
        bits = inflater.decompress(raw, MAX_LIST_BYTES + 1)
    except zlib.error as exc:
        raise ValueError(f"список статусов не распаковывается: {exc}") from exc
    if len(bits) > MAX_LIST_BYTES or inflater.unconsumed_tail:
        raise ValueError(f"список статусов больше {MAX_LIST_BYTES} байт после распаковки")
    return bytearray(bits)


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
    sequence: int = 0  # растёт при каждом save: старый подписанный список нельзя подложить вместо нового
    lists: dict[str, bytearray] = field(default_factory=dict)
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)  # индекс → сведения об удостоверении

    @classmethod
    def open(cls, directory: Path) -> StatusRegistry:
        root = published_root(directory)
        state = root / "registry.json"
        if not state.exists():
            reg = cls(directory, epoch=secrets.token_hex(8))
            reg.lists = {p: bytearray(MIN_BITS // 8) for p in PURPOSES}
            return reg
        payload = json.loads(state.read_text(encoding="utf-8"))
        size = int(payload["size"])
        if size < MIN_BITS or size % 8:
            raise ValueError(f"список статусов меньше {MIN_BITS} бит: групповая приватность не обеспечена")
        reg = cls(
            directory,
            size=size,
            epoch=str(payload.get("epoch") or secrets.token_hex(8)),
            sequence=int(payload.get("sequence") or 0),
        )
        reg.lists = {p: decode_bits(payload["lists"][p]) for p in PURPOSES}
        if any(len(bits) * 8 != size for bits in reg.lists.values()):
            raise ValueError("длина списка статусов не совпадает с размером в registry.json")
        reg.entries = {str(k): v for k, v in payload["entries"].items()}
        return reg

    def allocate(self, meta: dict[str, Any]) -> int:
        """Случайный свободный индекс из CSPRNG: по номеру не видно ни порядка выдачи, ни числа удостоверений.

        Так советует W3C Bitstring Status List (раздел о statusListIndex). Занятые индексы не выдаются повторно.
        """
        if len(self.entries) >= self.size:
            raise RuntimeError("в списке статусов не осталось свободных индексов: нужна новая эпоха")
        for _ in range(64):
            index = secrets.randbelow(self.size)
            if str(index) not in self.entries:
                break
        else:
            taken = {int(i) for i in self.entries}
            index = secrets.choice([i for i in range(self.size) if i not in taken])
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
        """Выставляет бит; возвращает True, если статус изменился.

        Индекс должен быть выдан через ``allocate``: иначе список не меняется. Отрицательный
        индекс в Python адресовал бы конец строки, поэтому он отвергается до записи.
        """
        if not 0 <= index < self.size or str(index) not in self.entries:
            raise ValueError(f"индекс {index} не выдан: статус не меняется")
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
            and (
                poa_number is None
                or normalize_poa_number(poa_number) in {normalize_poa_number(n) for n in meta.get("poa_numbers", [])}
            )
        )

    def _merge_from_disk(self, disk: StatusRegistry) -> None:
        """Монотонное слияние: биты revocation/suspension — OR; entries объединяются."""
        if disk.epoch and self.epoch and disk.epoch != self.epoch:
            raise ValueError(f"эпоха на диске {disk.epoch} не совпадает с {self.epoch}")
        for purpose in PURPOSES:
            ours = self.lists[purpose]
            theirs = disk.lists[purpose]
            if len(ours) != len(theirs):
                raise ValueError("длина списков статусов при слиянии не совпадает")
            for i, (a, b) in enumerate(zip(ours, theirs, strict=True)):
                ours[i] = a | b
        for idx, meta in disk.entries.items():
            if idx not in self.entries:
                self.entries[idx] = dict(meta)
            else:
                # История отзыва не должна теряться: объединяем history.
                hist = list(self.entries[idx].get("history") or [])
                seen = {(h.get("purpose"), h.get("reason")) for h in hist}
                for item in meta.get("history") or []:
                    key = (item.get("purpose"), item.get("reason"))
                    if key not in seen:
                        hist.append(item)
                        seen.add(key)
                merged = {**meta, **self.entries[idx]}
                if hist:
                    merged["history"] = hist
                self.entries[idx] = merged
        self.sequence = max(self.sequence, disk.sequence)

    def save(
        self,
        key: Ed25519PrivateKey | Signer,
        *,
        now: datetime | None = None,
        conflict: str = "merge",
    ) -> None:
        """Атомарная публикация registry + двух списков под межпроцессной блокировкой.

        ``conflict``:
          * ``merge`` — OR-слияние битов отзыва/приостановки при stale writer (по умолчанию);
          * ``error`` — ``StaleRegistryError``, диск не затирается.

        Снимок пишется в ``snapshots/{sequence}/``, затем атомарно обновляется ``CURRENT``,
        затем зеркалируется в плоские файлы для читателей. Crash до CURRENT оставляет
        предыдущий согласованный указатель (M-02).
        """
        if conflict not in {"merge", "error"}:
            raise ValueError("conflict: merge или error")
        with _RegistryLock(self.directory):
            root = published_root(self.directory)
            state_path = root / "registry.json"
            if state_path.exists():
                disk = StatusRegistry.open(self.directory)
                if disk.sequence != self.sequence:
                    if conflict == "error":
                        raise StaleRegistryError(
                            f"устаревший снимок sequence={self.sequence}, на диске {disk.sequence}"
                        )
                    self._merge_from_disk(disk)
            self.sequence += 1
            state = {
                "size": self.size,
                "epoch": self.epoch,
                "sequence": self.sequence,
                "lists": {p: encode_bits(b) for p, b in self.lists.items()},
                "entries": self.entries,
            }
            snap = self.directory / "snapshots" / str(self.sequence)
            snap.mkdir(parents=True, exist_ok=True)
            _atomic_write(snap / "registry.json", json.dumps(state, ensure_ascii=False, indent=2))
            for purpose in PURPOSES:
                cred = status_list_credential(
                    purpose, self.lists[purpose], key, epoch=self.epoch, now=now, sequence=self.sequence
                )
                _atomic_write(snap / f"{purpose}.json", attestation.dump(cred))
            # Указатель на полный снимок — только после записи всех трёх файлов.
            _atomic_write(self.directory / "CURRENT", f"{self.sequence}\n")
            # Плоское зеркало для check_status / внешних читателей.
            for name in ("registry.json", *(f"{p}.json" for p in PURPOSES)):
                _atomic_write(self.directory / name, (snap / name).read_text(encoding="utf-8"))


def status_list_credential(
    purpose: str,
    bits: bytearray,
    key: Ed25519PrivateKey | Signer,
    *,
    epoch: str = "",
    now: datetime | None = None,
    sequence: int = 0,
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
            "sequence": sequence,
            "encodedList": encode_bits(bits),
        },
    }
    return attestation.sign(document, key, now=now)


def _verify_any(status_cred: dict[str, Any], keys: Ed25519PublicKey | Iterable[Ed25519PublicKey]) -> bool:
    """Список переподписывается текущим ключом: после ротации он подписан не тем ключом, что удостоверение."""
    candidates = [keys] if isinstance(keys, Ed25519PublicKey) else list(keys)
    method = str((status_cred.get("proof") or {}).get("verificationMethod") or "")
    kid = method.split("#", 1)[-1]
    return any(attestation.public_key_multibase(k) == kid and attestation.verify(status_cred, k) for k in candidates)


def check_status(
    credential: dict[str, Any],
    status_dir: Path,
    public_key: Ed25519PublicKey | Iterable[Ed25519PublicKey],
    *,
    seen: dict[str, int] | None = None,
    now: datetime | None = None,
) -> dict[str, bool | str]:
    """Статус удостоверения по спискам из каталога. Подпись каждого списка проверяется.

    ``seen`` — состояние проверяющего: «id списка → наибольший виденный ``sequence``». Список
    старше уже виденного отвергается: так ловится подложенный после отзыва старый, но правильно
    подписанный список. Словарь обновляется на месте; хранит его проверяющий, не издатель.

    Свежесть по часам проверяется, только если передан ``now``: демо должно проверяться и через месяц.
    """
    result: dict[str, bool | str] = {}
    root = published_root(status_dir)
    issuer_state = root / "registry.json"
    issuer_sequence = -1
    if issuer_state.exists():
        # Список старше состояния издателя в том же каталоге — подложен: подписан верно, но устарел.
        issuer_sequence = int(json.loads(issuer_state.read_text(encoding="utf-8")).get("sequence") or -1)
    for entry in credential.get("credentialStatus") or []:
        purpose = entry["statusPurpose"]
        path = root / f"{purpose}.json"
        status_cred = json.loads(path.read_text(encoding="utf-8"))
        if status_cred.get("id") != entry["statusListCredential"]:
            raise ValueError(f"список {path} не тот, на который ссылается удостоверение")
        if not _verify_any(status_cred, public_key):
            raise ValueError(f"подпись списка {path} не верна")
        subject = status_cred.get("credentialSubject") or {}
        if entry.get("epoch") and subject.get("epoch") != entry.get("epoch"):
            raise ValueError(f"список {path} из другой эпохи, чем удостоверение")
        listed = subject.get("sequence")
        if issuer_sequence >= 0 and isinstance(listed, int) and listed < issuer_sequence:
            raise ValueError("список статусов старше состояния издателя: подложен устаревший список")
        if seen is not None:
            sequence = subject.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError(f"у списка {path} нет номера sequence: свежесть не проверить")
            list_id = f"{status_cred.get('id')}#{subject.get('epoch', '')}"
            if sequence < seen.get(list_id, -1):
                raise ValueError("список статусов старше уже проверенного")
            seen[list_id] = max(sequence, seen.get(list_id, -1))
        issued = datetime.fromisoformat(str(status_cred.get("validFrom")))
        if issued.tzinfo is None:
            raise ValueError(f"у списка {path} нет часового пояса")
        bits = decode_bits(subject["encodedList"])
        if len(bits) * 8 < MIN_BITS:
            raise ValueError(f"список {path} короче {MIN_BITS} бит (STATUS_LIST_LENGTH_ERROR)")
        position = int(entry["statusListIndex"])
        if not 0 <= position < len(bits) * 8:
            raise ValueError(f"индекс {position} вне списка {path} (RANGE_ERROR)")
        result[purpose] = get_bit(bits, position)
        result[f"{purpose}_issued"] = issued.isoformat()
    if not result:
        return {"state": "без статуса"}
    issued_values = [datetime.fromisoformat(str(result[key])) for key in result if str(key).endswith("_issued")]
    future = now is not None and any(item > now + timedelta(minutes=5) for item in issued_values)
    max_age = status_max_age_days()
    stale = now is not None and any(now - item > timedelta(days=max_age) for item in issued_values)
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
