"""Шлюз детерминизма: что модели можно и чего нельзя.

Перенесено из AeroBIM (``determinism_gate``, ``llm_advisory``). Предложения модели
становятся находками с происхождением ``advisory`` и эффектом «справка»:
свёртка вердикта их не учитывает. Кандидат без дословной цитаты из источника
отбрасывается («без цитаты нет факта»).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mandat.domain.model import EvidenceRef, ExtractionMethod, Finding, TextDocument
from mandat.domain.verdict import LegalEffect
from mandat.intake.charter import CandidateFact, quote_is_grounded

FORBIDDEN_LLM_ACTIONS = frozenset({"change_verdict", "approve_rule", "grant_right", "call_tool", "send_data"})
LLM_WRITES_VERDICT = False
LLM_SELECTS_RULES = False


@dataclass(frozen=True)
class GateResult:
    accepted: tuple[CandidateFact, ...]
    dropped: tuple[str, ...]


def _confidence(raw: object) -> float | None:
    """Уверенность модели: конечное число от 0 до 1, не boolean и не строка. Иначе ``None``."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None


def _amount(raw: object) -> int | None:
    """Сумма из ответа модели: только целое неотрицательное число без потери точности, не boolean."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float) and math.isfinite(raw) and raw.is_integer() and raw >= 0:
        return int(raw)
    return None


def ground_candidates(raw: list[dict[str, Any]], doc: TextDocument, *, min_confidence: float = 0.5) -> GateResult:
    """Оставляем только кандидатов с цитатой из документа и достаточной уверенностью."""
    if not (isinstance(min_confidence, (int, float)) and math.isfinite(min_confidence) and 0 <= min_confidence <= 1):
        # NaN в пороге сделал бы сравнение всегда ложным: факт с любой уверенностью прошёл бы.
        raise ValueError("min_confidence: конечное число от 0 до 1")
    accepted: list[CandidateFact] = []
    dropped: list[str] = []
    for item in raw:
        quote = str(item.get("quote") or "")
        kind = str(item.get("kind") or "")
        confidence = _confidence(item.get("confidence"))
        if kind not in {"approval_threshold", "joint_signature", "large_deal_approval"}:
            dropped.append(f"неизвестный вид факта {kind!r}")
            continue
        if not quote_is_grounded(quote, doc.text):
            dropped.append("цитата не найдена в документе")
            continue
        if confidence is None:
            dropped.append("уверенность не число от 0 до 1: кейс уходит человеку")
            continue
        if confidence < min_confidence:
            dropped.append("низкая уверенность: кейс уходит человеку")
            continue
        start = doc.text.find(quote.strip()[:40])
        locator = f"text:{start}-{start + len(quote)}" if start >= 0 else "text"
        value = item.get("value")
        accepted.append(
            CandidateFact(
                kind,
                _amount(value),
                quote,
                EvidenceRef(doc.doc_id, doc.sha256, locator, quote=quote[:400]),
                ExtractionMethod.LLM_ADVISORY,
                confidence,
            )
        )
    return GateResult(tuple(accepted), tuple(dropped))


def as_advisory_findings(candidates: tuple[CandidateFact, ...]) -> list[Finding]:
    return [
        Finding(
            rule_id="ADVISORY-LLM",
            check="charter",
            effect=LegalEffect.INFO,
            message=f"Модель предлагает факт «{c.kind}» по цитате; решение — за правилами и сотрудником",
            norm_ref="—",
            refs=(c.ref,),
            rule_status="advisory",
            origin="advisory",
        )
        for c in candidates
    ]
