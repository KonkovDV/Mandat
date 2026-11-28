"""Кейсы консоли: демо-кейсы из генератора примеров и кейс из загруженных документов.

Демо-кейсы строятся тем же генератором, что и ``examples/cases`` (``mandat.examples.demo_cases``), поэтому стенду
не нужен каталог примеров на диске. Загрузка собирает манифест API из МЧД (XML), выписки ЕГРЮЛ (JSON) и устава
(текст): организация берётся из выписки, статус МЧД в реестре сотрудник выбирает сам.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from mandat.domain.verdict import VERDICT_LABEL_RU, Verdict
from mandat.examples import demo_cases
from mandat.intake.emchd import EmchdError
from mandat.intake.formats import parse_poa
from mandat.intake.xml_safety import XmlRejected
from mandat.rules.pack import default_catalog
from mandat.strictjson import strict_loads

# Кейс, на котором вкладка «ДБО» показывает допуск платежа: подтверждён, смена руководителя — только сигнал.
DBO_CASE = "14_old_director_charter_cleared"
MAX_POAS = 3
MAX_DOCUMENT_CHARS = 128 * 1024
REGISTRY_STATUSES = ("active", "revoked", "unknown")
_INN = re.compile(r"\d{10}(\d{2})?")
_SNILS = re.compile(r"\d{3}-\d{3}-\d{3} \d{2}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class UploadError(ValueError):
    """Загрузка не собирается в манифест: текст ошибки — для сотрудника."""


@lru_cache(maxsize=1)
def _demo() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for folder, manifest, files in demo_cases():
        documents = []
        for doc in manifest["documents"]:
            raw = files[doc["path"]].decode("utf-8")
            content: Any = json.loads(raw) if doc["kind"] == "egrul_json" else raw
            documents.append({"id": doc["id"], "kind": doc["kind"], "content": content})
        out[folder] = {**{k: v for k, v in manifest.items() if k != "documents"}, "documents": documents}
    return out


def catalog() -> list[dict[str, str]]:
    """Список демо-кейсов для выбора: имя, заголовок и ожидаемый вердикт."""
    return [
        {
            "name": name,
            "title": str(manifest.get("title") or name),
            "expected": str(manifest.get("expected_verdict") or ""),
            "expected_label": VERDICT_LABEL_RU[Verdict(manifest["expected_verdict"])],
        }
        for name, manifest in _demo().items()
    ]


def demo_manifest(name: str) -> dict[str, Any]:
    """Манифест демо-кейса для ``POST /v1/cases``; поля только для примеров (ожидание) убраны."""
    found = _demo().get(name)
    if found is None:
        raise KeyError(name)
    return {k: v for k, v in json.loads(json.dumps(found)).items() if k != "expected_verdict"}


def operations() -> list[dict[str, str]]:
    return [{"code": op.code, "name": op.name} for op in default_catalog().operations.values()]


def _text(body: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise UploadError(f"не заполнено поле {key}")
        return ""
    if not isinstance(value, str):
        raise UploadError(f"поле {key} должно быть строкой")
    if len(value) > MAX_DOCUMENT_CHARS:
        raise UploadError(f"поле {key} длиннее {MAX_DOCUMENT_CHARS // 1024} КБ")
    return value


def upload_manifest(body: dict[str, Any]) -> dict[str, Any]:
    """Манифест кейса из загруженных документов и полей формы."""
    poas = body.get("poas")
    if not isinstance(poas, list) or not 1 <= len(poas) <= MAX_POAS or not all(isinstance(p, str) for p in poas):
        raise UploadError(f"нужно от 1 до {MAX_POAS} файлов МЧД (XML)")
    if any(len(p) > MAX_DOCUMENT_CHARS for p in poas):
        raise UploadError(f"файл МЧД длиннее {MAX_DOCUMENT_CHARS // 1024} КБ")
    try:
        egrul = strict_loads(_text(body, "egrul"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UploadError("выписка ЕГРЮЛ не читается как JSON") from exc
    if not isinstance(egrul, dict):
        raise UploadError("выписка ЕГРЮЛ должна быть объектом JSON")
    charter = _text(body, "charter", required=False)
    operation = body.get("operation")
    requester = body.get("requester")
    if not isinstance(operation, dict) or not isinstance(requester, dict):
        raise UploadError("нужны операция и заявитель")
    code = _text(operation, "code")
    if code not in {op["code"] for op in operations()}:
        raise UploadError("операция не из каталога")
    day = _text(operation, "date")
    if not _DAY.fullmatch(day):
        raise UploadError("дата операции: ГГГГ-ММ-ДД")
    amount = operation.get("amount_rub")
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0):
        raise UploadError("сумма — целое число рублей больше нуля")
    inn = _text(requester, "inn")
    snils = _text(requester, "snils", required=False)
    if not _INN.fullmatch(inn):
        raise UploadError("ИНН заявителя: 10 или 12 цифр")
    if snils and not _SNILS.fullmatch(snils):
        raise UploadError("СНИЛС заявителя: 000-000-000 00")
    status = body.get("registry_status", "active")
    if status not in REGISTRY_STATUSES:
        raise UploadError("статус в реестре: active, revoked или unknown")
    documents: list[dict[str, Any]] = []
    registry: dict[str, str] = {}
    for n, raw in enumerate(poas, start=1):
        doc_id = f"poa-{n}"
        documents.append({"id": doc_id, "kind": "emchd_xml", "content": raw})
        try:
            number = parse_poa(raw.encode("utf-8"), doc_id=doc_id).number
        except (EmchdError, XmlRejected, ValueError):
            number = None  # разбор не удался: R-FMT-001 назовёт причину в отчёте
        if number and status != "unknown":
            registry[number] = status
    documents.append({"id": "egrul", "kind": "egrul_json", "content": egrul})
    if charter.strip():
        documents.append({"id": "charter", "kind": "charter_text", "content": charter})
    organization = {k: egrul[k] for k in ("inn", "ogrn", "kpp", "name") if isinstance(egrul.get(k), str)}
    manifest: dict[str, Any] = {
        "case_id": "upload",
        "title": "Документы, загруженные в консоли",
        "organization": organization,
        "requester": {
            "full_name": _text(requester, "full_name"),
            "inn": inn,
            **({"snils": snils} if snils else {}),
        },
        "operation": {"code": code, "date": day, **({"amount_rub": amount} if amount is not None else {})},
        "documents": documents,
        "registry": registry,
    }
    return manifest
