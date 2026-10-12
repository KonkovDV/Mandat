"""Подписанное удостоверение о результате проверки полномочия (в духе W3C VC 2.0).

Идея перенесена из fsp-talent (ADR-0007: удостоверения W3C VC с подписью Ed25519).
Подпись проверяется без доступа к базе «Мандата»: нужен только открытый ключ.

Схема подписи повторяет криптонабор ``eddsa-jcs-2022``: хеш канонической
конфигурации доказательства и хеш канонического документа, подпись Ed25519,
значение в base58btc с префиксом ``z``. Каноническая запись — сортировка ключей
без пробелов (совпадает с RFC 8785 для строк и целых чисел). Совместимость с
внешними верификаторами VC ещё не проверялась — это задача спринта 2.

В удостоверение не попадают ФИО и паспортные данные: только ИНН организации,
хеш ИНН представителя, операция, вердикт, номера МЧД цепочки и хеши входов.
Статус удостоверения (отозвано, приостановлено) — по спискам W3C Bitstring Status List
(``audit/status_list.py``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mandat.data import canonical_json

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ISSUER = "did:web:mandat.example"
CONTEXTS = ["https://www.w3.org/ns/credentials/v2"]


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


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("ключ подписи должен быть Ed25519")
        return key
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    path.chmod(0o600)
    return key


def public_key_multibase(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "z" + b58encode(b"\xed\x01" + raw)  # multicodec ed25519-pub


def _hash_data(document: dict[str, Any], proof_config: dict[str, Any]) -> bytes:
    return (
        hashlib.sha256(canonical_json(proof_config).encode("utf-8")).digest()
        + hashlib.sha256(canonical_json(document).encode("utf-8")).digest()
    )


def sign(document: dict[str, Any], key: Ed25519PrivateKey, *, now: datetime | None = None) -> dict[str, Any]:
    """Доказательство Data Integrity в духе ``eddsa-jcs-2022`` для любого JSON-документа."""
    created = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    proof_config = {
        "@context": CONTEXTS,
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-jcs-2022",
        "created": created,
        "verificationMethod": f"{ISSUER}#{public_key_multibase(key)}",
        "proofPurpose": "assertionMethod",
    }
    signature = key.sign(_hash_data(document, proof_config))
    return {**document, "proof": {**proof_config, "proofValue": "z" + b58encode(signature)}}


def issue(
    report: dict[str, Any],
    key: Ed25519PrivateKey,
    *,
    now: datetime | None = None,
    status: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    created = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    rep_inn = (report.get("requester") or {}).get("inn") or ""
    credential: dict[str, Any] = {
        "@context": CONTEXTS,
        "type": ["VerifiableCredential", "MandatAuthorityAttestation"],
        "issuer": ISSUER,
        "validFrom": created,
        "credentialSubject": {
            "organizationInn": (report.get("organization") or {}).get("inn"),
            "representativeInnSha256": hashlib.sha256(rep_inn.encode()).hexdigest() if rep_inn else None,
            "operation": (report.get("operation") or {}).get("code"),
            "operationDate": (report.get("operation") or {}).get("date"),
            "verdict": report.get("verdict"),
            "profile": report.get("profile"),
            "ruleSetSha256": (report.get("rule_pack") or {}).get("sha256"),
            "reportSha256": report.get("reproducibility_hash"),
            "authorityChain": list((report.get("chain") or {}).get("poa_numbers") or []),
            "inputs": [row.get("sha256") for row in report.get("inputs") or []],
        },
    }
    if status:
        credential["credentialStatus"] = status
    return sign(credential, key, now=now)


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
