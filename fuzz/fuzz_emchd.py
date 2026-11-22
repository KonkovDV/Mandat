"""Фаззинг разбора МЧД в XML: любые байты дают доверенность или отказ разбора, а не падение.

Вход — недоверенный файл клиента. Допустимы только ``EmchdError`` (не та схема или поле) и ``XmlRejected``
(сущности, глубина, размер, битый XML). Всё остальное — дефект разбора.
"""

from __future__ import annotations

from _harness import instrumented, run

with instrumented():
    from mandat.intake.emchd import EmchdError, parse_emchd
    from mandat.intake.xml_safety import XmlRejected


def test_one_input(data: bytes) -> None:
    try:
        parse_emchd(data, doc_id="fuzz")
    except (EmchdError, XmlRejected):
        pass


if __name__ == "__main__":
    run(test_one_input)
