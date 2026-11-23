"""Фазз-цели из fuzz/ работают и без atheris, а найденное фаззером не возвращается.

ClusterFuzzLite гоняет цели часами (.github/workflows/cflite.yml). Здесь быстрая проверка: каждая цель
проходит затравочный корпус и детерминированные мутации, допустимые ошибки в ней совпадают с кодом,
а вход, на котором фаззер нашёл падение, даёт отказ разбора.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from collections.abc import Callable

import pytest
from conftest import ROOT

from mandat.intake.case_loader import load_case_inline
from mandat.intake.emchd import parse_emchd
from mandat.intake.xml_safety import XmlRejected

FUZZ = ROOT / "fuzz"


def _load(name: str):
    sys.path.insert(0, str(FUZZ))
    try:
        spec = importlib.util.spec_from_file_location(name, FUZZ / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(FUZZ))
    return module


SEEDS = _load("seed_corpus").seeds()


def _mutations(seed: bytes, rnd: random.Random, count: int) -> list[bytes]:
    out = []
    for _ in range(count):
        data = bytearray(seed)
        for _ in range(rnd.randint(1, 8)):
            pos = rnd.randrange(len(data) + 1)
            kind = rnd.randrange(3)
            if kind == 0 and data:
                data[min(pos, len(data) - 1)] = rnd.randrange(256)
            elif kind == 1:
                data[pos:pos] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 6)))
            else:
                del data[pos : pos + rnd.randint(1, 12)]
        out.append(bytes(data))
    return out


@pytest.mark.parametrize("name", ["fuzz_emchd", "fuzz_egrul", "fuzz_privacy", "fuzz_case", "fuzz_monitoring"])
def test_target_survives_its_seeds_and_mutations(name):
    target: Callable[[bytes], None] = _load(name).test_one_input
    seeds = [data for _, data in SEEDS[name]]
    assert seeds, f"{name}: пустой затравочный корпус"
    rnd = random.Random(f"mandat-{name}")
    for seed in seeds:
        target(seed)
        for data in _mutations(seed, rnd, 12):
            target(data)
    for size in (0, 1, 7, 64):
        target(bytes(rnd.randrange(256) for _ in range(size)))


@pytest.mark.parametrize("encoding", ["UTF-7", "utf-32", "Shift_JIS", "utf-16-le"])
def test_xml_in_a_multibyte_encoding_is_rejected_not_raised(encoding):
    """Находка fuzz_emchd: expat отвечал ValueError на кодировку в прологе, и исключение уходило мимо XmlRejected."""
    xml = f'<?xml version="1.0" encoding="{encoding}"?>\n<Доверенность ВерсФорм="EMCHD_1"/>'.encode()
    with pytest.raises(XmlRejected, match="кодировк"):
        parse_emchd(xml, doc_id="poa")


def test_inline_document_that_is_not_text_is_an_intake_error_of_that_document():
    """Документ с content не строкой не роняет приём: он попадает в ошибки входа и виден в отчёте."""
    manifest = {
        "case_id": "c",
        "organization": {"inn": "7729985006"},
        "operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20"},
        "documents": [{"id": "poa-root", "kind": "emchd_xml", "content": 42}],
    }
    case = load_case_inline(manifest)
    assert [(e.doc_id, "content" in e.error) for e in case.intake_errors] == [("poa-root", True)]
