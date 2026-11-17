"""Кандидатные факты об ограничениях полномочий руководителя из текста устава.

Базовый извлекатель — детерминированный разбор. Он ищет только признаки, по
которым кейс нужно отдать человеку: требование одобрения, совместную подпись,
порог суммы. Отсутствие находок не доказывает отсутствие ограничений — правило
R-CHR-002 так и пишет в отчёт.

Разбор линейный: текст сначала режется на предложения и слова, признаки ищутся по
началу слов, а не регулярными выражениями с вложенными повторами. Сумму ищет одно
выражение без вложенных квантификаторов. Если в предложении есть признаки ограничения,
а порог извлечь не удалось, получается факт ``unparsed_restriction``: такое ограничение
тоже повод для разбора, а не молчаливое «подтверждено».

Языковая модель может предложить такие же кандидаты (``mandat.advisory``), но
каждый кандидат проходит проверку цитаты: без дословной цитаты из устава факт
не засчитывается.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from mandat.domain.model import EvidenceRef, ExtractionMethod, TextDocument

# Конец предложения: точка с запятой, перевод строки или точка перед заглавной буквой и концом текста.
# Точки в «руб.», «тыс.» и «300.000» предложение не обрывают.
_SENTENCE_END = re.compile(r"[;\n]|[.!?](?=[ \t]*$|[ \t]+[А-ЯЁA-Z«\"(])")
_WORD = re.compile(r"\w+")
_NUMBER = (
    r"(?<![0-9.,])(?P<num>[0-9]{1,3}(?:[ \u00a0\u202f.,][0-9]{3})+(?:,[0-9]{1,2})?(?![0-9])"
    r"|[0-9]+(?:[.,][0-9]+)?)"
)
_AMOUNT = re.compile(
    _NUMBER
    + r"[ \u00a0\u202f]*(?:\([^()]{0,200}\)[ \u00a0\u202f]*)?"
    + r"(?P<mult>тыс[а-я]*\.?|млн\.?|миллион[а-я]*)?[ \u00a0\u202f]*"
    + r"(?P<cur>руб[а-я]*\.?|₽|rub(?![a-z]))",
    re.IGNORECASE,
)
_MULTIPLIER = {"тыс": Decimal(1000), "млн": Decimal(1_000_000), "миллион": Decimal(1_000_000)}

APPROVAL_STEMS = ("одобрен", "согласи", "согласован", "решени")
RESTRICTION_STEMS = ("сделк", "сумм", "одобрен", "согласи", "решени", "совместн")
INCLUSIVE_BEFORE = ("не менее", "от ")
INCLUSIVE_AFTER = ("и более", "и выше", "и свыше")


@dataclass(frozen=True)
class CandidateFact:
    kind: str  # approval_threshold | joint_signature | large_deal_approval | unparsed_restriction
    value: int | Decimal | None
    quote: str
    ref: EvidenceRef
    method: ExtractionMethod = ExtractionMethod.HEURISTIC
    confidence: float = 0.6
    inclusive: bool = False  # порог «и более»: сумма, равная порогу, тоже требует одобрения


def _words(text: str) -> list[str]:
    return [m.group(0).casefold() for m in _WORD.finditer(text)]


def _has(words: list[str], stems: tuple[str, ...]) -> bool:
    return any(word.startswith(stems) for word in words)


def _pair(words: list[str], first: tuple[str, ...], second: tuple[str, ...], gap: int = 2) -> bool:
    """Слово с началом из ``first``, а за ним не дальше ``gap`` слов — слово с началом из ``second``."""
    for i, word in enumerate(words):
        if word.startswith(first) and any(w.startswith(second) for w in words[i + 1 : i + 1 + gap]):
            return True
    return False


def _joint(words: list[str]) -> bool:
    if any(w == "совместно" and i + 1 < len(words) and words[i + 1] == "с" for i, w in enumerate(words)):
        return True
    return (
        _pair(words, ("совместн",), ("подпис", "действ", "решени"))
        or _pair(words, ("двумя",), ("подпис",), 1)
        or _pair(words, ("только",), ("совместно",), 1)
        or _pair(words, ("втор",), ("подпис",), 1)
    )


def _to_decimal(raw: str) -> Decimal | None:
    text = raw.replace("\u00a0", " ").replace("\u202f", " ")
    if re.fullmatch(r"[0-9]{1,3}(?:[ .,][0-9]{3})+(?:,[0-9]{1,2})?", text):
        head, _, kopecks = text.rpartition(",") if re.search(r",[0-9]{1,2}$", text) else (text, "", "")
        digits = re.sub(r"[ .,]", "", head)
        text = digits + ("." + kopecks if kopecks else "")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_amount(text: str) -> tuple[int | Decimal, bool] | None:
    """Порог суммы из предложения: значение и признак «включительно». Нет суммы в рублях — ``None``."""
    match = _AMOUNT.search(text)
    if not match:
        return None
    value = _to_decimal(match.group("num"))
    if value is None:
        return None
    mult = (match.group("mult") or "").casefold()
    for stem, factor in _MULTIPLIER.items():
        if mult.startswith(stem):
            value *= factor
    before = text[max(0, match.start() - 20) : match.start()].casefold()
    after = text[match.end() : match.end() + 20].casefold()
    inclusive = any(p in before for p in INCLUSIVE_BEFORE) or any(p in after for p in INCLUSIVE_AFTER)
    if not any(p in before for p in ("свыше", "более", "превыша", "больше")):
        inclusive = True  # сравнения нет: считаем порог включительным, так строже
    return (int(value) if value == value.to_integral_value() else value), inclusive


def _sentences(text: str) -> list[tuple[int, int, str]]:
    out, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        out.append((start, match.end(), text[start : match.end()]))
        start = match.end()
    if start < len(text):
        out.append((start, len(text), text[start:]))
    return [(s, e, chunk.strip()) for s, e, chunk in out if chunk.strip()]


def extract_charter_facts(doc: TextDocument) -> list[CandidateFact]:
    facts: list[CandidateFact] = []
    sentences = _sentences(doc.text)
    tokens = [_words(sentence) for _, _, sentence in sentences]
    for i, (start, end, sentence) in enumerate(sentences):
        words = tokens[i]
        nearby = words + (tokens[i + 1] if i + 1 < len(tokens) else []) + (tokens[i - 1] if i else [])
        ref = EvidenceRef(doc.doc_id, doc.sha256, f"text:{start}-{end}", quote=sentence[:400])
        found = False
        if _joint(words):
            facts.append(CandidateFact("joint_signature", None, sentence, ref))
            found = True
        amount = parse_amount(sentence)
        if _pair(words, ("крупн",), ("сделк",), 1):
            value, inclusive = amount if amount else (None, False)
            facts.append(CandidateFact("large_deal_approval", value, sentence, ref, inclusive=inclusive))
            found = True
        elif amount is not None and _has(nearby, APPROVAL_STEMS):
            facts.append(CandidateFact("approval_threshold", amount[0], sentence, ref, inclusive=amount[1]))
            found = True
        if not found and (_has(words, RESTRICTION_STEMS) or _pair(words, ("втор",), ("подпис",), 1)):
            facts.append(CandidateFact("unparsed_restriction", None, sentence, ref, confidence=0.3))
    return facts


def quote_is_grounded(quote: str, source: str) -> bool:
    """Цитата обязана встречаться в источнике дословно (с точностью до пробелов)."""

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    return bool(quote.strip()) and norm(quote) in norm(source)
