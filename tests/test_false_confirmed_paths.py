"""Пути к ложному confirmed: руководитель, реквизиты, банкротство.

Каждый тест мутирует базовый кейс, который без дефекта получает confirmed (устав с отметкой,
режим demo). Поэтому «не confirmed» здесь не может пройти из-за неразмеченного устава:
это проверяет ``test_base_cases_are_confirmed``.
"""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta

import pytest

from mandat.domain.identifiers import make_ogrn
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case

W = synth.World.make(1)
OP = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    monkeypatch.delenv("MANDAT_ENV", raising=False)


def base(kind: str = "ok_charter_cleared") -> dict:
    m = copy.deepcopy(synth.build(kind, 1).inline_manifest())
    m["charter_disposition"] = "no_applicable_restriction"
    if kind == "ok_director":
        # СНИЛС руководителя есть в выписке: без этого СНИЛС заявителя сверить не с чем.
        record = doc(m, "egrul")["content"]["executives"][0]
        assert record["inn"] == m["requester"]["inn"]
        record["snils"] = m["requester"]["snils"]
    return m


def doc(m: dict, doc_id: str) -> dict:
    return next(d for d in m["documents"] if d["id"] == doc_id)


def verdict(m: dict) -> str:
    return verify_case(load_case_inline(m), profile="demo").verdict.value


@pytest.mark.parametrize("kind", ["ok_charter_cleared", "ok_director"])
def test_base_cases_are_confirmed(kind):
    assert verdict(base(kind)) == "confirmed"


def test_new_director_while_old_record_open():
    m = base()
    start = (OP - timedelta(days=3)).isoformat()
    doc(m, "egrul")["content"]["executives"].append(
        {
            "full_name": W.stranger.full_name,
            "inn": W.stranger.inn,
            "position": "Генеральный директор",
            "grn": "2267700777777",
            "record_date": start,
            "valid_from": start,
            "valid_to": None,
        }
    )
    assert verdict(m) != "confirmed"


def test_eio_direct_with_snils_of_other_person():
    m = base("ok_director")
    m["requester"]["snils"] = W.stranger.snils
    assert verdict(m) != "confirmed"


def test_foreign_inn_with_representative_snils():
    m = base()
    m["requester"]["inn"] = W.stranger.inn
    assert verdict(m) != "confirmed"


def test_ogrn_of_other_company():
    m = base()
    doc(m, "egrul")["content"]["ogrn"] = make_ogrn("102770013219")
    assert verdict(m) != "confirmed"


@pytest.mark.parametrize("attr", ["НомДовер", "ДатаВыдДовер", "СрокДейст", "ИННЮЛ", "ОГРН"])
def test_missing_poa_field(attr):
    m = base()
    d = doc(m, "poa-root")
    d["content"], n = re.subn(f' {attr}="[^"]*"', "", d["content"], count=1)
    assert n == 1
    assert verdict(m) != "confirmed"


@pytest.mark.parametrize(
    "raw", ["BANKRUPTCY_PROCEEDINGS", "конкурсное производство", "bankrupcy_proceedings", "", "  none "]
)
def test_bankruptcy_status_outside_dictionary(raw):
    m = base()
    m["bankruptcy"] = {m["organization"]["inn"]: raw}
    assert verdict(m) != "confirmed"


def test_bankruptcy_none_keeps_confirmed():
    m = base()
    m["bankruptcy"] = {m["organization"]["inn"]: "none"}
    assert verdict(m) == "confirmed"


def test_r_bnk_001_missing_bankruptcy_blocks_strict_confirmation():
    m = base()
    m.pop("bankruptcy", None)
    report = verify_case(load_case_inline(m), profile="strict")
    assert report.checks["bankruptcy"] == "not_verified"
    assert report.verdict.value != "confirmed"
    demo = verify_case(load_case_inline(m), profile="demo")
    assert demo.checks["bankruptcy"] == "waived"
    assert any("банкротстве" in w for w in demo.warnings)


@pytest.mark.parametrize("issued_days_before", [1, 4, 10])
def test_poa_signed_by_old_director_after_new_appointment(issued_days_before):
    """МЧД подписана прежним руководителем уже после назначения нового."""
    m = base()
    d = doc(m, "poa-root")
    issued = (OP - timedelta(days=issued_days_before)).isoformat()
    d["content"], n = re.subn(r'ДатаВыдДовер="[^"]*"', f'ДатаВыдДовер="{issued}"', d["content"], count=1)
    assert n == 1
    start = (OP - timedelta(days=5)).isoformat()
    doc(m, "egrul")["content"]["executives"].append(
        {
            "full_name": W.stranger.full_name,
            "inn": W.stranger.inn,
            "position": "Генеральный директор",
            "grn": "2267700777778",
            "record_date": start,
            "valid_from": start,
            "valid_to": None,
        }
    )
    assert verdict(m) != "confirmed"


def test_director_path_without_client_ogrn_is_not_confirmed():
    # Без ОГРН в заявке выписку ЕГРЮЛ сверить можно только по ИНН, как и МЧД без ОГРН.
    m = base("ok_director")
    m["organization"].pop("ogrn", None)
    doc(m, "egrul")["content"]["ogrn"] = "1027700132195"
    assert verdict(m) != "confirmed"


def test_poa_with_missing_format_fields_is_not_confirmed():
    import xml.etree.ElementTree as ET

    m = base()
    poa = doc(m, "poa-root")
    root = ET.fromstring(poa["content"])
    node = root.find("Документ/Довер/СвДоверит/Доверит/РосОргДовер/ЛицоБезДов/СвФЛ")
    assert node is not None and "Должность" in node.attrib
    del node.attrib["Должность"]
    poa["content"] = ET.tostring(root, encoding="unicode")
    assert verdict(m) != "confirmed"


@pytest.mark.parametrize(
    ("tag", "attr", "value"),
    [("СвДов", "НомДовер", "00000000-0000-0000-0000-000000000000"), ("СвРосОрг", "ИННЮЛ", "7700000013")],
)
def test_repeated_single_element_in_poa_is_not_read_silently(tag, attr, value):
    m = copy.deepcopy(synth.build("ok_charter_cleared", 1).inline_manifest())
    m["charter_disposition"] = "no_applicable_restriction"
    poa = next(d for d in m["documents"] if d["id"] == "poa-root")
    xml = poa["content"]
    start = xml.index(f"<{tag} ")
    end = xml.index(f"</{tag}>", start) + len(f"</{tag}>") if f"</{tag}>" in xml[start:] else xml.index("/>", start) + 2
    twin = re.sub(f'{attr}="[^"]*"', f'{attr}="{value}"', xml[start:end], count=1)
    poa["content"] = xml[:end] + twin + xml[end:]
    report = verify_case(load_case_inline(m), profile="demo")
    assert report.verdict.value != "confirmed"
