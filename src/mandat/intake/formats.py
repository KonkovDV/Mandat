"""Реестр форматов машиночитаемых доверенностей.

По 315-ФЗ от 04.08.2026 (вступает 01.09.2027) единая форма МЧД становится
универсальной, но специальные формы сохраняются как дополнительные: Банк России
вправе утверждать свои формы для участников финансового рынка, нотариат — свои.
Поэтому разбор идёт через адаптеры: ядро получает ``PowerOfAttorney`` и не знает,
из какого формата он пришёл. Новый формат — новый файл соответствия и строка здесь.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mandat.domain.model import PowerOfAttorney
from mandat.intake.emchd import EmchdError, parse_emchd


@dataclass(frozen=True)
class PoaFormat:
    format_id: str
    title: str
    status: str  # supported | planned
    basis: str
    parser: Callable[..., PowerOfAttorney] | None = None


FORMATS: dict[str, PoaFormat] = {
    "EMCHD_1": PoaFormat(
        "EMCHD_1",
        "Единая МЧД, формат 003",
        "supported",
        "63-ФЗ, ст. 17.4; Приказ Минцифры № 857 в ред. Приказа № 1001 (с 01.02.2026)",
        parse_emchd,
    ),
    "CBR_FINMARKET": PoaFormat(
        "CBR_FINMARKET",
        "Форма МЧД Банка России для участников финансового рынка",
        "planned",
        "315-ФЗ от 04.08.2026: Банк России вправе утверждать свои формы (с 01.09.2027)",
    ),
    "NOTARIAL": PoaFormat(
        "NOTARIAL",
        "Нотариально удостоверенная МЧД",
        "planned",
        "315-ФЗ от 04.08.2026: специальные формы сохраняются как дополнительные",
    ),
}


def parse_poa(raw: bytes, *, doc_id: str) -> PowerOfAttorney:
    """Разбирает МЧД через адаптер. Неизвестная версия разбирается как 003, правило R-FMT-002 пометит её."""
    poa = parse_emchd(raw, doc_id=doc_id)
    fmt = FORMATS.get(poa.format_version or "")
    if fmt is not None and fmt.status != "supported":
        raise EmchdError(f"формат {fmt.title} пока не поддерживается: {fmt.basis}")
    return poa


def describe() -> list[dict[str, str]]:
    return [
        {"format_id": f.format_id, "title": f.title, "status": f.status, "basis": f.basis} for f in FORMATS.values()
    ]
