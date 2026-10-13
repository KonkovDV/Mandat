"""Снимок единого классификатора полномочий с датами начала и исключения кода.

Классификатор ведётся в ЕСНСИ; у записи есть поля STARTED и REVOKED, поэтому
действие кода проверяется на дату выдачи МЧД и на дату операции (правило R-COV-002).
В стенде снимок синтетический, настоящий собирается ``scripts/import_classifier.py``.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from mandat.data import load_json, sha256_json


@dataclass(frozen=True)
class ClassifierEntry:
    code: str
    mnemonic: str | None
    name: str | None
    started: date | None
    revoked: date | None

    def active_on(self, day: date) -> bool:
        if self.started and day < self.started:
            return False
        return not (self.revoked and day >= self.revoked)


@dataclass(frozen=True)
class Classifier:
    classifier_id: str
    version: str
    sha256: str
    synthetic: bool
    entries: dict[str, ClassifierEntry]

    def get(self, code: str | None) -> ClassifierEntry | None:
        return self.entries.get(code or "")

    def summary(self) -> dict[str, Any]:
        return {
            "classifier_id": self.classifier_id,
            "version": self.version,
            "sha256": self.sha256,
            "synthetic": self.synthetic,
        }


def _d(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if "." in text[:10]:
        dd, mm, yyyy = text[:10].split(".")
        return date(int(yyyy), int(mm), int(dd))
    return date.fromisoformat(text[:10])


def _no_repeats(codes: list[str]) -> None:
    """Код классификатора встречается один раз: какая из двух записей верна, не выбираем."""
    seen: set[str] = set()
    repeated: set[str] = set()
    for code in codes:
        if code in seen:
            repeated.add(code)
        seen.add(code)
    if repeated:
        raise ValueError("коды классификатора повторяются: " + ", ".join(sorted(repeated)))


def from_payload(payload: dict[str, Any]) -> Classifier:
    rows = payload.get("entries") or []
    _no_repeats([str(row["code"]) for row in rows])
    entries = {
        str(row["code"]): ClassifierEntry(
            code=str(row["code"]),
            mnemonic=row.get("mnemonic"),
            name=row.get("name"),
            started=_d(row.get("started")),
            revoked=_d(row.get("revoked")),
        )
        for row in rows
    }
    return Classifier(
        classifier_id=str(payload.get("classifier_id")),
        version=str(payload.get("version")),
        sha256=sha256_json(payload),
        synthetic=bool(payload.get("synthetic", False)),
        entries=entries,
    )


def default_classifier() -> Classifier:
    return from_payload(load_json("authority_classifier.json"))


ESNSI_COLUMNS = {"code": "CODE", "mnemonic": "MNEMONIC", "name": "NAME", "started": "STARTED", "revoked": "REVOKED"}


def import_esnsi(raw: bytes, *, source: str) -> dict[str, Any]:
    """Выгрузка справочника ЕСНСИ (CSV с заголовком или JSON-массив) → снимок «Мандата».

    Ожидаются поля CODE, MNEMONIC, NAME, STARTED, REVOKED (так они названы в справочнике).
    """
    text = raw.decode("utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("["):
        rows = json.loads(stripped)
    else:
        sample = text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    entries = []
    for row in rows:
        upper = {str(k).strip().upper(): v for k, v in row.items()}
        code = (upper.get("CODE") or "").strip()
        if not code:
            continue
        entry = {key: (upper.get(col) or None) for key, col in ESNSI_COLUMNS.items()}
        entry["code"] = code
        for key in ("started", "revoked"):
            parsed = _d(entry[key])
            entry[key] = parsed.isoformat() if parsed else None
        entries.append(entry)
    if not entries:
        raise ValueError("в выгрузке нет записей с полем CODE")
    _no_repeats([str(entry["code"]) for entry in entries])
    return {
        "classifier_id": "esnsi-authority-classifier",
        "version": date.today().isoformat(),
        "source": source,
        "synthetic": False,
        "entries": sorted(entries, key=lambda e: e["code"]),
    }
