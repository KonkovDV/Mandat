"""Сведения ЕГРЮЛ в нормализованном JSON (внутренний формат «Мандата»).

Это не выписка ФНС в XML: в пилоте сюда подключается адаптер банка (СМЭВ или
внутренний сервис), который приводит ответ к этой форме. Формат описан в
docs/DOMAIN.md, пример — examples/cases/*/egrul.json.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

from mandat.domain.model import EgrulSnapshot, EvidenceRef, ExecutiveRecord, Organization, Person
from mandat.strictjson import strict_loads

STATUSES = {"active", "in_liquidation", "liquidated", "reorganizing", "terminated", "unknown"}


# Тот же предел, что у разбора XML (xml_safety.MAX_XML_BYTES): лимит действует в самом разборщике,
# а не только у загрузчика кейса, поэтому любой вызов parse_egrul (CLI, адаптеры) ограничен одинаково.
MAX_EGRUL_BYTES = 2 * 1024 * 1024


class EgrulError(ValueError):
    pass


def _d(value: Any) -> date | None:
    """Дата ``ГГГГ-ММ-ДД`` целиком. Хвост после даты и другие записи — ошибка, а не догадка."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise EgrulError(f"дата не распознана: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EgrulError(f"дата не распознана: {value!r}") from exc


def _text(row: dict[str, Any], key: str, where: str) -> str | None:
    value = row.get(key)
    if value is None or isinstance(value, str):
        return value
    raise EgrulError(f"{where}.{key} должен быть строкой")


def _flag(row: dict[str, Any], key: str, where: str) -> bool:
    value = row.get(key, False)
    if isinstance(value, bool):
        return value
    # Строка "false" через bool() дала бы True: признак принимается только как JSON-логическое значение.
    raise EgrulError(f"{where}.{key} должен быть true или false")


def parse_egrul(data: bytes | str, *, doc_id: str) -> EgrulSnapshot:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_EGRUL_BYTES:
        raise EgrulError(f"выписка ЕГРЮЛ больше {MAX_EGRUL_BYTES} байт")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = strict_loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EgrulError(f"JSON не разбирается: {exc}") from exc
    if not isinstance(payload, dict):
        raise EgrulError("ожидался JSON-объект")
    raw_status = payload.get("status")
    status = "unknown" if raw_status in (None, "") else str(raw_status)
    if status not in STATUSES:
        raise EgrulError(f"неизвестный статус {status!r}")
    rows = payload.get("executives") or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise EgrulError("executives должен быть списком объектов")
    for key in ("inn", "ogrn", "kpp", "name", "full_name"):
        _text(payload, key, "$")
    executives = []
    for index, row in enumerate(rows):
        where = f"$.executives[{index}]"
        person = Person(
            full_name=_text(row, "full_name", where),
            inn=_text(row, "inn", where),
            snils=_text(row, "snils", where),
            position=_text(row, "position", where),
        )
        executives.append(
            ExecutiveRecord(
                person=person,
                grn=row.get("grn"),
                record_date=_d(row.get("record_date")),
                valid_from=_d(row.get("valid_from")),
                valid_to=_d(row.get("valid_to")),
                unreliable=_flag(row, "unreliable", where),
                ref=EvidenceRef(doc_id, sha, f"$.executives[{index}]", quote=f"ГРН {row.get('grn')}"),
            )
        )
    org = Organization(
        inn=payload.get("inn"), ogrn=payload.get("ogrn"), kpp=payload.get("kpp"), name=payload.get("name")
    )
    return EgrulSnapshot(
        doc_id=doc_id,
        sha256=sha,
        organization=org,
        status=status,
        executives=tuple(executives),
        extracted_at=_d(payload.get("extracted_at")),
        full_name=payload.get("full_name"),
    )
