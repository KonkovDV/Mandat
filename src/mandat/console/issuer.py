"""Демо-издатель удостоверений стенда и его списки статусов.

На стенде банк выпускает удостоверение на один платёж сам: ключ Ed25519 и ключ HMAC представителя создаются
при старте процесса и живут только в его памяти, списки статусов лежат во временном каталоге. Допуск операции
(``POST /v1/operations/authorize`` и AuthZEN) проверяет удостоверение той же связкой ключей и тем же каталогом.
После перезапуска выпущенные раньше удостоверения не проходят проверку подписи: это стенд, а не реестр банка.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.attestation import BANK_TZ
from mandat.audit.signing import Keyring
from mandat.audit.status_list import StatusRegistry
from mandat.services.admission import AdmissionConfig

# Потолок удостоверений за жизнь процесса: реестр статусов на диске не растёт без конца.
MAX_ISSUED = 3000
# Сколько снимков списков статусов хранить: текущий и два прежних для читателя, который открыл их чуть раньше.
KEEP_SNAPSHOTS = 3


class IssuerExhausted(RuntimeError):
    """Потолок удостоверений стенда исчерпан до перезапуска."""


def bank_today() -> str:
    """День по Москве: допуск сверяет день операции с часами сервера."""
    return datetime.now(UTC).astimezone(BANK_TZ).date().isoformat()


class DemoIssuer:
    def __init__(self, workdir: Path | None = None) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.pseudonym_key = secrets.token_bytes(32)
        if workdir is not None:
            workdir.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="mandat-stand-", dir=workdir))
        self.status_dir = self.root / "status"
        self.registry = StatusRegistry.open(self.status_dir)
        self.registry.save(self.key)
        self.issued = 0
        self._lock = threading.Lock()

    def config(self) -> AdmissionConfig:
        return AdmissionConfig(Keyring.of([self.key.public_key()]), self.status_dir, self.pseudonym_key)

    def issue(self, report: dict[str, Any], *, audience: str, nonce: str) -> tuple[dict[str, Any], int]:
        """Удостоверение на операцию отчёта, привязанное к получателю и одноразовому ``nonce``."""
        with self._lock:
            if self.issued >= MAX_ISSUED:
                raise IssuerExhausted(f"стенд выпустил {MAX_ISSUED} удостоверений: перезапуск обнуляет счёт")
            index = self.registry.allocate(
                {
                    "case_id": report["case_id"],
                    "org_inn": report["organization"]["inn"],
                    "verdict": report["verdict"],
                    "poa_numbers": report["chain"]["poa_numbers"],
                    "chain_kind": report["chain"]["kind"],
                }
            )
            credential = attestation.issue(
                report,
                self.key,
                status=self.registry.credential_status(index),
                pseudonym_key=self.pseudonym_key,
                audience=audience,
                nonce=nonce,
            )
            self.registry.save(self.key)
            self._prune()
            self.issued += 1
            return credential, index

    def suspend(self, index: int, reason: str) -> bool:
        """Приостановить удостоверение, как это делает мониторинг по признакам захвата."""
        with self._lock:
            changed = self.registry.mark(index, "suspension", reason)
            if changed:
                self.registry.save(self.key)
                self._prune()
            return changed

    def _prune(self) -> None:
        snapshots = self.status_dir / "snapshots"
        if not snapshots.is_dir():
            return
        numbered = sorted((int(p.name), p) for p in snapshots.iterdir() if p.name.isdigit())
        for _, stale in numbered[:-KEEP_SNAPSHOTS]:
            shutil.rmtree(stale, ignore_errors=True)


def rebased_today(manifest: dict[str, Any]) -> dict[str, Any]:
    """Демо-кейс на сегодня: день операции — сегодня по Москве, выписка ЕГРЮЛ — вчерашняя.

    Допуск сверяет день операции с часами сервера, а выписка старше 30 дней уходит на разбор. Остальные даты
    кейса (выдача и срок МЧД, записи ЕГРЮЛ) не меняются.
    """
    today = bank_today()
    yesterday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
    documents = []
    for doc in manifest.get("documents") or []:
        content = doc.get("content")
        if doc.get("kind") == "egrul_json" and isinstance(content, dict):
            doc = {**doc, "content": {**content, "extracted_at": yesterday}}
        documents.append(doc)
    return {**manifest, "operation": {**manifest["operation"], "date": today}, "documents": documents}
