"""Поиск адресов почты в ПДн и маска журнала сервера — за линейное время и с прежними совпадениями.

Выражение ``\\b[\\w.+-]+@…`` пробовало каждую границу слова внутри «a.a.a…» и сканировало хвост заново:
100 000 знаков устава разбирались 30 секунд. Маска журнала делала то же на строке без «@» и к тому же
обрезала текст после маски: причина ошибки с мегабайтом ввода держала процесс часами.
"""

from __future__ import annotations

import random
import re
import time

from mandat.api.app import scrub
from mandat.privacy import pii
from mandat.privacy.pii import LinearEmail

OLD_PII = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
OLD_LOG = re.compile(r"[^\s@'\"]+@[^\s@'\"]+")
OLD_DIGITS = re.compile(r"\+?\d(?:[ \t().\-‐-―/]*\d){4,}")


def _strings(seed: int, count: int, alphabet: str, longest: int):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(alphabet) for _ in range(rng.randint(0, longest)))


def test_linear_email_finds_exactly_what_the_old_expression_found():
    new = LinearEmail()
    for text in _strings(11, 30000, "ab1.+-@ _'\"я\n", 16):
        assert [m.span() for m in new.finditer(text)] == [m.span() for m in OLD_PII.finditer(text)], text
        assert new.sub(lambda m: "<e>", text) == OLD_PII.sub("<e>", text), text


def test_log_mask_is_the_old_mask_on_short_texts():
    for text in _strings(12, 30000, "ab1.@ '\"-", 18):
        old = OLD_LOG.sub("<email>", OLD_DIGITS.sub("<num>", text))[:300]
        assert scrub(text) == old, text


def _fast(call, seconds: float = 1.0) -> None:
    started = time.perf_counter()
    call()
    assert time.perf_counter() - started < seconds


def test_pseudonymization_is_linear_on_hostile_text():
    for text in ("a." * 50000, "a-" * 50000, "a." * 20000 + "@", "a@" * 50000):
        _fast(lambda text=text: pii.find(text))
        _fast(lambda text=text: pii.pseudonymize(text))


def test_log_mask_cuts_before_masking_and_hides_a_number_on_the_cut():
    _fast(lambda: scrub("a" * 1_000_000 + "@x", limit=300), 0.5)
    text = "x" * 290 + " ivanov.petr@mail.ru и паспорт 45 06 123456"
    out = scrub(text)
    assert len(out) == 300 and "ivanov" not in out and "123456" not in out
