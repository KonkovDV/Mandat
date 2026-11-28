"""Реестр заключений юриста в HTTP API: вид записи для ответа и чтение записей хранилища для проверки кейса.

Маршруты — в ``api/app.py`` (``/v1/conclusions``), жизнь записи — ``review/conclusions.py``, применение в правилах —
``rules/conclusions.py``. Здесь — то, что нужно обоим концам: вид записи с действующим статусом и числом применений,
разбор записей для правил и пределы реестра.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from mandat.domain.conclusion import OUTCOME_RU, STATUS_RU, Conclusion, content_sha256, msk_day
from mandat.review.conclusions import effective_status

# Записей на одну организацию: история редакций устава и отзывов, но не бесконечный поток (OWASP API4:2023).
MAX_PER_OGRN = 200
Row = tuple[str, dict[str, Any], int]
FIELDS = (
    "ogrn",
    "charter_sha256",
    "outcome",
    "restrictions",
    "basis",
    "operations",
    "max_amount_rub",
    "valid_until",
    "origin",
    "confirmed",
    "source_case",
    "proposals",
    "model_note",
    "supersedes",
    "previous",
    "author",
    "author_role",
    "created_at",
    "confirmed_at",
    "approver",
    "approved_at",
    "revoked_by",
    "revoked_at",
    "recheck_at",
    "recheck_case",
)


def today(now: datetime | None = None) -> date:
    """День по Москве: по нему считается, истекло ли заключение."""
    return msk_day(now or datetime.now(UTC))


def view(
    conclusion_id: str,
    body: Mapping[str, Any],
    version: int,
    *,
    day: date,
    tally: tuple[int, int] = (0, 0),
    superseded_by: str | None = None,
) -> dict[str, Any]:
    """Ответ API о заключении: действующий статус, отпечаток вывода, число применений и история."""
    status = effective_status(body, day)
    outcome = body.get("outcome")
    return {
        "id": conclusion_id,
        "version": version,
        "status": status,
        "status_label": STATUS_RU.get(status, status),
        "outcome_label": OUTCOME_RU.get(str(outcome)) if outcome else "вывод не записан",
        **{name: body.get(name) for name in FIELDS},
        "superseded_by": superseded_by,
        "content_sha256": content_sha256(body),
        "applications": {"applied": tally[0], "cleared": tally[1]},
        "history": list(body.get("history") or []),
    }


def listing(rows: Sequence[Row], *, day: date, tallies: Mapping[str, tuple[int, int]]) -> list[dict[str, Any]]:
    """Заключения организации, новые сверху. ``superseded_by`` — какое заключение пришло на смену прежнему."""
    replaced = {old: cid for cid, body, _ in rows for old in body.get("supersedes") or [] if isinstance(old, str)}
    items = [
        view(cid, body, version, day=day, tally=tallies.get(cid, (0, 0)), superseded_by=replaced.get(cid))
        for cid, body, version in rows
    ]
    items.sort(key=lambda item: (str(item.get("created_at") or ""), item["id"]), reverse=True)
    return items


def cleared(report: Mapping[str, Any]) -> bool:
    """Устав в отчёте закрыт и кейс не ушёл к юристу: так реестр считает операции, закрытые заключением."""
    status = ((report.get("checks") or {}).get("charter") or {}).get("status")
    return status not in {"review", "not_verified", "refuted", "missing"}


def for_rules(rows: Sequence[Row]) -> tuple[tuple[Conclusion, ...], list[str]]:
    """Записи для проверки кейса и номера записей, которые не читаются: такие не применяются."""
    parsed: list[Conclusion] = []
    broken: list[str] = []
    for cid, body, version in rows:
        try:
            item = Conclusion.from_record(body, version)
        except (ValueError, TypeError):
            broken.append(cid)
            continue
        if item.id != cid:
            broken.append(cid)
            continue
        parsed.append(item)
    return tuple(parsed), broken


def previous(rows: Sequence[Row], charter_sha256: str, limit: int = 3) -> list[dict[str, Any]]:
    """Действовавшие заключения по другой редакции устава: с них начинается новый разбор."""
    out = []
    for cid, body, version in rows:
        if body.get("status") in {"approved", "recheck"} and body.get("charter_sha256") != charter_sha256:
            out.append(
                {
                    "id": cid,
                    "version": version,
                    "status": body.get("status"),
                    "outcome": body.get("outcome"),
                    "restrictions": body.get("restrictions") or [],
                    "approver": body.get("approver"),
                    "approved_at": body.get("approved_at"),
                    "valid_until": body.get("valid_until"),
                    "charter_sha256": body.get("charter_sha256"),
                }
            )
    out.sort(key=lambda row: str(row.get("approved_at") or ""), reverse=True)
    return out[:limit]
