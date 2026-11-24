"""МЧД, которую подписали несколько лиц, действующих без доверенности.

В XSD EMCHD_1 у ``ЛицоБезДов`` ``maxOccurs="unbounded"``: юрлицо, у которого несколько руководителей, выдаёт МЧД
за подписью каждого из них (п. 3 ст. 53 и п. 4 ст. 185.1 ГК РФ; так же проверяет подписи библиотека БСП 1С).
Раньше такой документ отвергался как «повтор элемента» и клиента просили прислать читаемую МЧД. Теперь он
разбирается, а R-EIO-001 сверяет с ЕГРЮЛ каждого подписанта и отдаёт на разбор МЧД, которую подписали не все
руководители: совместно они действуют или независимо, выписка не показывает.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET  # синтетика теста, внешних данных нет
from pathlib import Path

import pytest
from conftest import rule_effects, run

from mandat.domain.model import Person
from mandat.eval.synth import World, build
from mandat.intake.case_loader import load_case_inline
from mandat.intake.emchd import EmchdError, parse_emchd
from mandat.privacy.names import SIGNER, known_people
from mandat.report.html import render

DATA = Path(__file__).parent / "data" / "emchd"
SEED = 11
WORLD = World.make(SEED)
HOLDER = "Документ/Довер/СвДоверит/Доверит/РосОргДовер"
SECOND = (
    '<ЛицоБезДов ПолнЮЛ="2"><СвФЛ ИННФЛ="500100732259" СНИЛС="112-233-447 97" Должность="Президент">'
    '<СведФЛ><ФИО Фамилия="Второв" Имя="Семён"/></СведФЛ></СвФЛ></ЛицоБезДов>'
)


def _text(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def _two_signers(text: str) -> str:
    return text.replace("</ЛицоБезДов>", "</ЛицоБезДов>" + SECOND, 1)


# --- разбор -------------------------------------------------------------------------------------------------


def test_document_signed_by_two_persons_without_poa_is_parsed():
    poa = parse_emchd(_two_signers(_text("direct.xml")), doc_id="d")
    assert poa.signer.inn == "770000000156"
    assert [p.inn for p in poa.co_signers] == ["500100732259"]
    assert [p.label() for p in poa.signers] == ["Образцов Пётр Иванович", "Второв Семён"]
    assert poa.co_signers[0].position == "Президент"
    assert poa.format_gaps == () and poa.policy_gaps == () and poa.format_questions == ()
    assert poa.ref("signer.2").locator.endswith("РосОргДовер/ЛицоБезДов[2]")
    assert poa.ref("signer.inn").locator.endswith("ЛицоБезДов[1]/СвФЛ/@ИННФЛ")


def test_single_signer_keeps_its_references():
    """У МЧД с одним лицом ссылки прежние: находки и хеш отчёта у существующих кейсов не меняются."""
    poa = parse_emchd(_text("direct.xml"), doc_id="d")
    assert poa.co_signers == ()
    assert "signer.1" not in poa.refs
    assert poa.ref("signer.inn").locator.endswith("РосОргДовер/ЛицоБезДов/СвФЛ/@ИННФЛ")
    assert poa.signer_ref(1) == poa.ref("signer.inn")


def test_second_person_without_name_or_position_is_reported():
    no_name = SECOND.replace(' Имя="Семён"', "")
    poa = parse_emchd(_text("direct.xml").replace("</ЛицоБезДов>", "</ЛицоБезДов>" + no_name, 1), doc_id="d")
    assert "signer.2.fio" in poa.format_gaps
    no_position = SECOND.replace(' Должность="Президент"', "")
    poa = parse_emchd(_text("direct.xml").replace("</ЛицоБезДов>", "</ЛицоБезДов>" + no_position, 1), doc_id="d")
    assert "signer.2.position" in poa.policy_gaps


def test_second_person_in_another_form_is_parsed_without_identifiers():
    """Управляющая компания или ИП в роли лица без доверенности не разбираются: сверять не с чем, пробела формата нет."""
    company = '<ЛицоБезДов ПолнЮЛ="2"><СВЮЛ/></ЛицоБезДов>'
    poa = parse_emchd(_text("direct.xml").replace("</ЛицоБезДов>", "</ЛицоБезДов>" + company, 1), doc_id="d")
    assert poa.co_signers == (Person(),)
    assert not any(gap.startswith("signer.2") for gap in poa.format_gaps)


def test_field_given_twice_inside_one_person_is_still_rejected():
    twice = (
        '<ЛицоБезДов ПолнЮЛ="2"><СвФЛ ИННФЛ="500100732259"><ИННФЛ>500100732259</ИННФЛ>'
        '<СведФЛ><ФИО Фамилия="Второв" Имя="Семён"/></СведФЛ></СвФЛ></ЛицоБезДов>'
    )
    with pytest.raises(EmchdError, match="лицо без доверенности 2"):
        parse_emchd(_text("direct.xml").replace("</ЛицоБезДов>", "</ЛицоБезДов>" + twice, 1), doc_id="d")


def _transfer_from_company(people: str) -> str:
    text = _text("transfer.xml")
    start, end = text.index("<ФЛПерПолн"), text.index("</ФЛПерПолн>") + len("</ФЛПерПолн>")
    org = (
        '<РосОргПерПолн ЕИОУК="0" ЕИОФЛ="1" ЕИОИП="0"><СвРосОрг НаимОрг="ООО «Пример»" ИННЮЛ="7700000016" '
        f'КПП="770001001"/>{people}</РосОргПерПолн>'
    )
    return text[:start] + org + text[end:]


FIRST = (
    '<ЛицоБезДов ПолнЮЛ="2"><СвФЛ ИННФЛ="770000000156" Должность="Директор">'
    '<СведФЛ><ФИО Фамилия="Образцов" Имя="Пётр"/></СведФЛ></СвФЛ></ЛицоБезДов>'
)


def test_transfer_signed_by_two_persons_of_a_company_is_parsed():
    poa = parse_emchd(_transfer_from_company(FIRST + SECOND), doc_id="t")
    assert poa.subdelegator_kind == "legal_entity"
    assert [p.inn for p in poa.signers] == ["770000000156", "500100732259"]


def test_persons_listed_and_signer_in_another_form_are_rejected():
    text = _transfer_from_company(FIRST + SECOND)
    individual = (
        '<ФЛПерПолн ИННФЛ="770000000220"><СведФЛ><ФИО Фамилия="Пробный" Имя="Иван"/></СведФЛ></ФЛПерПолн>'
    )
    with pytest.raises(EmchdError):
        parse_emchd(text.replace("</РосОргПерПолн>", "</РосОргПерПолн>" + individual), doc_id="t")


# --- правила ------------------------------------------------------------------------------------------------


def _add_signer(person: Person, *, position: str | None = "Президент", file: str = "poa-root.xml"):
    def mutate(sc) -> None:
        root = ET.fromstring(sc.files[file])  # noqa: S314
        holder = root.find(HOLDER)
        assert holder is not None
        node = ET.SubElement(holder, "ЛицоБезДов", {"ПолнЮЛ": "2"})
        attrs = {"ИННФЛ": person.inn or "", "СНИЛС": person.snils or ""}
        if position:
            attrs["Должность"] = position
        fl = ET.SubElement(node, "СвФЛ", {k: v for k, v in attrs.items() if v})
        last, first, *rest = (person.full_name or "").split()
        fio = {"Фамилия": last, "Имя": first, **({"Отчество": rest[0]} if rest else {})}
        ET.SubElement(ET.SubElement(fl, "СведФЛ"), "ФИО", fio)
        sc.files[file] = ET.tostring(root, encoding="unicode").encode("utf-8")

    return mutate


def _add_head(person: Person, *, since: str = "2024-05-01"):
    def mutate(sc) -> None:
        egrul = json.loads(sc.files["egrul.json"])
        egrul["executives"].append(
            {
                "full_name": person.full_name,
                "inn": person.inn,
                "position": "Президент",
                "grn": "2247700765432",
                "record_date": since,
                "valid_from": since,
                "valid_to": None,
            }
        )
        sc.files["egrul.json"] = json.dumps(egrul, ensure_ascii=False).encode("utf-8")

    return mutate


def _close_director(until: str):
    def mutate(sc) -> None:
        egrul = json.loads(sc.files["egrul.json"])
        egrul["executives"][0]["valid_to"] = until
        sc.files["egrul.json"] = json.dumps(egrul, ensure_ascii=False).encode("utf-8")

    return mutate


def _both(*mutations):
    def mutate(sc) -> None:
        for one in mutations:
            one(sc)

    return mutate


def _messages(report, rule_id: str) -> list[str]:
    return [f.message for f in report.findings if f.rule_id == rule_id]


def test_baseline_case_is_confirmed():
    report = run("ok_charter_cleared", seed=SEED)
    assert report.verdict.value == "confirmed"
    assert rule_effects(report, "R-EIO-001") == {"supports"}


def test_r_eio_001_other_head_who_did_not_sign_goes_to_review():
    """Был путь к ложному «подтверждено»: два руководителя в ЕГРЮЛ, МЧД подписал один, устав не приложен."""
    report = run("ok_charter_cleared", seed=SEED, mutate=_add_head(WORLD.deputy))
    assert rule_effects(report, "R-EIO-001") == {"supports", "needs_review"}
    assert any("п. 3 ст. 53" in m and WORLD.deputy.label() in m for m in _messages(report, "R-EIO-001"))
    assert report.verdict.value == "needs_review"


def test_r_eio_001_poa_signed_by_every_head_is_confirmed():
    report = run("ok_charter_cleared", seed=SEED, mutate=_both(_add_head(WORLD.deputy), _add_signer(WORLD.deputy)))
    assert rule_effects(report, "R-EIO-001") == {"supports"}
    assert any("подписали руководители" in m for m in _messages(report, "R-EIO-001"))
    assert report.verdict.value == "confirmed"
    assert report.as_dict()["chain"]["documents"][0]["co_signers"] == [WORLD.deputy.full_name]


def test_r_eio_001_second_signer_who_is_not_a_head_goes_to_review():
    report = run("ok_charter_cleared", seed=SEED, mutate=_add_signer(WORLD.stranger))
    assert rule_effects(report, "R-EIO-001") == {"supports", "needs_review"}
    assert any("не значится" in m and WORLD.stranger.label() in m for m in _messages(report, "R-EIO-001"))
    assert report.verdict.value == "needs_review"


def test_r_eio_001_no_signer_is_a_head_refutes():
    report = run("signer_not_eio", seed=SEED, mutate=_add_signer(WORLD.deputy))
    assert rule_effects(report, "R-EIO-001") == {"refutes"}
    message = _messages(report, "R-EIO-001")[0]
    assert WORLD.stranger.label() in message and WORLD.deputy.label() in message
    assert report.verdict.value == "refuted"


def test_r_eio_002_head_who_signed_is_still_in_office():
    """Первый подписант ушёл, второй руководит и сейчас: смены руководителя для этой МЧД нет."""
    mutate = _both(_add_head(WORLD.deputy), _add_signer(WORLD.deputy), _close_director("2026-06-01"))
    report = run("ok_charter_cleared", seed=SEED, mutate=mutate)
    assert rule_effects(report, "R-EIO-002") == set()
    assert rule_effects(report, "R-EIO-001") == {"supports"}


def test_r_fmt_003_snils_is_required_from_every_signer():
    report = run("ok_charter_cleared", seed=SEED, mutate=_add_signer(Person(WORLD.deputy.full_name, WORLD.deputy.inn)))
    assert any("СНИЛС лица без доверенности" in m for m in _messages(report, "R-FMT-003"))
    assert "needs_review" in rule_effects(report, "R-FMT-003")


def test_r_id_001_checksum_of_the_second_signer_is_checked():
    bad = Person(WORLD.deputy.full_name, (WORLD.deputy.inn or "")[:-1] + "0", WORLD.deputy.snils)
    if bad.inn == WORLD.deputy.inn:
        bad = Person(WORLD.deputy.full_name, (WORLD.deputy.inn or "")[:-1] + "1", WORLD.deputy.snils)
    report = run("ok_charter_cleared", seed=SEED, mutate=_add_signer(bad))
    assert any("ИНН подписанта 2" in m for m in _messages(report, "R-ID-001"))
    assert report.verdict.value == "refuted"


def test_r_fmt_001_second_signer_without_position_is_a_gap():
    report = run("ok_charter_cleared", seed=SEED, mutate=_add_signer(WORLD.deputy, position=None))
    assert any("signer.2.position" in m for m in _messages(report, "R-FMT-001"))
    assert report.verdict.value == "insufficient_data"


def test_r_sub_009_edge_signed_by_several_persons_goes_to_review():
    report = run("ok_subdelegation", seed=SEED, mutate=_add_signer(WORLD.stranger, file="poa-child.xml"))
    assert any("подписали несколько лиц" in m for m in _messages(report, "R-SUB-009"))
    assert report.verdict.value != "confirmed"


# --- защита данных и отчёт ----------------------------------------------------------------------------------


def test_second_signer_is_masked_before_the_model():
    scenario = build("ok_charter_cleared", seed=SEED)
    _add_signer(WORLD.stranger)(scenario)
    people = known_people(load_case_inline(scenario.inline_manifest()))
    assert any(p.marker == SIGNER and p.full_name == WORLD.stranger.full_name for p in people)


def test_card_names_every_signer_and_single_signer_report_has_no_new_field():
    single = run("ok_charter_cleared", seed=SEED).as_dict()
    assert "co_signers" not in single["chain"]["documents"][0]
    joint = run("ok_charter_cleared", seed=SEED, mutate=_add_signer(WORLD.stranger)).as_dict()
    page = render(joint)
    assert f"{WORLD.director.full_name}, {WORLD.stranger.full_name}" in page
    assert any(WORLD.stranger.label() in row for row in joint["chain"]["links"])
