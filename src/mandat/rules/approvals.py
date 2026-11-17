"""Подписанные записи утверждения правил (B1, R15).

Строка ``approved`` в ``rules.json`` сама по себе ничего не значит: кто поменял файл, тот и «утвердил».
Правило утверждено, только если в файле утверждений есть действующая запись юриста банка:

- запись подписана Ed25519 по канонической записи RFC 8785 ключом из связки утверждающих
  (``MANDAT_APPROVAL_KEYS``), и этот ключ действовал в момент подписи;
- в записи хеш нормативного текста правила (:func:`rule_text_sha256`): правка ``check``, ``title``,
  ``norm_ref`` или ``effects`` после подписи снимает утверждение;
- запись того же пакета правил, дата подписи не в будущем, срок не истёк и не длиннее года.

Подпись защищает каждую запись, но не полноту набора. Удалить запись может любой, кто пишет в файл, и это
безопасная сторона: правило становится черновиком. Вернуть удалённую запись тоже можно, пока не истёк её срок;
поэтому срок не длиннее года, а отозвать все записи юриста разом — значит пометить его ключ ``revoked``.

В банке запись подписывает КЭП юриста через СКЗИ; на стенде тот же контракт на Ed25519 (docs/KEYS.md).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature

from mandat.audit.attestation import b58decode, b58encode
from mandat.audit.jcs import JcsError, canonicalize
from mandat.audit.signing import Keyring, Signer, checked_sign
from mandat.data import load_json
from mandat.rules.pack import RulePack, RuleSpec
from mandat.strictjson import strict_loads

APPROVALS_ENV = "MANDAT_APPROVALS"
KEYS_ENV = "MANDAT_APPROVAL_KEYS"
RECORD_TYPE = "mandat-rule-approval"
RECORD_FIELDS = frozenset(
    {
        "type",
        "pack_id",
        "rule_id",
        "rule_sha256",
        "decision",
        "norm_edition",
        "norm_checked_on",
        "approver_role",
        "signed_at",
        "valid_until",
        "key_id",
        "signature",
    }
)
MAX_VALIDITY = timedelta(days=366)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_FILE_BYTES = 1024 * 1024
MAX_RECORDS = 1000
MAX_TEXT = 300
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class ApprovalError(ValueError):
    """Файл утверждений или связка ключей не читаются."""


def rule_text(spec: RuleSpec) -> dict[str, Any]:
    """То, что юрист утверждает: группа, название, норма и эффекты правила.

    Параметры (``params``) — настройки банка, подразделение и следующий шаг — операционные поля: их правка
    утверждение не снимает. Логику правила держит код и тесты из поля ``tests``, подпись её не покрывает.
    """
    return {
        "id": spec.id,
        "check": spec.check,
        "title": spec.title,
        "norm_ref": spec.norm_ref,
        "effects": list(spec.effects),
    }


def rule_text_sha256(spec: RuleSpec) -> str:
    return hashlib.sha256(canonicalize(rule_text(spec)).encode("utf-8")).hexdigest()


def _signed_bytes(record: dict[str, Any]) -> bytes:
    return canonicalize({k: v for k, v in record.items() if k != "signature"}).encode("utf-8")


def sign_record(
    spec: RuleSpec,
    pack_id: str,
    signer: Signer,
    *,
    approver_role: str,
    norm_edition: str,
    norm_checked_on: date,
    valid_until: date,
    signed_at: datetime | None = None,
) -> dict[str, Any]:
    """Запись утверждения правила ``spec``, подписанная ``signer``. Проверка — :func:`check_record`."""
    moment = signed_at or datetime.now(UTC)
    record: dict[str, Any] = {
        "type": RECORD_TYPE,
        "pack_id": pack_id,
        "rule_id": spec.id,
        "rule_sha256": rule_text_sha256(spec),
        "decision": "approve",
        "norm_edition": norm_edition,
        "norm_checked_on": norm_checked_on.isoformat(),
        "approver_role": approver_role,
        "signed_at": moment.isoformat(timespec="seconds"),
        "valid_until": valid_until.isoformat(),
        "key_id": signer.key_id,
    }
    record["signature"] = "z" + b58encode(checked_sign(signer, _signed_bytes(record)))
    return record


def _text(record: dict[str, Any], name: str) -> str | None:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        return None
    return value


def _day(record: dict[str, Any], name: str) -> date | None:
    value = record.get(name)
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_record(record: Any, pack: RulePack, keyring: Keyring | None, now: datetime) -> str | None:
    """Почему запись не утверждает правило. ``None`` — запись действует."""
    if not isinstance(record, dict):
        return "запись не объект JSON"
    extra = set(record) - RECORD_FIELDS
    if extra or set(record) != RECORD_FIELDS:
        missing = sorted(RECORD_FIELDS - set(record))
        return f"поля записи не по контракту: лишние {sorted(extra)[:5]}, нет {missing[:5]}"
    if record["type"] != RECORD_TYPE or record["decision"] != "approve":
        return "запись не об утверждении правила"
    if record["pack_id"] != pack.pack_id:
        return f"запись другого пакета правил ({str(record['pack_id'])[:60]!r})"
    rule_id = record["rule_id"]
    if not isinstance(rule_id, str) or rule_id not in pack.rules:
        return "правила с таким id нет в пакете"
    spec = pack.rules[rule_id]
    if spec.status == "retired":
        return "правило выведено из пакета (retired)"
    digest = record["rule_sha256"]
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        return "rule_sha256 — 64 шестнадцатеричных знака"
    if digest != rule_text_sha256(spec):
        return "текст правила изменён после подписи: нужна новая запись"
    for name in ("norm_edition", "approver_role", "key_id", "signature"):
        if _text(record, name) is None:
            return f"{name}: непустая строка не длиннее {MAX_TEXT} знаков"
    checked_on, valid_until = _day(record, "norm_checked_on"), _day(record, "valid_until")
    if checked_on is None or valid_until is None:
        return "norm_checked_on и valid_until — даты ГГГГ-ММ-ДД"
    try:
        signed_at = datetime.fromisoformat(str(record["signed_at"]))
    except ValueError:
        return "signed_at — дата и время с часовым поясом"
    if signed_at.tzinfo is None:
        return "signed_at — дата и время с часовым поясом"
    if signed_at > now + MAX_CLOCK_SKEW:
        return "дата подписи в будущем"
    if checked_on > signed_at.date():
        return "норма сверена позже подписи"
    if valid_until < signed_at.date() or valid_until - signed_at.date() > MAX_VALIDITY:
        return "срок записи — от даты подписи и не длиннее года"
    if now.date() > valid_until:
        return f"срок записи истёк {valid_until.isoformat()}"
    if keyring is None:
        return f"связка ключей утверждающих не задана ({KEYS_ENV})"
    signature = str(record["signature"])
    try:
        raw_signature = b58decode(signature[1:]) if signature.startswith("z") else b""
        payload = _signed_bytes(record)
    except (ValueError, JcsError):
        return "подпись не читается"
    entry = next((e for e in keyring.entries if e.kid == record["key_id"]), None)
    if entry is not None and any(key is entry.public for key in keyring.trusted_for(signed_at)):
        try:
            entry.public.verify(raw_signature, payload)
        except (InvalidSignature, ValueError):
            return "подпись не совпадает"
        return None
    return "ключ не из связки утверждающих или не действовал в момент подписи"


@dataclass(frozen=True)
class Approvals:
    """Набор записей и связка ключей. ``source`` — откуда взят набор, для отчёта."""

    records: tuple[Any, ...]
    keyring: Keyring | None
    sha256: str
    source: str


def _read(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ApprovalError(f"{path.name}: не читается ({exc.strerror})") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ApprovalError(f"{path.name}: больше {MAX_FILE_BYTES} байт")
    try:
        return strict_loads(raw)
    except ValueError as exc:
        raise ApprovalError(f"{path.name}: не строгий JSON") from exc


def _read_only_anchor(path: Path) -> None:
    """Связка утверждающих — якорь доверия: кто пишет в неё, тот утверждает любое правило своим ключом.

    В pilot, prod и любом защищённом режиме файл не должен быть доступен на запись группе, всем и самому
    сервису: его монтируют только для чтения, а меняет служба безопасности, а не процесс «Мандата».
    """
    from mandat.services.env_policy import protected

    if not protected():
        return
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ApprovalError(f"{path.name}: не читается ({exc.strerror})") from exc
    if mode & 0o022 or os.access(path, os.W_OK):
        raise ApprovalError(
            f"{path.name}: связка ключей утверждающих доступна на запись; в защищённом режиме — только чтение"
        )


def load_approvals(path: Path | None = None, keys: Path | None = None) -> Approvals:
    """Записи из ``path`` (по умолчанию ``MANDAT_APPROVALS`` или пустой файл пакета) и связка из ``keys``."""
    raw_path = path or (Path(p) if (p := os.environ.get(APPROVALS_ENV, "").strip()) else None)
    raw_keys = keys or (Path(k) if (k := os.environ.get(KEYS_ENV, "").strip()) else None)
    payload = _read(raw_path) if raw_path is not None else load_json("approvals.json")
    if not isinstance(payload, dict) or payload.get("schema") != 1 or not isinstance(payload.get("approvals"), list):
        raise ApprovalError('файл утверждений: ожидался {"schema": 1, "approvals": [...]}')
    records = payload["approvals"]
    if len(records) > MAX_RECORDS:
        raise ApprovalError(f"файл утверждений: больше {MAX_RECORDS} записей")
    keyring = None
    if raw_keys is not None:
        _read_only_anchor(raw_keys)
        try:
            keyring = Keyring.parse(_read(raw_keys))
        except ValueError as exc:
            raise ApprovalError(f"связка ключей утверждающих: {exc}") from exc
    try:
        digest = hashlib.sha256(canonicalize(payload).encode("utf-8")).hexdigest()
    except JcsError as exc:
        raise ApprovalError(f"файл утверждений: {exc}") from exc
    return Approvals(tuple(records), keyring, digest, str(raw_path) if raw_path else "пакет: approvals.json")


def effective_pack(pack: RulePack, now: datetime, approvals: Approvals | None = None) -> tuple[RulePack, list[str]]:
    """Пакет со статусами по подписанным записям и предупреждения о записях, которые не приняты.

    ``approved`` получает только правило с действующей записью. Остальные ``approved`` становятся ``draft``,
    ``retired`` остаётся ``retired``. Нечитаемый файл утверждений не роняет проверку: все правила — черновики,
    а причина — в предупреждении.
    """
    notes: list[str] = []
    try:
        loaded = approvals or load_approvals()
    except ApprovalError as exc:
        loaded, notes = None, [f"Утверждения правил не прочитаны ({exc}): все правила считаются черновиками"]
    approved: set[str] = set()
    for record in loaded.records if loaded else ():
        reason = check_record(record, pack, loaded.keyring if loaded else None, now)
        rule = record.get("rule_id") if isinstance(record, dict) else None
        label = rule if isinstance(rule, str) and rule in pack.rules else "запись"
        if reason is None:
            approved.add(str(rule))
        else:
            notes.append(f"Утверждение {label} не принято: {reason}")
    rules: dict[str, RuleSpec] = {}
    for rule_id, spec in pack.rules.items():
        status = "retired" if spec.status == "retired" else "approved" if rule_id in approved else "draft"
        rules[rule_id] = spec if spec.status == status else replace(spec, status=status)
    if not approved and all(rules[r] is pack.rules[r] for r in rules):
        return pack, notes
    summary = {"sha256": loaded.sha256, "approved": sorted(approved)} if loaded and approved else None
    return replace(pack, rules=rules, approvals=summary), notes
