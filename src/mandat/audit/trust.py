"""TrustedRoot и SigningConfig Sigstore: какие журналы Rekor и TSA доверенные и куда писать.

Sigstore раздаёт оба файла через TUF (репозиторий ``https://tuf-repo-cdn.sigstore.dev``, цели
``trusted_root.json`` и ``signing_config.v0.2.json``). Якорь доверия — корень TUF, вшитый в пакет
(``data/sigstore_tuf_root.json``, тот же, что у sigstore-python); клиент TUF (extra ``[tuf]``, python-tuf)
проходит цепочку корней, проверяет timestamp/snapshot/targets и хеши целей.

Офлайн: если TUF недоступен (нет сети или extra), берутся последние проверенные копии из кеша
(``MANDAT_SIGSTORE_TUF_CACHE``), а если их нет — файлы ``MANDAT_SIGSTORE_TRUSTED_ROOT`` и
``MANDAT_SIGSTORE_SIGNING_CONFIG``, положенные администратором. ``MANDAT_SIGSTORE_OFFLINE=1`` — сеть не трогать.

Выбор журнала — по SigningConfig v0.2: действующий по ``validFor``, с наибольшей поддерживаемой
``majorApiVersion``, первый в списке. Ключ журнала — из TrustedRoot по ``baseUrl``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

log = logging.getLogger("mandat.anchor.trust")
TUF_URL = "https://tuf-repo-cdn.sigstore.dev"
TRUSTED_ROOT = "trusted_root.json"
SIGNING_CONFIG = "signing_config.v0.2.json"
SUPPORTED_REKOR = (1, 2)


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _valid(window: dict[str, Any] | None, now: datetime) -> bool:
    window = window or {}
    start, end = _time(window.get("start")), _time(window.get("end"))
    return (start is None or start <= now) and (end is None or now < end)


def _pem(der: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_der_public_key(der)
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


@dataclass(frozen=True)
class LogInstance:
    base_url: str
    key_pem: bytes
    key_details: str
    valid_for: dict[str, Any]


@dataclass(frozen=True)
class TrustedRoot:
    tlogs: tuple[LogInstance, ...]
    tsa_roots_pem: bytes

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TrustedRoot:
        if not str(data.get("mediaType", "")).startswith("application/vnd.dev.sigstore.trustedroot"):
            raise ValueError("не TrustedRoot Sigstore")
        tlogs = tuple(
            LogInstance(
                item["baseUrl"].rstrip("/"),
                _pem(base64.b64decode(item["publicKey"]["rawBytes"])),
                item["publicKey"].get("keyDetails", ""),
                item["publicKey"].get("validFor") or {},
            )
            for item in data.get("tlogs", [])
        )
        roots = []
        for tsa in data.get("timestampAuthorities", []):
            chain = tsa.get("certChain", {}).get("certificates") or []
            if chain:
                der = base64.b64decode(chain[-1]["rawBytes"])
                body = base64.encodebytes(der).decode().replace("\n", "")
                lines = [body[i : i + 64] for i in range(0, len(body), 64)]
                roots.append("-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n")
        return cls(tlogs, "".join(roots).encode())

    def log(self, url: str, now: datetime | None = None) -> LogInstance:
        moment = now or datetime.now(UTC)
        for item in self.tlogs:
            if item.base_url == url.rstrip("/") and _valid(item.valid_for, moment):
                return item
        raise ValueError(f"журнала {url} нет в TrustedRoot или его ключ недействителен")


@dataclass(frozen=True)
class Service:
    url: str
    major: int
    valid_for: dict[str, Any]
    operator: str


@dataclass(frozen=True)
class SigningConfig:
    rekor: tuple[Service, ...]
    tsa: tuple[Service, ...]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SigningConfig:
        if data.get("mediaType") != "application/vnd.dev.sigstore.signingconfig.v0.2+json":
            raise ValueError("SigningConfig не версии v0.2")

        def services(key: str) -> tuple[Service, ...]:
            return tuple(
                Service(
                    s["url"].rstrip("/"),
                    int(s.get("majorApiVersion", 1)),
                    s.get("validFor") or {},
                    s.get("operator", ""),
                )
                for s in data.get(key, [])
            )

        return cls(services("rekorTlogUrls"), services("tsaUrls"))

    def select_rekor(self, now: datetime | None = None) -> Service:
        moment = now or datetime.now(UTC)
        valid = [s for s in self.rekor if s.major in SUPPORTED_REKOR and _valid(s.valid_for, moment)]
        if not valid:
            raise ValueError("в SigningConfig нет действующего журнала Rekor поддерживаемой версии")
        best = max(s.major for s in valid)
        return next(s for s in valid if s.major == best)


@dataclass(frozen=True)
class Trust:
    root: TrustedRoot
    signing: SigningConfig | None
    source: str


def bundled_tuf_root() -> bytes:
    return resources.files("mandat").joinpath("data/sigstore_tuf_root.json").read_bytes()


def _tuf_refresh(cache: Path, url: str, bootstrap: bytes) -> None:
    from tuf.ngclient import Updater  # type: ignore[import-not-found,attr-defined,unused-ignore]  # extra [tuf]

    metadata, targets = cache / "metadata", cache / "targets"
    metadata.mkdir(parents=True, exist_ok=True)
    targets.mkdir(parents=True, exist_ok=True)
    if not (metadata / "root.json").exists():
        (metadata / "root.json").write_bytes(bootstrap)
    updater = Updater(str(metadata), url, str(targets), f"{url}/targets", bootstrap=bootstrap)
    updater.refresh()
    for name in (TRUSTED_ROOT, SIGNING_CONFIG):
        info = updater.get_targetinfo(name)
        if info is None:
            raise ValueError(f"в репозитории TUF нет цели {name}")
        updater.download_target(info, str(targets / name))


def load(environ: dict[str, str] | None = None, refresh: Any = None) -> Trust:
    """TrustedRoot и SigningConfig: TUF → кеш последних проверенных → файлы администратора."""
    env = os.environ if environ is None else environ
    cache = Path(env.get("MANDAT_SIGSTORE_TUF_CACHE", "var/sigstore-tuf"))
    url = env.get("MANDAT_SIGSTORE_TUF_URL", TUF_URL).rstrip("/")
    offline = env.get("MANDAT_SIGSTORE_OFFLINE", "") in {"1", "true", "yes"}
    bootstrap_path = env.get("MANDAT_SIGSTORE_TUF_ROOT", "")
    bootstrap = Path(bootstrap_path).read_bytes() if bootstrap_path else bundled_tuf_root()
    source = "tuf"
    if offline:
        source = "кеш (офлайн по настройке)"
    else:
        try:
            (refresh or _tuf_refresh)(cache, url, bootstrap)
        except Exception as exc:  # noqa: BLE001 - TUF недоступен: офлайн-копии ниже
            log.warning("TUF Sigstore недоступен (%s): беру последние проверенные копии", type(exc).__name__)
            source = "кеш (TUF недоступен)"
    root_file = cache / "targets" / TRUSTED_ROOT
    config_file = cache / "targets" / SIGNING_CONFIG
    if not root_file.exists():
        explicit = env.get("MANDAT_SIGSTORE_TRUSTED_ROOT", "")
        if not explicit:
            raise ValueError(
                "нет TrustedRoot Sigstore: TUF недоступен, кеша нет, MANDAT_SIGSTORE_TRUSTED_ROOT не задан"
            )
        root_file, source = Path(explicit), "файл администратора"
        config_file = Path(env.get("MANDAT_SIGSTORE_SIGNING_CONFIG", "") or "/nonexistent")
    root = TrustedRoot.from_json(json.loads(root_file.read_text(encoding="utf-8")))
    signing = (
        SigningConfig.from_json(json.loads(config_file.read_text(encoding="utf-8"))) if config_file.exists() else None
    )
    return Trust(root, signing, source)


def rekor_target(trust: Trust, override: str | None = None, now: datetime | None = None) -> tuple[str, int, bytes]:
    """(адрес, версия API, PEM ключа журнала). ``override`` — адрес вручную (например, Rekor v2 до его раздачи)."""
    if override:
        instance = trust.root.log(override, now)
        major = 2 if "ED25519" in instance.key_details.upper() else 1
        if trust.signing:
            major = next((s.major for s in trust.signing.rekor if s.url == instance.base_url), major)
        return instance.base_url, major, instance.key_pem
    if trust.signing is None:
        raise ValueError("нет SigningConfig: задайте MANDAT_SIGSTORE_REKOR_URL")
    service = trust.signing.select_rekor(now)
    return service.url, service.major, trust.root.log(service.url, now).key_pem
