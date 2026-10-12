"""Кандидатные факты об ограничениях полномочий руководителя из текста устава.

Базовый извлекатель — детерминированные шаблоны. Он ищет только признаки, по
которым кейс нужно отдать человеку: требование одобрения, совместную подпись,
порог суммы. Отсутствие находок не доказывает отсутствие ограничений — правило
R-CHR-002 так и пишет в отчёт.

Языковая модель может предложить такие же кандидаты (``mandat.advisory``), но
каждый кандидат проходит проверку цитаты: без дословной цитаты из устава факт
не засчитывается.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mandat.domain.model import EvidenceRef, ExtractionMethod, TextDocument

_SENTENCE = re.compile(r"[^.;\n]+[.;\n]?")
_APPROVAL = re.compile(r"(одобрени\w*|согласовани\w*|решени\w* общего собрания|совет\w* директоров)", re.I)
_LARGE_DEAL = re.compile(r"крупн\w+\s+сделк", re.I)
_JOINT = re.compile(r"(совместн\w+\s+(подпис|действ|решени)|двумя\s+подписями|только\s+совместно)", re.I)
_AMOUNT = re.compile(
    r"(?:свыше|более|превыша\w*|не\s+более|до)\s+((?:\d[\d\s]{2,}\d)|\d{4,})\s*(?:\([^)]*\)\s*)?(руб|₽|рублей)",
    re.I,
)


@dataclass(frozen=True)
class CandidateFact:
    kind: str  # approval_threshold | joint_signature | large_deal_approval
    value: int | None
    quote: str
    ref: EvidenceRef
    method: ExtractionMethod = ExtractionMethod.HEURISTIC
    confidence: float = 0.6


def _amount(text: str) -> int | None:
    match = _AMOUNT.search(text)
    if not match:
        return None
    return int(re.sub(r"\s", "", match.group(1)))


def extract_charter_facts(doc: TextDocument) -> list[CandidateFact]:
    facts: list[CandidateFact] = []
    for match in _SENTENCE.finditer(doc.text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        start, end = match.span()
        ref = EvidenceRef(doc.doc_id, doc.sha256, f"text:{start}-{end}", quote=sentence[:400])
        if _JOINT.search(sentence):
            facts.append(CandidateFact("joint_signature", None, sentence, ref))
        if _LARGE_DEAL.search(sentence):
            facts.append(CandidateFact("large_deal_approval", _amount(sentence), sentence, ref))
        elif _APPROVAL.search(sentence) and _amount(sentence) is not None:
            facts.append(CandidateFact("approval_threshold", _amount(sentence), sentence, ref))
    return facts


def quote_is_grounded(quote: str, source: str) -> bool:
    """Цитата обязана встречаться в источнике дословно (с точностью до пробелов)."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip().casefold()  # noqa: E731
    return bool(quote.strip()) and norm(quote) in norm(source)
