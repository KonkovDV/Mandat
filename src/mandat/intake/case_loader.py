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


def _object(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{name} должен быть объектом JSON")
    return value


def _registry(value: Any, field_name: str = "registry") -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{field_name} должен быть объектом")
    return {str(k): str(v) for k, v in value.items()}


def _disposition(value: Any) -> str | None:
    if value is None:
        return None
    if value != "no_applicable_restriction":
        raise CaseError("charter_disposition: no_applicable_restriction или пусто")
    return value


def _rubles(value: Any) -> int | None:
    """Целые рубли. Дробь не усекается, boolean не считается деньгами."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaseError("amount_rub — целое число рублей; дробные значения и boolean не принимаются")
    return value


def _text(raw: dict[str, Any], key: str, where: str) -> str | None:
    """Реквизит строкой или пусто. Число вместо ИНН теряет ведущие нули, поэтому не принимается."""
    value = raw.get(key)
    if value is None or isinstance(value, str):
        return value
    raise CaseError(f"{where}.{key} должен быть строкой")


def _person(raw: dict[str, Any] | None) -> Person:
    raw = _object(raw, "person")
    return Person(**{k: _text(raw, k, "requester") for k in ("full_name", "inn", "snils", "position")})


def load_case_from_manifest(manifest: dict[str, Any], read: Callable[[dict[str, Any]], bytes]) -> Case:
    if not isinstance(manifest, dict):
        raise CaseError("манифест должен быть объектом JSON")
    try:
        op = _object(manifest.get("operation"), "operation")
        operation = Operation(
            code=str(op["code"]),
            on_date=date.fromisoformat(str(op["date"])),
            amount_rub=_rubles(op.get("amount_rub")),
            counterparty_inn=_text(op, "counterparty_inn", "operation"),
        )
        org = _object(manifest.get("organization"), "organization")
        organization = Organization(**{k: _text(org, k, "organization") for k in ("inn", "ogrn", "kpp", "name")})
    except (KeyError, ValueError, TypeError) as exc:
        raise CaseError(f"манифест кейса неполный: {exc}") from exc

    poas, papers, errors, inputs = [], [], [], []
    egrul = charter = None
    documents = manifest.get("documents") or []
    if not isinstance(documents, list):
        raise CaseError("documents должен быть списком")
    for doc in documents:
        if not isinstance(doc, dict):
            raise CaseError("документ в списке должен быть объектом")
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
        registry=_registry(manifest.get("registry")),
        bankruptcy=_registry(manifest.get("bankruptcy"), "bankruptcy"),
        intake_errors=tuple(errors),
        inputs=tuple(inputs),
        charter_disposition=_disposition(manifest.get("charter_disposition")),
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
