"""Фаззинг разбора выписки ЕГРЮЛ в JSON: любой вход даёт снимок реестра или ``EgrulError``."""

from __future__ import annotations

from _harness import instrumented, run

with instrumented():
    from mandat.intake.egrul import EgrulError, parse_egrul


def test_one_input(data: bytes) -> None:
    try:
        parse_egrul(data, doc_id="fuzz")
    except EgrulError:
        pass


if __name__ == "__main__":
    run(test_one_input)
