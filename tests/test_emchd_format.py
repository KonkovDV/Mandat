"""Разбор МЧД в форме XSD EMCHD_1: пространство имён, ветвь передоверия, перечни значений.

Файлы ``tests/data/emchd/*.xml`` синтетические: реквизиты вымышлены, контрольные числа рассчитаны.
Оба файла проходят XSD ON_EMCHD_1_928_00_01_01_01; сама схема в проект не входит, проверка по ней
описана в docs/audit/AUDIT.md. Здесь проверяется, что разбор читает документы такой формы.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mandat.intake.emchd import EmchdError, build_emchd_xml, parse_emchd

DATA = Path(__file__).parent / "data" / "emchd"
NS = 'xmlns="urn://x-artefacts/EMCHD_1"'


def _text(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def test_document_in_the_official_namespace_is_parsed():
    poa = parse_emchd(_text("direct.xml"), doc_id="direct")
    assert poa.format_version == "EMCHD_1"
    assert poa.number == "00000000-0000-4000-8000-0000000000a1"
    assert poa.principal.inn == "7700000016"
    assert poa.signer.position == "Генеральный директор"
    assert poa.joint is False
    assert [a.mnemonic for a in poa.authorities] == ["TEST_CODE"]
    assert poa.representative_kinds == ("individual",)
    assert poa.parent_number is None
    assert poa.format_gaps == () and poa.policy_gaps == ()


def test_document_in_a_foreign_namespace_is_rejected():
    text = _text("direct.xml").replace(NS, 'xmlns="urn://example/other"')
    with pytest.raises(EmchdError, match="пространстве имён"):
        parse_emchd(text, doc_id="x")


def test_elements_of_a_foreign_namespace_are_not_read():
    text = _text("direct.xml").replace("<СвПолн ", '<СвПолн xmlns="urn://example/other" ')
    poa = parse_emchd(text, doc_id="x")
    assert poa.authorities == ()
    assert "joint" not in poa.refs


def test_subdelegation_branch_is_parsed():
    poa = parse_emchd(_text("transfer.xml"), doc_id="transfer")
    assert poa.number == "00000000-0000-4000-8000-0000000000b2"
    assert poa.original_number == "00000000-0000-4000-8000-0000000000a1"
    # НомДоверN не указан: передоверие первичной доверенности, родитель — она же.
    assert poa.parent_number == poa.original_number
    assert poa.principal.inn == "7700000016"
    assert poa.signer.inn == "770000000220"
    assert poa.signer.full_name == "Пробный Иван"
    assert poa.subdelegator_kind == "individual"
    assert [r.inn for r in poa.representatives] == ["770000000300"]
    assert poa.format_gaps == ()
    assert poa.format_questions == ("signer.position",)


def test_explicit_parent_number_wins_over_the_original():
    text = _text("transfer.xml").replace(
        'НомДоверПерв="00000000-0000-4000-8000-0000000000a1"',
        'НомДоверПерв="00000000-0000-4000-8000-0000000000a1" НомДоверN="00000000-0000-4000-8000-0000000000c3"',
    )
    assert parse_emchd(text, doc_id="t").parent_number == "00000000-0000-4000-8000-0000000000c3"


def test_both_branches_in_one_document_are_rejected():
    direct = _text("direct.xml")
    body = direct[direct.index("<Довер>") : direct.index("</Довер>") + len("</Довер>")]
    text = _text("transfer.xml").replace("</Передов>", "</Передов>" + body)
    with pytest.raises(EmchdError):
        parse_emchd(text, doc_id="t")


@pytest.mark.parametrize(
    ("old", "new", "gap"),
    [
        ('ВидДовер="1"', 'ВидДовер="7"', "kind.value"),
        ('ПрПередов="2"', 'ПрПередов="9"', "subdelegation.value"),
        ('ТипДоверит="1"', 'ТипДоверит="5"', "principal_type.value"),
        (' ТипДоверит="1"', "", "principal_type"),
        ('ТипПред="3"', 'ТипПред="6"', "representative_type.1.value"),
        (' ТипПред="3"', "", "representative_type.1"),
        ('ПрСовмПолн="1"', 'ПрСовмПолн="3"', "joint.value"),
        ('ПрСовмПолн="1"', 'ПрСовмПолн="1" ПрУтрПолн="0"', "loses_authority.value"),
        ("<СведСист>", '<Безотзыв ПрПерБезДов="4" УслОтзыва="1"/><СведСист>', "irrevocable_subdelegation.value"),
    ],
)
def test_values_outside_the_schema_lists_are_format_gaps(old, new, gap):
    text = _text("direct.xml")
    assert old in text
    poa = parse_emchd(text.replace(old, new, 1), doc_id="x")
    assert gap in poa.format_gaps


@pytest.mark.parametrize(
    ("old", "new", "gap"),
    [
        ('ТипПерПолн="3"', 'ТипПерПолн="0"', "subdelegator_type.value"),
        (' ТипПерПолн="3"', "", "subdelegator_type"),
        ('ТипПред="3"', 'ТипПред="9"', "representative_type.1.value"),
    ],
)
def test_subdelegation_branch_values_are_checked(old, new, gap):
    poa = parse_emchd(_text("transfer.xml").replace(old, new, 1), doc_id="t")
    assert gap in poa.format_gaps


def test_organization_passing_authority_is_read_from_the_document():
    text = _text("transfer.xml")
    start, end = text.index("<ФЛПерПолн"), text.index("</ФЛПерПолн>") + len("</ФЛПерПолн>")
    org = (
        '<РосОргПерПолн ЕИОУК="0" ЕИОФЛ="1" ЕИОИП="0"><СвРосОрг НаимОрг="ООО «Пример»" ИННЮЛ="7700000016" '
        'КПП="770001001"/><ЛицоБезДов ПолнЮЛ="1"><СвФЛ ИННФЛ="770000000156" Должность="Директор">'
        '<СведФЛ><ФИО Фамилия="Образцов" Имя="Пётр"/></СведФЛ></СвФЛ></ЛицоБезДов></РосОргПерПолн>'
    )
    poa = parse_emchd(text[:start] + org + text[end:], doc_id="t")
    assert poa.subdelegator_kind == "legal_entity"
    assert poa.signer.inn == "770000000156"
    assert poa.signer.position == "Директор"
    assert poa.format_questions == ()


def test_signer_present_in_two_forms_is_rejected():
    text = _text("transfer.xml")
    extra = (
        '<РосОргПерПолн ЕИОУК="0" ЕИОФЛ="1" ЕИОИП="0"><ЛицоБезДов><СвФЛ ИННФЛ="770000000156">'
        '<СведФЛ><ФИО Фамилия="Образцов" Имя="Пётр"/></СведФЛ></СвФЛ></ЛицоБезДов></РосОргПерПолн>'
    )
    with pytest.raises(EmchdError):
        parse_emchd(text.replace("</ФЛПерПолн>", "</ФЛПерПолн>" + extra), doc_id="t")


def test_subdelegator_kind_conflicting_with_the_parent_is_unknown():
    from mandat.rules.checks import _subdelegator_kind

    parent = parse_emchd(_text("direct.xml"), doc_id="p")
    child = parse_emchd(_text("transfer.xml"), doc_id="c")
    assert _subdelegator_kind(parent, child) == "individual"
    org_parent = parse_emchd(_text("direct.xml").replace("СведФизЛ", "СведОрг"), doc_id="p2")
    assert org_parent.representative_kinds == ("legal_entity",)
    assert _subdelegator_kind(org_parent, child) == "unknown"


def test_writer_output_is_not_claimed_to_follow_the_schema():
    """Синтетический писатель пишет без пространства имён: разбор принимает и такую форму."""
    from datetime import date

    from mandat.domain.model import Organization, Person

    xml = build_emchd_xml(
        number="n-1",
        issue_date=date(2026, 10, 1),
        valid_until=None,
        principal=Organization(inn="7700000016", ogrn="1027700000019", name="ООО «Пример»"),
        signer=Person(full_name="Образцов Пётр", inn="770000000156", position="Директор"),
        representatives=[Person(full_name="Пробный Иван", inn="770000000220")],
    )
    assert "urn://x-artefacts/EMCHD_1" not in xml
    assert 'ПрСовмПолн="1"' in xml and "<СвПолн" in xml
    poa = parse_emchd(xml, doc_id="w")
    assert poa.joint is False


def _mutant(old: str, new: str):
    """Копия модуля разбора с одной заменой в исходном тексте."""
    import importlib.util

    from mandat.intake import emchd

    source = Path(emchd.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    spec = importlib.util.spec_from_loader("emchd_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source.replace(old, new), emchd.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("    if root.tag != root_tag:\n", "    if False:\n"),
        ('    if root.tag == "{" + spec["namespace"] + "}" + root_tag:\n', '    if root.tag.startswith("{"):\n'),
    ],
    ids=["no-rejection", "any-namespace"],
)
def test_foreign_namespace_check_catches_its_own_removal(old, new):
    """Контроль мутацией: без отказа по чужому пространству имён проверка отказа перестаёт проходить."""
    mutant = _mutant(old, new)
    if new.startswith("    if root.tag.startswith"):
        mutant._strip_namespace = lambda root, _ns: [
            setattr(el, "tag", el.tag.split("}", 1)[-1]) for el in root.iter() if isinstance(el.tag, str)
        ]
    text = _text("direct.xml").replace(NS, 'xmlns="urn://example/other"')
    try:
        mutant.parse_emchd(text, doc_id="x")
    except mutant.EmchdError as exc:
        assert "пространстве имён" not in str(exc)
