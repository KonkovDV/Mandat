"""Снимок единого классификатора полномочий с датами начала и исключения кода.

Классификатор ведётся в ЕСНСИ; у записи есть поля STARTED и REVOKED, поэтому
действие кода проверяется на дату выдачи МЧД и на дату операции (правило R-COV-002).
В стенде снимок синтетический, настоящий собирается ``scripts/import_classifier.py`` из выгрузки ЕСНСИ
или из JSON ФНС (``GetAllRecords`` сервиса kpmp.nalog.gov.ru, ``import_kpmp``).

Даты в JSON ФНС — моменты в UTC (``2023-08-29T21:00:00Z`` — это 30.08.2023 по Москве). В снимке они хранятся
как есть, с часовым поясом, а сравниваются по календарной дате в Europe/Moscow. Снимок верен на момент выгрузки:
параметр ``date`` сервиса прошлое не возвращает, поэтому для старых операций нужен снимок, сохранённый тогда же,
а операция позже снимка больше чем на ``MANDAT_CLASSIFIER_MAX_AGE_DAYS`` уходит на разбор (docs/CLASSIFIER.md).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from mandat.data import load_json, sha256_json


@dataclass(frozen=True)
class ClassifierEntry:
    code: str
    mnemonic: str | None
    name: str | None
    started: date | None
    revoked: date | None
    groups: tuple[str, ...] = ()
    # Код встречается в выгрузке несколько раз с разным содержанием: какая запись верна, не выбираем.
    ambiguous: bool = False

    def active_on(self, day: date) -> bool:
        """Нет даты начала — интервал не доказан, код не действует. Нет даты исключения — код не отозван."""
        if self.started is None or day < self.started:
            return False
        return self.revoked is None or day < self.revoked


@dataclass(frozen=True)
class Classifier:
    classifier_id: str
    version: str
    sha256: str
    synthetic: bool
    entries: dict[str, ClassifierEntry]
    # Момент выгрузки источника (UTC); у синтетического снимка его нет.
    snapshot_at: datetime | None = None

    def snapshot_day(self) -> date | None:
        return self.snapshot_at.astimezone(MSK).date() if self.snapshot_at else None

    def stale_for(self, day: date, max_days: int | None = None) -> int | None:
        """На сколько дней операция позже снимка сверх допуска; None — снимок годится или даты нет."""
        snap = self.snapshot_day()
        if snap is None:
            return None
        limit = max_age_days() if max_days is None else max_days
        lag = (day - snap).days
        return lag if lag > limit else None

    def get(self, code: str | None) -> ClassifierEntry | None:
        return self.entries.get(code or "")

    def summary(self) -> dict[str, Any]:
        return {
            "classifier_id": self.classifier_id,
            "version": self.version,
            "sha256": self.sha256,
            "synthetic": self.synthetic,
            "snapshot_at": self.snapshot_at.isoformat() if self.snapshot_at else None,
        }


MSK = ZoneInfo("Europe/Moscow")
MAX_AGE_ENV = "MANDAT_CLASSIFIER_MAX_AGE_DAYS"
# Допуск по умолчанию — осторожный инженерный выбор, а не частота обновления сервиса: график обновления
# ФНС не публикует (docs/CLASSIFIER.md, «Возраст снимка»). Значение не из норм, задаёт банк.
DEFAULT_MAX_AGE_DAYS = 3
log = logging.getLogger("mandat.classifier")


def max_age_days() -> int:
    raw = os.environ.get(MAX_AGE_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_DAYS
    if not raw.isdigit() or int(raw) > 365:
        raise ValueError(f"{MAX_AGE_ENV}: целое число дней от 0 до 365")
    return int(raw)


def _moment(value: Any) -> datetime | None:
    """Момент с часовым поясом (ISO 8601, ``Z`` или смещение) или None, если пояса нет."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) <= 10 or "T" not in text:
        return None
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"момент без часового пояса: {text!r}")
    return moment.astimezone(UTC)


def _d(value: Any) -> date | None:
    """Календарная дата по Москве: из даты как есть, из момента с поясом — переводом в Europe/Moscow."""
    if value in (None, ""):
        return None
    moment = _moment(value)
    if moment is not None:
        return moment.astimezone(MSK).date()
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
            groups=tuple(row.get("groups") or ()),
            ambiguous=bool(row.get("ambiguous", False)),
        )
        for row in rows
    }
    return Classifier(
        classifier_id=str(payload.get("classifier_id")),
        version=str(payload.get("version")),
        sha256=sha256_json(payload),
        synthetic=bool(payload.get("synthetic", False)),
        entries=entries,
        snapshot_at=_moment(payload.get("snapshot_at")),
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


# Поля записи ФНС, расхождение в которых делает код неоднозначным. groupId — не расхождение: один код в
# нескольких группах классификатора.
KPMP_FIELDS = (
    "code",
    "mnemonic",
    "name",
    "description",
    "started",
    "revoked",
    "exp",
    "nsiId",
    "pwrVis",
    "pwrRead",
    "pwrIss",
    "cxt",
    "legalRelations",
    "lawDetails",
)


def _utc(value: Any) -> str | None:
    moment = _moment(value)
    if value not in (None, "") and moment is None:
        raise ValueError(f"в выгрузке ФНС дата без времени и пояса: {value!r}")
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def import_kpmp(raw: bytes, *, source: str) -> dict[str, Any]:
    """JSON ФНС ``GetAllRecords`` (kpmp.nalog.gov.ru) → снимок «Мандата». Сеть не используется: только файл.

    Повторы кода: полностью одинаковые записи схлопываются; различие только в группе даёт список групп;
    различие по существу помечает код ``ambiguous``, и МЧД с таким кодом уходит на разбор (R-COV-002).
    """
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("classRecords"), list):
        raise ValueError("ожидался объект ФНС с полем classRecords")
    if str(payload.get("date") or "").startswith("0001-"):
        # Так сервис отвечает на параметр date в прошлом: справочника на ту дату нет.
        raise ValueError("выгрузка ФНС пустая: сервис не вернул справочник на эту дату")
    snapshot_at = _utc(payload.get("date"))
    if snapshot_at is None:
        raise ValueError("в выгрузке ФНС нет даты (поле date)")
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in payload["classRecords"]:
        code = str(row.get("code") or "").strip()
        if code:
            by_code.setdefault(code, []).append(row)
    if not by_code:
        raise ValueError("в выгрузке ФНС нет записей с кодом")
    entries = []
    collapsed = grouped = 0
    ambiguous: list[str] = []
    for code, rows in sorted(by_code.items()):
        variants = {json.dumps({k: r.get(k) for k in KPMP_FIELDS}, sort_keys=True, ensure_ascii=False) for r in rows}
        groups = sorted({str(r["groupId"]) for r in rows if r.get("groupId")})
        first = rows[0]
        if len(rows) > 1 and len(variants) == 1:
            if len(groups) > 1:
                grouped += 1
            else:
                collapsed += 1
                log.info("classifier_duplicate_collapsed code=%s rows=%d", code, len(rows))
        is_ambiguous = len(variants) > 1
        if is_ambiguous:
            ambiguous.append(code)
            log.warning("classifier_code_ambiguous code=%s rows=%d", code, len(rows))
        entries.append(
            {
                "code": code,
                "mnemonic": first.get("mnemonic"),
                "name": None if is_ambiguous else first.get("name"),
                "started": None if is_ambiguous else _utc(first.get("started")),
                "revoked": None if is_ambiguous else _utc(first.get("revoked")),
                "groups": groups,
                "ambiguous": is_ambiguous,
            }
        )
    return {
        "classifier_id": "fns-kpmp-authority-classifier",
        "version": snapshot_at[:10],
        "source": source,
        "snapshot_at": snapshot_at,
        "synthetic": False,
        "duplicates": {"collapsed": collapsed, "grouped": grouped, "ambiguous": ambiguous},
        "entries": entries,
    }
