"""Фаззинг манифеста кейса: любой JSON даёт кейс или ``CaseError`` и ошибки разбора его документов."""

from __future__ import annotations

import json

from _harness import instrumented, run

with instrumented():
    from mandat.intake.case_loader import CaseError, load_case_inline
    from mandat.intake.egrul import EgrulError
    from mandat.intake.emchd import EmchdError
    from mandat.intake.xml_safety import XmlRejected


def test_one_input(data: bytes) -> None:
    try:
        manifest = json.loads(data)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return
    if not isinstance(manifest, dict):
        return
    try:
        load_case_inline(manifest)
    except (CaseError, EgrulError, EmchdError, XmlRejected):
        pass


if __name__ == "__main__":
    run(test_one_input)
