"""Ключ подписи удостоверений за абстракцией ``Signer``: файл PEM, HSM по PKCS#11, Vault Transit, AWS KMS.

Подписант отдаёт три вещи: идентификатор ключа (``key_id``), открытый ключ и подпись Ed25519 байтов.
Закрытый ключ наружу не выходит, если он в HSM или KMS: Мандат видит только подпись.

Идентификатор ключа — multibase открытого ключа (``z6Mk…``, как в did:key). Он входит в подписанную
часть доказательства (``proof.verificationMethod = ISSUER#key_id``), поэтому подменить его нельзя,
а проверяющий выбирает ключ из связки (``Keyring``) по нему, как ``kid`` в JWKS.

Выбор подписанта: ``MANDAT_SIGNER`` = ``file`` (по умолчанию), ``pkcs11``, ``vault`` или ``aws-kms``.
В pilot и prod файл PEM без шифрования не читается и не создаётся (RT-05): нужна парольная фраза
``MANDAT_SIGNING_KEY_PASSPHRASE`` или внешний модуль. Внешние модули — extras с ленивым импортом:
``[pkcs11]`` (python-pkcs11), ``[aws]`` (boto3); Vault — только стандартная библиотека.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PASSPHRASE_ENV = "MANDAT_SIGNING_KEY_PASSPHRASE"  # noqa: S105 - имя переменной, не значение
MIN_PASSPHRASE_BYTES = 16
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class SignerError(RuntimeError):
    """Подписант не настроен, недоступен или вернул подпись не тем ключом."""


def _b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58[rem] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def key_id(public: Ed25519PublicKey) -> str:
    """Multibase открытого ключа с префиксом multicodec ed25519-pub."""
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "z" + _b58encode(b"\xed\x01" + raw)


@runtime_checkable
class Signer(Protocol):
    @property
    def key_id(self) -> str: ...

    def public_key(self) -> Ed25519PublicKey: ...

    def sign(self, data: bytes) -> bytes: ...


class LocalSigner:
    """Ключ в памяти процесса. Для тестов и для ключа, прочитанного из файла."""

    def __init__(self, private: Ed25519PrivateKey):
        self._private = private
        self._public = private.public_key()
        self._kid = key_id(self._public)

    @property
    def key_id(self) -> str:
        return self._kid

    def public_key(self) -> Ed25519PublicKey:
        return self._public

    def sign(self, data: bytes) -> bytes:
        return self._private.sign(data)


def as_signer(key: Ed25519PrivateKey | Signer) -> Signer:
    return LocalSigner(key) if isinstance(key, Ed25519PrivateKey) else key


def checked_sign(signer: Signer, data: bytes) -> bytes:
    """Подпись с проверкой открытым ключом подписанта.

    HSM или KMS могли подписать другой версией ключа (ротация на стороне модуля): тогда ``key_id``
    в доказательстве не совпал бы с ключом подписи. Такая подпись не выпускается.
    """
    signature = signer.sign(data)
    try:
        signer.public_key().verify(signature, data)
    except InvalidSignature as exc:
        raise SignerError(f"подпись не проверяется ключом {signer.key_id}: модуль подписал другим ключом") from exc
    return signature


# --- файл PEM ---------------------------------------------------------------------------------


def _protected() -> bool:
    from mandat.services.env_policy import protected

    return protected()


def _passphrase(value: str | None) -> bytes | None:
    text = os.environ.get(PASSPHRASE_ENV, "") if value is None else value
    text = text.strip()
    if not text:
        return None
    if len(text.encode("utf-8")) < MIN_PASSPHRASE_BYTES:
        raise SignerError(f"{PASSPHRASE_ENV} короче {MIN_PASSPHRASE_BYTES} байт")
    return text.encode("utf-8")


def load_pem_signer(path: Path, *, passphrase: str | None = None, create: bool = True) -> LocalSigner:
    """Ключ из PEM PKCS#8. С парольной фразой файл шифруется (PBES2, AES-256); без неё — только demo/dev/test."""
    from mandat.audit.attestation import _private_bytes, _write_private

    secret = _passphrase(passphrase)
    if secret is None and _protected():
        raise SignerError(
            f"ключ подписи в открытом PEM в защищённом режиме запрещён: задайте {PASSPHRASE_ENV} "
            "или MANDAT_SIGNER=pkcs11/vault/aws-kms (docs/KEYS.md)"
        )
    if not path.exists():
        if not create:
            raise SignerError(f"нет ключа подписи {path}")
        key = Ed25519PrivateKey.generate()
        encryption = serialization.BestAvailableEncryption(secret) if secret else serialization.NoEncryption()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
        if _write_private(path, pem):
            return LocalSigner(key)
    data = _private_bytes(path)
    encrypted = b"ENCRYPTED PRIVATE KEY" in data
    if secret and not encrypted:
        # Фраза задана, а файл открытый: молча читать его значит считать защищённым то, что не защищено.
        raise SignerError(f"{path.name} не зашифрован, хотя задан {PASSPHRASE_ENV}: перешифруйте ключ")
    if encrypted and not secret:
        raise SignerError(f"{path.name} зашифрован: задайте {PASSPHRASE_ENV}")
    try:
        loaded = serialization.load_pem_private_key(data, password=secret)
    except (ValueError, TypeError) as exc:
        raise SignerError(f"ключ {path.name} не читается: неверная парольная фраза или повреждён файл") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise SignerError("ключ подписи должен быть Ed25519")
    return LocalSigner(loaded)


# --- PKCS#11 (HSM, SoftHSM, токены) -----------------------------------------------------------


def _ec_point_to_raw(point: bytes) -> bytes:
    """CKA_EC_POINT для Ed25519: 32 байта или DER OCTET STRING (04 20 …) вокруг них."""
    if len(point) == 32:
        return point
    if len(point) == 34 and point[0] == 0x04 and point[1] == 0x20:
        return point[2:]
    raise SignerError("CKA_EC_POINT не похож на открытый ключ Ed25519")


class Pkcs11Signer:
    """Подпись CKM_EDDSA в HSM через python-pkcs11. Закрытый ключ из модуля не извлекается."""

    def __init__(self, library: str, token_label: str, key_label: str, pin: str, *, lib_loader: Any = None):
        if lib_loader is None:
            try:
                import pkcs11
            except ModuleNotFoundError as exc:
                raise SignerError("для MANDAT_SIGNER=pkcs11 установите extra: pip install 'mandat[pkcs11]'") from exc
            lib_loader = pkcs11
        self._p11 = lib_loader
        token = lib_loader.lib(library).get_token(token_label=token_label)
        self._session = token.open(user_pin=pin)
        cls = lib_loader.ObjectClass
        self._private = self._session.get_key(object_class=cls.PRIVATE_KEY, label=key_label)
        public = self._session.get_key(object_class=cls.PUBLIC_KEY, label=key_label)
        raw = _ec_point_to_raw(bytes(public[lib_loader.Attribute.EC_POINT]))
        self._public = Ed25519PublicKey.from_public_bytes(raw)
        self._kid = key_id(self._public)

    @property
    def key_id(self) -> str:
        return self._kid

    def public_key(self) -> Ed25519PublicKey:
        return self._public

    def sign(self, data: bytes) -> bytes:
        return bytes(self._private.sign(data, mechanism=self._p11.Mechanism.EDDSA))

    def close(self) -> None:
        self._session.close()


# --- HashiCorp Vault Transit ------------------------------------------------------------------

Opener = Callable[[urllib.request.Request], Any]


def _urlopen(request: urllib.request.Request) -> Any:
    return urllib.request.urlopen(request, timeout=10)  # noqa: S310 - адрес Vault задаёт администратор, схема проверена


class VaultTransitSigner:
    """Ключ ``ed25519`` в Vault Transit. Версия ключа закрепляется при старте: подпись и ``key_id`` согласованы."""

    def __init__(
        self,
        addr: str,
        key_name: str,
        token: str,
        *,
        mount: str = "transit",
        key_version: int | None = None,
        opener: Opener | None = None,
    ):
        if not addr.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise SignerError("VAULT_ADDR: только https (http допускается лишь на localhost)")
        if not token:
            raise SignerError("VAULT_TOKEN не задан")
        self._base = f"{addr.rstrip('/')}/v1/{mount.strip('/')}"
        self._name = key_name
        self._token = token
        self._open = opener or (lambda request: _urlopen(request))
        info = self._call("GET", f"keys/{key_name}")["data"]
        if info.get("type") != "ed25519":
            raise SignerError(f"ключ Vault {key_name} типа {info.get('type')!r}, нужен ed25519")
        self._version = int(key_version or info["latest_version"])
        entry = (info.get("keys") or {}).get(str(self._version))
        if not isinstance(entry, dict) or not entry.get("public_key"):
            raise SignerError(f"у ключа Vault {key_name} нет открытого ключа версии {self._version}")
        self._public = Ed25519PublicKey.from_public_bytes(base64.b64decode(entry["public_key"]))
        self._kid = key_id(self._public)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310
            f"{self._base}/{path}",
            data=data,
            method=method,
            headers={"X-Vault-Token": self._token, "Content-Type": "application/json"},
        )
        with self._open(request) as response:
            return dict(json.loads(response.read()))

    @property
    def key_id(self) -> str:
        return self._kid

    @property
    def version(self) -> int:
        return self._version

    def public_key(self) -> Ed25519PublicKey:
        return self._public

    def sign(self, data: bytes) -> bytes:
        body = {"input": base64.b64encode(data).decode(), "key_version": self._version}
        signature = str(self._call("POST", f"sign/{self._name}", body)["data"]["signature"])
        prefix = f"vault:v{self._version}:"
        if not signature.startswith(prefix):
            raise SignerError(f"Vault подписал не версией {self._version}: {signature.split(':')[:2]}")
        return base64.b64decode(signature[len(prefix) :])


# --- AWS KMS ----------------------------------------------------------------------------------


class AwsKmsSigner:
    """Ключ ``ECC_NIST_EDWARDS25519`` в AWS KMS, алгоритм ``ED25519_SHA_512`` с ``MessageType=RAW``."""

    def __init__(self, key_ref: str, *, client: Any = None):
        if client is None:
            try:
                import boto3
            except ModuleNotFoundError as exc:
                raise SignerError("для MANDAT_SIGNER=aws-kms установите extra: pip install 'mandat[aws]'") from exc
            client = boto3.client("kms")
        self._client = client
        self._key = key_ref
        meta = client.get_public_key(KeyId=key_ref)
        if meta.get("KeySpec") != "ECC_NIST_EDWARDS25519":
            raise SignerError(f"ключ KMS {key_ref} типа {meta.get('KeySpec')!r}, нужен ECC_NIST_EDWARDS25519")
        public = serialization.load_der_public_key(meta["PublicKey"])
        if not isinstance(public, Ed25519PublicKey):
            raise SignerError("открытый ключ KMS не Ed25519")
        self._public = public
        self._kid = key_id(public)

    @property
    def key_id(self) -> str:
        return self._kid

    def public_key(self) -> Ed25519PublicKey:
        return self._public

    def sign(self, data: bytes) -> bytes:
        out = self._client.sign(KeyId=self._key, Message=data, MessageType="RAW", SigningAlgorithm="ED25519_SHA_512")
        return bytes(out["Signature"])


# --- выбор по окружению -----------------------------------------------------------------------


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SignerError(f"задайте {name}")
    return value


def signer_from_env(default_path: Path, *, create: bool = True) -> Signer:
    kind = os.environ.get("MANDAT_SIGNER", "file").strip().lower() or "file"
    if kind == "file":
        path = Path(os.environ.get("MANDAT_SIGNING_KEY_FILE", "").strip() or default_path)
        return load_pem_signer(path, create=create)
    if kind == "pkcs11":
        return Pkcs11Signer(
            _require("MANDAT_PKCS11_LIB"),
            _require("MANDAT_PKCS11_TOKEN"),
            _require("MANDAT_PKCS11_KEY_LABEL"),
            _require("MANDAT_PKCS11_PIN"),
        )
    if kind == "vault":
        version = os.environ.get("MANDAT_VAULT_KEY_VERSION", "").strip()
        return VaultTransitSigner(
            _require("VAULT_ADDR"),
            _require("MANDAT_VAULT_KEY"),
            _require("VAULT_TOKEN"),
            mount=os.environ.get("MANDAT_VAULT_MOUNT", "transit").strip() or "transit",
            key_version=int(version) if version else None,
        )
    if kind == "aws-kms":
        return AwsKmsSigner(_require("MANDAT_KMS_KEY_ID"))
    raise SignerError(f"MANDAT_SIGNER={kind!r}: допустимы file, pkcs11, vault, aws-kms")


# --- связка доверенных ключей -----------------------------------------------------------------

STATUSES = frozenset({"active", "retired", "revoked"})


@dataclass(frozen=True)
class KeyEntry:
    kid: str
    public: Ed25519PublicKey
    status: str
    retired_at: datetime | None = None


@dataclass
class Keyring:
    """Доверенные ключи издателя, как JWKS: ``{"keys": [{"kid": "z6Mk…", "status": "active"}, …]}``.

    ``active`` — текущий ключ; ``retired`` — выведен из выпуска, но проверяет удостоверения,
    подписанные не позже ``retired_at``; ``revoked`` — скомпрометирован, не проверяет ничего.
    """

    entries: list[KeyEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Keyring:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.parse(payload)

    @classmethod
    def parse(cls, payload: Any) -> Keyring:
        from mandat.audit.attestation import public_key_from_multibase

        rows = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError("связка ключей: ожидался непустой список keys")
        entries, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("kid"), str):
                raise ValueError("связка ключей: у каждого ключа нужен kid")
            status = row.get("status", "active")
            if status not in STATUSES:
                raise ValueError(f"связка ключей: статус {status!r} не из {sorted(STATUSES)}")
            kid = row["kid"]
            if kid in seen:
                raise ValueError(f"связка ключей: {kid} указан дважды")
            seen.add(kid)
            retired_at = None
            if status == "retired":
                retired_at = datetime.fromisoformat(str(row.get("retired_at") or ""))
                if retired_at.tzinfo is None:
                    raise ValueError(f"связка ключей: retired_at у {kid} без часового пояса")
            public = public_key_from_multibase(kid)
            entries.append(KeyEntry(kid, public, status, retired_at))
        return cls(entries)

    def trusted_for(self, created: datetime | None) -> list[Ed25519PublicKey]:
        """Ключи, которым можно доверять для подписи, сделанной в момент ``created``."""
        out = []
        for entry in self.entries:
            if entry.status == "active":
                out.append(entry.public)
            elif entry.status == "retired" and created is not None and entry.retired_at is not None:
                if created <= entry.retired_at:
                    out.append(entry.public)
        return out

    def for_status_lists(self) -> list[Ed25519PublicKey]:
        """Список статусов переподписывается текущим ключом, поэтому для него годятся только active."""
        return [e.public for e in self.entries if e.status == "active"]

    @staticmethod
    def of(keys: Iterable[Ed25519PublicKey]) -> Keyring:
        return Keyring([KeyEntry(key_id(k), k, "active") for k in keys])
