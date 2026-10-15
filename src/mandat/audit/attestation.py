"""Подписанное удостоверение о результате проверки полномочия (в духе W3C VC 2.0).

Идея перенесена из fsp-talent (ADR-0007: удостоверения W3C VC с подписью Ed25519).
Подпись проверяется без доступа к базе «Мандата»: нужен только открытый ключ.

Схема подписи повторяет криптонабор ``eddsa-jcs-2022``: хеш канонической
конфигурации доказательства и хеш канонического документа, подпись Ed25519,
значение в base58btc с префиксом ``z``. Каноническая запись — RFC 8785 (``audit/jcs.py``,
проверена на примерах из текста RFC). Для ключей ASCII, строк и целых она совпадает с прежней
записью ``sort_keys``, поэтому подпись ранее выданных удостоверений проверяется без переподписи.
Принимаются они не будут: в них нет ``validUntil`` (тест ``test_attestation_signed_before_rfc8785_*``).
Совместимость с внешними верификаторами VC не проверялась. Ed25519 — не квалифицированная
электронная подпись по 63-ФЗ и не ГОСТ.

``verify_signature`` отвечает только на вопрос «подписано этим ключом».
``validate_attestation`` проверяет подлинность удостоверения (издатель, срок, профиль, статус).
``authorize_operation`` — отдельный контракт допуска операции: подлинность + вердикт confirmed.
Успех проверки подписи или подлинности сам по себе не является банковским допуском операции.

В удостоверение не попадают ФИО и паспортные данные: только ИНН организации, токен представителя
(HMAC-SHA256 на ключе издателя, а не хеш ИНН: хеш 12 цифр перебирается словарём), операция,
вердикт, номера МЧД цепочки и хеши входов.
Статус удостоверения (отозвано, приостановлено) — по спискам W3C Bitstring Status List
(``audit/status_list.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mandat.audit.jcs import canonicalize
from mandat.audit.signing import Keyring, Signer, as_signer, checked_sign, key_id

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ISSUER = "did:web:mandat.example"
CONTEXTS = ["https://www.w3.org/ns/credentials/v2"]
CRYPTOSUITE = "eddsa-jcs-2022"
PURPOSE = "assertionMethod"
VALID_FOR = timedelta(days=30)  # срок принятия удостоверения; параметр банка


def b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58[rem] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    num = 0
    for char in text:
        num = num * 58 + _B58.index(char)
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def _private_bytes(path: Path) -> bytes:
    """Байты секретного ключа. В POSIX файл, открытый группе или всем, не читается: ключ уже мог утечь."""
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError(f"ключ {path.name} доступен не только владельцу: выполните chmod 600 {path}")
    return path.read_bytes()


def _read_signing_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(_private_bytes(path), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("ключ подписи должен быть Ed25519")
    return key


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    """Открытый PEM: только demo/dev/test. В защищённом режиме — ``signing.signer_from_env`` (RT-05)."""
    from mandat.services.env_policy import protected

    if protected():
        raise ValueError("ключ подписи в открытом PEM в защищённом режиме запрещён: см. docs/KEYS.md")
    if path.exists():
        return _read_signing_key(path)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return key if _write_private(path, pem) else _read_signing_key(path)


def _write_private(path: Path, data: bytes) -> bool:
    """Новый файл ключа с правами 0600 (в Windows права POSIX не действуют).

    Байты пишутся во временный файл в том же каталоге (двоичный режим, без замены перевода
    строки), сбрасываются на диск и только потом публикуются под именем ключа жёсткой ссылкой.
    Поэтому читатель видит либо полный ключ, либо его отсутствие. Существующий файл не
    перезаписывается: если его успел создать другой процесс, возвращается False, и вызывающий
    читает уже записанный ключ.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        tmp.unlink(missing_ok=True)


def public_key_multibase(key: Ed25519PrivateKey | Ed25519PublicKey | Signer) -> str:
    if isinstance(key, Ed25519PublicKey):
        return key_id(key)
    return as_signer(key).key_id  # multicodec ed25519-pub


def _hash_data(document: dict[str, Any], proof_config: dict[str, Any]) -> bytes:
    return (
        hashlib.sha256(canonicalize(proof_config).encode("utf-8")).digest()
        + hashlib.sha256(canonicalize(document).encode("utf-8")).digest()
    )


def sign(document: dict[str, Any], key: Ed25519PrivateKey | Signer, *, now: datetime | None = None) -> dict[str, Any]:
    """Доказательство Data Integrity в духе ``eddsa-jcs-2022`` для любого JSON-документа."""
    signer = as_signer(key)
    created = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    proof_config = {
        "@context": CONTEXTS,
        "type": "DataIntegrityProof",
        "cryptosuite": CRYPTOSUITE,
        "created": created,
        # Идентификатор ключа (kid) в подписанной части: по нему проверяющий берёт ключ из связки.
        "verificationMethod": f"{ISSUER}#{signer.key_id}",
        "proofPurpose": PURPOSE,
    }
    signature = checked_sign(signer, _hash_data(document, proof_config))
    return {**document, "proof": {**proof_config, "proofValue": "z" + b58encode(signature)}}


def issue(
    report: dict[str, Any],
    key: Ed25519PrivateKey | Signer,
    *,
    now: datetime | None = None,
    status: list[dict[str, str]] | None = None,
    pseudonym_key: bytes | None = None,
    valid_for: timedelta = VALID_FOR,
) -> dict[str, Any]:
    """Удостоверение по отчёту. Без ``pseudonym_key`` представитель в удостоверение не попадает."""
    moment = now or datetime.now(UTC)
    created = moment.isoformat(timespec="seconds")
    rep_inn = (report.get("requester") or {}).get("inn") or ""
    subject: dict[str, Any] = {
        "organizationInn": (report.get("organization") or {}).get("inn"),
        "operation": (report.get("operation") or {}).get("code"),
        "operationDate": (report.get("operation") or {}).get("date"),
        "verdict": report.get("verdict"),
        "profile": report.get("profile"),
        "ruleSetSha256": (report.get("rule_pack") or {}).get("sha256"),
        "reportSha256": report.get("reproducibility_hash"),
        "authorityChain": list((report.get("chain") or {}).get("poa_numbers") or []),
        "inputs": [row.get("sha256") for row in report.get("inputs") or []],
    }
    if pseudonym_key and rep_inn:
        subject["representativeToken"] = representative_token(rep_inn, pseudonym_key)
    credential: dict[str, Any] = {
        "@context": CONTEXTS,
        "type": ["VerifiableCredential", "MandatAuthorityAttestation"],
        "issuer": ISSUER,
        "validFrom": created,
        "validUntil": (moment + valid_for).isoformat(timespec="seconds"),
        "credentialSubject": subject,
    }
    if status:
        credential["credentialStatus"] = status
    return sign(credential, key, now=now)


def representative_token(inn: str, key: bytes) -> str:
    """Токен представителя: HMAC-SHA256 на ключе издателя. Без ключа ИНН по токену не подобрать."""
    return hmac.new(key, inn.encode("utf-8"), hashlib.sha256).hexdigest()


PSEUDONYM_KEY_MIN_BYTES = 32


def _read_pseudonym_key(path: Path) -> bytes:
    key = _private_bytes(path)
    if len(key) < PSEUDONYM_KEY_MIN_BYTES:
        raise ValueError(f"ключ HMAC короче {PSEUDONYM_KEY_MIN_BYTES} байт: токен представителя был бы слабым")
    return key


def load_or_create_pseudonym_key(path: Path) -> bytes:
    if path.exists():
        return _read_pseudonym_key(path)
    key = secrets.token_bytes(PSEUDONYM_KEY_MIN_BYTES)
    return key if _write_private(path, key) else _read_pseudonym_key(path)


def verify_signature(credential: dict[str, Any], public_key: Ed25519PublicKey) -> bool:
    """Только подпись: «подписано этим ключом». Не означает, что удостоверение можно принять."""
    return verify(credential, public_key)


def _when(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def verify_cryptographic(
    credential: dict[str, Any],
    trusted_keys: Iterable[Ed25519PublicKey] | Keyring,
    *,
    now: datetime,
    allow_demo: bool = False,
) -> list[str]:
    """Только криптография и поля удостоверения: без проверки статуса.

    Не означает допуск операции и не печатается как «принимается».
    """
    return validate_attestation(
        credential,
        trusted_keys,
        now=now,
        status_dir=None,
        allow_demo=allow_demo,
        require_status=False,
    )


def validate_attestation(
    credential: dict[str, Any],
    trusted_keys: Iterable[Ed25519PublicKey] | Keyring,
    *,
    now: datetime,
    status_dir: Path | None = None,
    allow_demo: bool = False,
    require_status: bool | None = None,
    seen: dict[str, int] | None = None,
    operation_risk: str | None = None,
    require_seen: bool = False,
) -> list[str]:
    """Подлинность удостоверения как квитанции результата.

    Пустой список — криптографически и по полям подлинно на момент ``now``.
    Это **не** разрешение банковской операции: для допуска см. ``authorize_operation``.

    ``require_status``: ``None`` берёт политику ``protected()`` (pilot/prod). В защищённом
    режиме каталог статусов обязателен: без него, при неизвестном/устаревшем статусе или
    при отсутствии ``credentialStatus`` удостоверение не считается подлинным для приёма.
    Диагностика подписи без статуса — только при явном ``require_status=False``.
    """
    from mandat.services.env_policy import require_attestation_status

    if require_status is None:
        require_status = require_attestation_status()
    problems: list[str] = []
    proof = credential.get("proof") or {}
    method = str(proof.get("verificationMethod") or "")
    keyring = trusted_keys if isinstance(trusted_keys, Keyring) else Keyring.of(trusted_keys)
    # Выведенный из выпуска ключ проверяет только подписи, сделанные до вывода (Keyring.trusted_for).
    keys = {public_key_multibase(k): k for k in keyring.trusted_for(_when(proof.get("created")))}
    key = keys.get(method.split("#", 1)[-1]) if method.startswith(f"{ISSUER}#") else None
    if credential.get("issuer") != ISSUER:
        problems.append(f"издатель {credential.get('issuer')!r} не {ISSUER}")
    if key is None:
        problems.append("ключ подписи не из списка доверенных ключей издателя")
    elif not verify(credential, key):
        problems.append("подпись не верна")
    if proof.get("cryptosuite") != CRYPTOSUITE or proof.get("type") != "DataIntegrityProof":
        problems.append(f"криптонабор {proof.get('cryptosuite')!r} не {CRYPTOSUITE}")
    if proof.get("proofPurpose") != PURPOSE:
        problems.append(f"назначение подписи {proof.get('proofPurpose')!r} не {PURPOSE}")
    if "MandatAuthorityAttestation" not in (credential.get("type") or []):
        problems.append("тип удостоверения не MandatAuthorityAttestation")
    start, end = _when(credential.get("validFrom")), _when(credential.get("validUntil"))
    if start is None or end is None:
        problems.append("срок действия не указан или без часового пояса")
    elif not start <= now < end:
        problems.append(f"срок действия {start:%d.%m.%Y %H:%M}–{end:%d.%m.%Y %H:%M} не включает момент проверки")
    if (credential.get("credentialSubject") or {}).get("profile") == "demo" and not allow_demo:
        problems.append("удостоверение выпущено по профилю demo: для решения в банке не принимается")
    needs_status = require_status or status_dir is not None
    if require_status and status_dir is None:
        problems.append("каталог статусов не передан: в защищённом режиме проверка статуса обязательна")
    elif require_status and not (credential.get("credentialStatus") or []):
        problems.append("в удостоверении нет credentialStatus: статус проверить нельзя")
    elif (
        needs_status
        and status_dir is not None
        and key is not None
        and not any(
            p.startswith("ключ подписи") or p.startswith("подпись не верна") or p.startswith("издатель")
            for p in problems
        )
    ):
        from mandat.audit.status_list import check_status

        try:
            state = check_status(
                credential,
                status_dir,
                keyring.for_status_lists() or [key],
                now=now,
                seen=seen,
                operation_risk=operation_risk,
                require_seen=require_seen,
            )["state"]
        except (OSError, ValueError, KeyError) as exc:
            problems.append(f"статус не проверен: {exc}")
        else:
            if state != "действует":
                problems.append(f"статус удостоверения: {state}")
    return problems


def authorize_operation(
    credential: dict[str, Any],
    trusted_keys: Iterable[Ed25519PublicKey] | Keyring,
    *,
    now: datetime,
    status_dir: Path,
    allow_demo: bool = False,
    seen: dict[str, int] | None = None,
    operation_risk: str | None = None,
) -> list[str]:
    """Допуск операции по удостоверению: подлинность + статус + вердикт confirmed.

    Успех ``validate_attestation`` при вердикте refuted/needs_review недостаточен для операции.
    В pilot/prod обязателен ``seen`` (high-watermark списков) и учитывается ``operation_risk`` (M-13).
    """
    from mandat.services.env_policy import protected

    problems: list[str] = []
    require_seen = bool(protected())
    if require_seen and seen is None:
        problems.append(
            "нет high-watermark seen: в защищённом режиме обязателен для допуска операции"
        )
    problems.extend(
        validate_attestation(
            credential,
            trusted_keys,
            now=now,
            status_dir=status_dir,
            allow_demo=allow_demo,
            require_status=True,
            seen=seen,
            operation_risk=operation_risk,
            # Если seen уже отсутствует — не дублируем ошибку check_status; age всё равно проверим
            # только когда watermark передан.
            require_seen=require_seen and seen is not None,
        )
    )
    verdict = (credential.get("credentialSubject") or {}).get("verdict")
    if verdict != "confirmed":
        problems.append(f"вердикт {verdict!r} не разрешает операцию: нужен confirmed")
    return problems


def verify(credential: dict[str, Any], public_key: Ed25519PublicKey) -> bool:
    proof = dict(credential.get("proof") or {})
    value = proof.pop("proofValue", "")
    if not isinstance(value, str) or not value.startswith("z"):
        return False
    document = {k: v for k, v in credential.items() if k != "proof"}
    try:
        public_key.verify(b58decode(value[1:]), _hash_data(document, proof))
    except (InvalidSignature, ValueError):
        return False
    return True


def public_key_from_multibase(value: str) -> Ed25519PublicKey:
    raw = b58decode(value[1:])
    if not raw.startswith(b"\xed\x01"):
        raise ValueError("ожидался ключ ed25519-pub в multibase")
    return Ed25519PublicKey.from_public_bytes(raw[2:])


def dump(credential: dict[str, Any]) -> str:
    return json.dumps(credential, ensure_ascii=False, indent=2)
