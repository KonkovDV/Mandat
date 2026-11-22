"""Фаззинг маскировки перед моделью: любой текст размечается без исключений и без зависания.

Регулярные выражения ``privacy/pii.py`` разбирают номера с пробелами, точками и дефисами; фаззер ищет вход,
на котором они падают или работают дольше тайм-аута libFuzzer.
"""

from __future__ import annotations

from _harness import instrumented, run

with instrumented():
    from mandat.privacy.pii import pseudonymize, redact


def test_one_input(data: bytes) -> None:
    text = data.decode("utf-8", errors="replace")
    redact(text)
    pseudonymize(text)


if __name__ == "__main__":
    run(test_one_input)
