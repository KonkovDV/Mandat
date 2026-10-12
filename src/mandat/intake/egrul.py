"""Сведения ЕГРЮЛ в нормализованном JSON (внутренний формат «Мандата»).

Это не выписка ФНС в XML: в пилоте сюда подключается адаптер банка (СМЭВ или
внутренний сервис), который приводит ответ к этой форме. Формат описан в
docs/DOMAIN.md, пример — examples/cases/*/egrul.json.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from mandat.domain.model import EgrulSnapshot, EvidenceRef, ExecutiveRecord, Organization, Person

STATUSES = {"active", "in_liquidation", "liquidated"}


class EgrulError(ValueError):
    pass


def _d(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise EgrulError(f"дата не распознана: {value!r}") from exc


def parse_egrul(data: bytes | str, *, doc_id: str) -> EgrulSnapshot:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EgrulError(f"JSON не разбирается: {exc}") from exc
    if not isinstance(payload, dict):
        raise EgrulError("ожидался JSON-объект")
    status = str(payload.get("status", "active"))
    if status not in STATUSES:
        raise EgrulError(f"неизвестный статус {status!r}")
    executives = []
    for index, row in enumerate(payload.get("executives") or []):
        person = Person(
            full_name=row.get("full_name"),
            inn=row.get("inn"),
            snils=row.get("snils"),
            position=row.get("position"),
        )
        executives.append(
            ExecutiveRecord(
                person=person,
                grn=row.get("grn"),
                record_date=_d(row.get("record_date")),
                valid_from=_d(row.get("valid_from")),
                valid_to=_d(row.get("valid_to")),
                unreliable=bool(row.get("unreliable", False)),
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
