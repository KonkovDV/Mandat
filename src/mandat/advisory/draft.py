"""Проект заключения юриста по уставу кейса: факты, которые предложила модель банка и пропустил шлюз цитаты.

Модель вызывается только через ``advisory/llm.py`` (``llm.charter_candidates``) и видит обезличенные строки устава
о полномочиях. Каждый факт проходит ``gate.ground_candidates``: без дословной цитаты из устава или с низкой
уверенностью он в проект не попадает. Без модели проект пуст, а порядок тот же. Проект ничего не решает: заключение
действует только после того, как юрист-автор подтвердит или исправит его, а второй юрист утвердит
(``review/conclusions.py``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from mandat.advisory import llm
from mandat.advisory.gate import ground_candidates
from mandat.domain.model import Case

# Одновременных запросов проекта к модели: остальные получают пустой проект с пометкой «модель занята».
MODEL_SLOTS = 2
_slots = threading.BoundedSemaphore(MODEL_SLOTS)


@dataclass(frozen=True)
class Proposal:
    """Что модель дала проекту. ``origin`` — ``advisory`` (модель ответила) или ``case`` (проект без модели)."""

    origin: str
    proposals: tuple[dict[str, Any], ...]
    note: str
    dropped: tuple[str, ...] = ()


def propose(case: Case) -> Proposal:
    """Факты устава для проекта заключения. Любой сбой модели — пустой проект с причиной, а не ошибка запроса."""
    if case.charter is None:
        return Proposal("case", (), "в кейсе нет устава: модели нечего читать")
    if not _slots.acquire(blocking=False):
        return Proposal("case", (), "модель занята другим проектом: проект пуст, юрист заполняет его сам")
    try:
        raw, seen = llm.charter_candidates(case)
    except llm.LlmDisabled:
        return Proposal("case", (), "модель не подключена: проект пуст, юрист заполняет его сам")
    except Exception as exc:  # noqa: BLE001 — сеть, тайм-аут, адрес не из списка, ответ не по формату
        return Proposal("case", (), f"модель не ответила ({type(exc).__name__}): проект пуст, юрист заполняет его сам")
    finally:
        _slots.release()
    gate = ground_candidates(raw, case.charter, seen=seen)
    proposals = tuple(
        {
            "kind": fact.kind,
            "value_rub": int(fact.value) if fact.value is not None else None,
            "quote": fact.quote[:400],
            "confidence": round(float(fact.confidence), 2),
            "locator": fact.ref.locator,
            "method": fact.method.value,
        }
        for fact in gate.accepted
    )
    note = f"модель предложила фактов: {len(raw)}, шлюз цитаты пропустил: {len(proposals)}"
    return Proposal("advisory", proposals, note, gate.dropped)
