"""Сборка ``Case`` из манифеста: каталог с case.json (CLI) или JSON с содержимым (API).

Ошибки разбора не роняют проверку: они попадают в ``intake_errors``, а правило
R-FMT-001 превращает их в «недостаточно данных» (fail-closed).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from mandat.domain.model import Case, IntakeError, Operation, Organization, Person, TextDocument
from mandat.intake.egrul import EgrulError, parse_egrul
from mandat.intake.emchd import EmchdError
from mandat.intake.formats import parse_poa
from mandat.intake.xml_safety import XmlRejected

KINDS = {"emchd_xml", "egrul_json", "charter_text", "paper_poa_text"}
MAX_DOC_BYTES = 4 * 1024 * 1024


class CaseError(ValueError):
    pass


def _person(raw: dict[str, Any] | None) -> Person:
    raw = raw or {}
    return Person(
        full_name=raw.get("full_name"),
        inn=raw.get("inn"),
        snils=raw.get("snils"),
        position=raw.get("position"),
    )


def load_case_from_manifest(manifest: dict[str, Any], read: Callable[[dict[str, Any]], bytes]) -> Case:
    try:
        op = manifest["operation"]
        operation = Operation(
            code=str(op["code"]),
            on_date=date.fromisoformat(str(op["date"])),
            amount_rub=int(op["amount_rub"]) if op.get("amount_rub") is not None else None,
            counterparty_inn=op.get("counterparty_inn"),
        )
        org = manifest.get("organization") or {}
        organization = Organization(inn=org.get("inn"), ogrn=org.get("ogrn"), kpp=org.get("kpp"), name=org.get("name"))
    except (KeyError, ValueError, TypeError) as exc:
        raise CaseError(f"манифест кейса неполный: {exc}") from exc

    poas, papers, errors, inputs = [], [], [], []
    egrul = charter = None
    for doc in manifest.get("documents") or []:
        doc_id, kind = str(doc.get("id")), str(doc.get("kind"))
        if kind not in KINDS:
            errors.append(IntakeError(doc_id, kind, f"неизвестный вид документа {kind!r}"))
            continue
        try:
            raw = read(doc)
        except (OSError, ValueError) as exc:
            errors.append(IntakeError(doc_id, kind, f"документ не читается: {exc}"))
            continue
        if len(raw) > MAX_DOC_BYTES:
            errors.append(IntakeError(doc_id, kind, "документ больше допустимого размера"))
            continue
        sha = hashlib.sha256(raw).hexdigest()
        inputs.append({"doc_id": doc_id, "kind": kind, "sha256": sha})
        try:
            if kind == "emchd_xml":
                poas.append(parse_poa(raw, doc_id=doc_id))
            elif kind == "egrul_json":
                egrul = parse_egrul(raw, doc_id=doc_id)
            elif kind == "charter_text":
                charter = TextDocument(doc_id, sha, kind, raw.decode("utf-8"))
            else:
                papers.append(TextDocument(doc_id, sha, kind, raw.decode("utf-8")))
        except (EmchdError, EgrulError, XmlRejected, UnicodeDecodeError) as exc:
            errors.append(IntakeError(doc_id, kind, str(exc)))

    return Case(
        case_id=str(manifest.get("case_id") or "case"),
        organization=organization,
        requester=_person(manifest.get("requester")),
        operation=operation,
        poas=tuple(poas),
        egrul=egrul,
        charter=charter,
        paper_poas=tuple(papers),
        registry={str(k): str(v) for k, v in (manifest.get("registry") or {}).items()},
        intake_errors=tuple(errors),
        inputs=tuple(inputs),
    )


def load_case_dir(path: Path) -> Case:
    manifest_path = path / "case.json"
    if not manifest_path.is_file():
        raise CaseError(f"нет {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = path.resolve()

    def read(doc: dict[str, Any]) -> bytes:
        target = (base / str(doc["path"])).resolve()
        if base not in target.parents:
            raise ValueError("путь документа выходит за каталог кейса")
        return target.read_bytes()

    return load_case_from_manifest(manifest, read)


def load_case_inline(manifest: dict[str, Any]) -> Case:
    """Для API: содержимое документов передаётся строкой (XML, JSON или текст)."""

    def read(doc: dict[str, Any]) -> bytes:
        content = doc.get("content")
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False).encode("utf-8")
        if not isinstance(content, str):
            raise ValueError("content должен быть строкой или JSON-объектом")
        return content.encode("utf-8")

    return load_case_from_manifest(manifest, read)
