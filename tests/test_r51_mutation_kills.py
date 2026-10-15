"""R5.1: добиваем логических выживших mutmut в rules/checks.py (не текст сообщений).

См. docs/testing/MUTATION.md. Текстовые мутанты _charter_info / _org_status_egrul / _format_supports
намеренно не трогаем.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest
from test_r11_mutation_kills import _bnk_ctx, _ctx
from test_regressions import doc, manifest

from mandat.domain.graph import AuthorityChain
from mandat.domain.model import Person
from mandat.intake.case_loader import load_case_inline
from mandat.ports import BankruptcyStatus as B
from mandat.rules import checks
from mandat.rules.pack import default_pack
from mandat.services.verify import verify_case

ON = date(2026, 10, 20)


def test_emit_copies_non_draft_rule_status():
    """Мутант emit без rule_status= оставлял default «draft» — ловится только на non-draft."""
    ctx = _ctx()
    approved = dataclasses.replace(ctx.pack.get("R-TERM-002"), status="approved")
    ctx.pack.rules["R-TERM-002"] = approved
    f = ctx.emit("R-TERM-002", checks.E.REFUTES, "x", [])
    assert f.rule_status == "approved"


def test_registry_cache_key_for_missing_number_is_empty_string():
    ctx = _ctx()
    ctx.registry(None)
    assert "" in ctx._registry
    assert "XXXX" not in ctx._registry


def test_registry_query_matches_empty_number_link_only_with_empty_key():
    ctx = _ctx()
    link = ctx.chain.links[0]
    empty = dataclasses.replace(link, number="")
    ctx.chain = dataclasses.replace(ctx.chain, links=(empty, *ctx.chain.links[1:]))
    q = ctx.registry_query("")
    assert q["principal_inn"] == empty.principal.inn
    assert q["representative_inn"] is not None


def _two_rep_ctx(*, requester_second: bool = True):
    """МЧД с двумя представителями: первый — чужой ИНН, второй — заявитель кейса."""
    ctx = _ctx("01_ok")
    link = ctx.chain.links[0]
    other = Person(full_name="Сидоров Иван Петрович", inn="500100732259", snils="112-233-445 95")
    me = ctx.case.requester
    reps = (other, me) if requester_second else (me, other)
    link = dataclasses.replace(link, representatives=reps)
    ctx.chain = dataclasses.replace(ctx.chain, links=(link, *ctx.chain.links[1:]))
    return ctx, link, other, me


def test_registry_query_prefers_requester_when_not_first_representative():
    ctx, link, other, me = _two_rep_ctx(requester_second=True)
    q = ctx.registry_query(link.number)
    assert q["representative_inn"] == me.inn
    assert q["representative_inn"] != other.inn
    assert q["principal_inn"] == link.principal.inn


def test_registry_query_without_any_representative_inn_returns_none():
    ctx = _ctx()
    link = ctx.chain.links[0]
    bare = dataclasses.replace(link, representatives=(Person(full_name="Без ИНН", inn=None, snils=None),))
    ctx.chain = dataclasses.replace(ctx.chain, links=(bare,))
    q = ctx.registry_query(bare.number)
    assert q == {"principal_inn": bare.principal.inn, "representative_inn": None}


def test_poas_in_scope_returns_case_poas_when_chain_kind_is_none():
    ctx = _ctx()
    ctx.chain = AuthorityChain(kind="none", links=(), notes=("нет цепочки",))
    assert checks._poas_in_scope(ctx) == ctx.case.poas
    ctx.chain = AuthorityChain(kind="paper", links=(), notes=("бумага",))
    assert checks._poas_in_scope(ctx) == ()


def test_egrul_ref_default_locator_is_dollar():
    ctx = _ctx(name="02_director_without_poa")
    assert ctx.case.egrul is not None
    ref = checks._egrul_ref(ctx)
    assert ref.locator == "$"
    assert ref.doc_id == ctx.case.egrul.doc_id


def test_fresh_director_silent_when_egrul_missing_but_eio_record_present():
    ctx = _ctx(name="02_director_without_poa")
    assert ctx.chain.eio_record is not None
    ctx.case = dataclasses.replace(ctx.case, egrul=None)
    assert checks._fresh_director(ctx) == []


def test_fresh_director_uses_valid_from_when_record_date_absent():
    m = manifest("ok_director")
    m["operation"]["code"] = "PAYMENT_SIGN"
    execs = doc(m, "egrul")["content"]["executives"]
    appointed = (ON - timedelta(days=3)).isoformat()
    me = execs[0]
    me.update(valid_from=appointed, record_date=None, grn="2267700000001")
    other = dict(
        me,
        full_name="Сидоров Иван Петрович",
        inn="500100732259",
        grn="2207700000002",
        valid_from="2024-05-01",
        record_date=None,
        valid_to=appointed,
    )
    execs.insert(0, other)
    findings = [f for f in verify_case(load_case_inline(m)).findings if f.rule_id == "R-EIO-002"]
    assert findings and findings[0].effect.value == "needs_review"


def test_fresh_director_predecessor_with_only_record_date():
    """Мутанты or→and на (valid_from or record_date or date.min) ломают предшественника без valid_from."""
    m = manifest("ok_director")
    execs = doc(m, "egrul")["content"]["executives"]
    appointed = (ON - timedelta(days=3)).isoformat()
    me = execs[0]
    me.update(valid_from=appointed, record_date=appointed, grn="2267700000001")
    other = dict(
        me,
        full_name="Сидоров Иван Петрович",
        inn="500100732259",
        grn="2207700000002",
        valid_from=None,
        record_date="2024-05-01",
        valid_to=appointed,
    )
    execs.insert(0, other)
    (f,) = [x for x in verify_case(load_case_inline(m)).findings if x.rule_id == "R-EIO-002"]
    assert f.effect.value == "needs_review"


def test_fresh_director_falls_back_to_egrul_ref_when_record_has_no_ref():
    ctx = _ctx(name="02_director_without_poa")
    rec = ctx.chain.eio_record
    assert rec is not None
    # убрать ref у записи и сдвинуть дату в окно свежести
    bare = dataclasses.replace(rec, ref=None, valid_from=ON - timedelta(days=2), record_date=ON - timedelta(days=2))
    ctx.chain = dataclasses.replace(ctx.chain, eio_record=bare)
    # нужен предшественник в egrul
    eg = ctx.case.egrul
    assert eg is not None
    pred = dataclasses.replace(
        eg.executives[0],
        person=Person(full_name="Сидоров Иван Петрович", inn="500100732259"),
        grn="2207700000002",
        valid_from=date(2024, 5, 1),
        record_date=date(2024, 5, 1),
        ref=None,
    )
    ctx.case = dataclasses.replace(ctx.case, egrul=dataclasses.replace(eg, executives=(pred, bare)))
    out = checks._fresh_director(ctx)
    assert out and out[0].refs[0].locator == "$"


def test_bankruptcy_skips_principal_without_inn_and_still_checks_representative():
    ctx = _bnk_ctx(B.NONE, B.PROPERTY_SALE)
    ctx.case = dataclasses.replace(
        ctx.case, organization=dataclasses.replace(ctx.case.organization, inn=None)
    )
    (f,) = checks._bankruptcy(ctx)
    assert f.message.startswith("Представитель")


def test_run_rules_skips_retired_and_continues():
    import dataclasses as dc

    ctx = _ctx()
    pack = default_pack()
    # первый в порядке пакета — retired; второй должен выполниться
    first_id = next(iter(pack.rules))
    pack.rules[first_id] = dc.replace(pack.get(first_id), status="retired")
    ctx.pack = pack
    # сломать retired-правило: если break вместо continue — дальше не пойдём и findings оскудеют
    original = checks.RULES[first_id]

    def boom(_ctx):
        raise AssertionError("retired rule must not run")

    checks.RULES[first_id] = boom
    try:
        findings = checks.run_rules(ctx)
    finally:
        checks.RULES[first_id] = original
    assert findings  # остальные правила отработали
    assert not any(f.rule_id == first_id for f in findings)


def test_run_rules_charter_max_chars_is_inclusive(monkeypatch):
    ctx = _ctx()
    pack = default_pack()
    charter = ctx.case.charter
    if charter is None:
        pytest.skip("нет устава в кейсе")
    limit = len(charter.text)
    spec = pack.get("R-CHR-002")
    pack.rules["R-CHR-002"] = dataclasses.replace(
        spec, params={**spec.params, "charter_max_chars": limit}
    )
    ctx.pack = pack
    calls: list[int] = []

    def spy(doc):
        calls.append(len(doc.text))
        return []

    monkeypatch.setattr(checks, "extract_charter_facts", spy)
    checks.run_rules(ctx)
    assert calls == [limit]  # <= max: вызвали; мутант < max — не вызвал бы

    calls.clear()
    pack.rules["R-CHR-002"] = dataclasses.replace(
        spec, params={**spec.params, "charter_max_chars": limit - 1}
    )
    ctx.pack = pack
    checks.run_rules(ctx)
    assert calls == []


def test_run_rules_records_exception_type_name():
    ctx = _ctx()
    rid = "R-TERM-002"
    original = checks.RULES[rid]

    def boom(_ctx):
        raise RuntimeError("probe")

    checks.RULES[rid] = boom
    try:
        findings = checks.run_rules(ctx)
    finally:
        checks.RULES[rid] = original
    hit = [f for f in findings if f.rule_id == rid and "RuntimeError" in f.message]
    assert hit
    assert "NoneType" not in hit[0].message
