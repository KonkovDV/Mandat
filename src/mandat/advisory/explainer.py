"""Пояснение для клиента или сотрудника только по фактам отчёта.

Перенесено из SynAPS-ProgramPlan (``copilot.py``): фраза без ссылки на известный
факт отбрасывается, фраза с числом или датой, которых нет в процитированных
фактах, — тоже. Без фактов запрос к модели не отправляется.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMBER = re.compile(r"\d+")

SYSTEM = (
    "Ты объясняешь сотруднику банка результат проверки полномочий только по переданным фактам. "
    'Ответь JSON: {"statements":[{"text":"...","fact_ids":["..."]}]}. Каждая фраза ссылается на '
    "идентификаторы фактов. Не меняй вердикт, не придумывай документы, суммы и даты."
)


@dataclass(frozen=True)
class Fact:
    id: str
    text: str


@dataclass(frozen=True)
class Statement:
    text: str
    fact_ids: tuple[str, ...]


def facts_of(report: dict[str, Any]) -> list[Fact]:
    facts = [Fact("verdict", f"Вердикт: {report['verdict_label']}")]
    facts += [Fact(f["finding_id"], f["message"]) for f in report.get("findings", [])]
    facts += [Fact(f"step:{i}", s["action"]) for i, s in enumerate(report.get("next_steps", []), start=1)]
    return facts


def numbers_in(text: str) -> set[str]:
    found: set[str] = set()
    for m in _DATE.finditer(text):
        found.add(
            f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m.group(1) else f"{m.group(4)}-{m.group(5)}-{m.group(6)}"
        )
    found.update(str(int(x)) for x in _NUMBER.findall(_DATE.sub(" ", text)))
    return found


def _polarity(text: str) -> set[str]:
    """Метки смысла: подтверждение, отказ, разбор. «Не подтверждено» — это отказ, не подтверждение."""
    low = text.casefold().replace("ё", "е")
    tags: set[str] = set()
    if re.search(r"не\s+подтвержд", low):
        tags.add("refute")
    elif "подтвержд" in low:
        tags.add("confirm")
    refute_stems = ("опроверг", "отозван", "отменен", "не вправе", "нельзя проводить", "нельзя подписывать")
    if any(stem in low for stem in refute_stems):
        tags.add("refute")
    if "можно проводить" in low or "можно подписывать" in low:
        tags.add("confirm")
    if "недостаточно данных" in low:
        tags.add("insufficient")
    if "на разбор" in low:
        tags.add("review")
    return tags


def _statements(raw: str) -> list[tuple[str, list[str]]]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        rows = json.loads(raw[start : end + 1]).get("statements")
    except (json.JSONDecodeError, AttributeError):
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            ids = row.get("fact_ids")
            out.append(
                (
                    str(row.get("text") or ""),
                    [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [],
                )
            )
    return out


def explain(
    question: str, facts: list[Fact], complete: Callable[[list[dict[str, str]]], str]
) -> tuple[list[Statement], int]:
    if not facts:
        raise ValueError("без фактов модель не вызывается")
    known = {f.id: f.text for f in facts}
    listing = "\n".join(f"{f.id}: {f.text}" for f in facts)
    raw = complete(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Факты:\n{listing}\n\nВопрос: {question}"},
        ]
    )
    kept: list[Statement] = []
    dropped = 0
    for text, cited in _statements(raw):
        if not text.strip() or not cited or any(c not in known for c in cited):
            dropped += 1
            continue
        allowed = set().union(*(numbers_in(known[c]) for c in cited))
        if not numbers_in(text) <= allowed:
            dropped += 1
            continue
        # Язык утверждения не шире языка процитированных фактов (OMEGA: publication gate).
        cited_text = "\n".join(known[c] for c in cited)
        if not _polarity(text) <= _polarity(cited_text):
            dropped += 1
            continue
        kept.append(Statement(text.strip(), tuple(cited)))
    return kept, dropped
