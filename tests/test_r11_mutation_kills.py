"""R5: тесты, добивающие выживших мутантов mutmut в rules/checks.py (см. docs/testing/MUTATION.md)."""

from __future__ import annotations

from conftest import ROOT

from mandat.domain.graph import build_chain
from mandat.intake.case_loader import load_case_dir
from mandat.ports import FixtureRegistry, NotConnectedSignature, Ports, RegistryObservation, RegistryStatus
from mandat.rules import checks
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import default_ports


def _ctx(registry=None, name="01_ok"):
    case = load_case_dir(ROOT / "examples" / "cases" / name)
    base = default_ports(case)
    ports = Ports(registry=registry or base.registry, signature=NotConnectedSignature(), bankruptcy=base.bankruptcy)
    pack = default_pack()
    chain = build_chain(case, max_links=int(pack.get("R-SUB-008").param("max_chain_links")))
    return checks.RuleContext(case=case, chain=chain, pack=pack, catalog=default_catalog(), ports=ports)


def test_accepts_query_needs_both_inn_parameters():
    assert checks._accepts_query(lambda number, principal_inn=None, representative_inn=None: None)
    assert not checks._accepts_query(lambda number: None)
    assert not checks._accepts_query(lambda number, principal_inn=None: None)
    assert not checks._accepts_query(lambda number, representative_inn=None: None)
    assert not checks._accepts_query(1)  # не вызываемое — сигнатуры нет


class _Counting:
    name, is_stub = "counting", True

    def __init__(self, base):
        self.base, self.calls = base, []

    def observe(self, number, principal_inn=None, representative_inn=None):
        self.calls.append((number, principal_inn, representative_inn))
        return self.base.observe(number)


def test_registry_is_cached_per_normalized_number_and_gets_query():
    ctx = _ctx()
    port = _Counting(FixtureRegistry(ctx.case.registry))
    ctx = _ctx(port)
    number = ctx.chain.links[0].number
    first = ctx.registry(number)
    assert ctx.registry(number) is first
    assert len(port.calls) == 1
    assert port.calls[0] == (number, ctx.case.organization.inn, ctx.case.requester.inn)


def test_registry_without_number_uses_empty_key():
    port = _Counting(FixtureRegistry({}))
    ctx = _ctx(port)
    ctx.registry(None)
    ctx.registry("")
    assert port.calls == [("", None, None)]


def test_status_only_port_is_wrapped_with_original_number():
    class StatusOnly:
        name, is_stub = "status-only", True

        def status(self, number):
            return RegistryStatus.ACTIVE if number else RegistryStatus.UNAVAILABLE

    ctx = _ctx(StatusOnly())
    number = ctx.chain.links[0].number
    obs = ctx.registry(number)
    assert isinstance(obs, RegistryObservation)
    assert obs.number == number
    assert obs.status == RegistryStatus.ACTIVE
    empty = _ctx(StatusOnly()).registry(None)
    assert empty.number == ""
    assert empty.status == RegistryStatus.UNAVAILABLE


def test_registry_query_for_unknown_number_has_exact_keys():
    assert _ctx().registry_query("НЕТ-ТАКОЙ") == {"principal_inn": None, "representative_inn": None}


def test_registry_query_prefers_requester_over_first_representative():
    ctx = _ctx()
    link = ctx.chain.links[0]
    q = ctx.registry_query(link.number)
    assert q["principal_inn"] == link.principal.inn
    assert q["representative_inn"] == ctx.case.requester.inn
    assert set(q) == {"principal_inn", "representative_inn"}


def test_input_sha_finds_exact_document():
    ctx = _ctx()
    rows = ctx.case.inputs
    assert len(rows) >= 2
    for row in rows:
        assert checks._input_sha(ctx, row["doc_id"]) == row["sha256"]
    assert checks._input_sha(ctx, "нет-такого") == "unavailable"


# --- R-EIO-002: руководитель сам проводит операцию вскоре после назначения (_fresh_director) ---

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402
from test_regressions import doc, manifest  # noqa: E402

from mandat.intake.case_loader import load_case_inline  # noqa: E402
from mandat.services.verify import verify_case  # noqa: E402

ON = date(2026, 10, 20)


def _director(appointed: date, *, predecessor: date | None = None, same_person: bool = False, code="PAYMENT_SIGN"):
    m = manifest("ok_director")
    m["operation"]["code"] = code
    execs = doc(m, "egrul")["content"]["executives"]
    me = execs[0]
    me.update(record_date=appointed.isoformat(), valid_from=appointed.isoformat(), grn="2267700000001")
    if predecessor is not None:
        other = dict(me, grn="2207700000002", record_date=predecessor.isoformat(), valid_from=predecessor.isoformat())
        other["valid_to"] = appointed.isoformat()
        if not same_person:
            other.update(full_name="Сидоров Иван Петрович", inn="500100732259")
        execs.insert(0, other)
    return [f for f in verify_case(load_case_inline(m)).findings if f.rule_id == "R-EIO-002"]


def test_fresh_first_director_without_predecessor_is_info():
    (f,) = _director(ON - timedelta(days=5))
    assert f.effect.value == "info"
    assert "в должности 5 дн." in f.message and "предшественника в ЕГРЮЛ нет" in f.message
    assert f.refs


def test_fresh_director_with_predecessor_needs_review_with_both_refs():
    (f,) = _director(ON - timedelta(days=3), predecessor=date(2024, 5, 1))
    assert f.effect.value == "needs_review"
    assert "назначен 3 дн. назад (ГРН 2267700000001)" in f.message
    assert "не более 30 дн." in f.message
    assert "приоритет P1" not in f.message  # подпись платежа — не операция высокого риска
    assert len(f.refs) == 2


@pytest.mark.parametrize("code", ["USER_ADMIN", "REQUISITES_CHANGE"])
def test_fresh_director_high_risk_operation_gets_p1_tail(code):
    (f,) = _director(ON - timedelta(days=3), predecessor=date(2024, 5, 1), code=code)
    assert f.message.endswith("; операция высокого риска — приоритет P1")


def test_fresh_window_boundary_is_inclusive():
    assert _director(ON - timedelta(days=30), predecessor=date(2024, 5, 1))[0].effect.value == "needs_review"
    assert not [f for f in _director(ON - timedelta(days=31), predecessor=date(2024, 5, 1)) if f.effect.value != "info"]


def test_predecessor_starting_same_day_counts_and_same_person_does_not():
    day = ON - timedelta(days=3)
    assert _director(day, predecessor=day)[0].effect.value == "needs_review"
    assert _director(day, predecessor=date(2024, 5, 1), same_person=True)[0].effect.value == "info"


def test_rule_decorator_registers_once(monkeypatch):
    monkeypatch.setattr(checks, "RULES", dict(checks.RULES))

    def probe(ctx):
        return []

    assert checks.rule("R-TEST-999")(probe) is probe
    assert checks.RULES["R-TEST-999"] is probe
    with pytest.raises(ValueError, match="повторная регистрация R-TEST-999"):
        checks.rule("R-TEST-999")(probe)


@pytest.mark.parametrize("profile", ["demo", "strict"])
def test_red_team_stranger_cannot_ride_on_first_representative_registry_query(profile):
    """Чужой заявитель: реестр спрошен по ИНН первого представителя, но R-ID-005 не даёт «подтверждено»."""
    from test_regressions import verdict

    m = manifest("ok")
    m["requester"] = {"full_name": "Сидоров Иван Петрович", "inn": "500100732259", "snils": "112-233-445 95"}
    assert verdict(m, profile) != "confirmed"


# --- часы, наименования, R-FMT-001, emit ---


def test_from_future_tolerates_five_minutes_and_treats_naive_as_utc():
    from datetime import UTC, datetime
    from datetime import timezone as tz

    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert not checks._from_future(None, now)
    assert not checks._from_future(now, None)
    assert not checks._from_future(now + timedelta(minutes=5), now)
    assert checks._from_future(now + timedelta(minutes=5, seconds=1), now)
    assert not checks._from_future(now - timedelta(minutes=6), now)
    naive = datetime(2026, 10, 15, 12, 6)  # без зоны — UTC
    assert checks._from_future(naive, now)
    msk = datetime(2026, 10, 15, 15, 6, tzinfo=tz(timedelta(hours=3)))  # 12:06 UTC
    assert checks._from_future(msk, now)
    assert not checks._from_future(datetime(2026, 10, 15, 15, 4, tzinfo=tz(timedelta(hours=3))), now)


def test_normalize_org_name_folds_yo_quotes_and_forms():
    assert checks.normalize_org_name(None) == ""
    assert checks.normalize_org_name("ООО «Ёлка»") == "общество с ограниченной ответственностью елка"
    assert checks.normalize_org_name('АО "Ромашка"') == checks.normalize_org_name("акционерное общество Ромашка")


def test_emit_carries_rule_spec_and_hides_next_step_on_quiet_effects():
    ctx = _ctx()
    spec = ctx.pack.get("R-TERM-002")
    loud = ctx.emit("R-TERM-002", checks.E.REFUTES, "x", [])
    assert (loud.norm_ref, loud.rule_status, loud.check) == (spec.norm_ref, spec.status, spec.check)
    assert loud.next_step == spec.next_step and loud.unit == spec.unit
    assert loud.refs == (ctx.case_ref,)
    for effect in (checks.E.SUPPORTS, checks.E.INFO):
        quiet = ctx.emit("R-TERM-002", effect, "x", [])
        assert quiet.next_step is None and quiet.unit is None


def test_format_supports_messages_and_refs():
    import dataclasses

    ctx = _ctx()
    out = checks._format_supports(ctx)
    assert [f.rule_id for f in out] == ["R-FMT-001"] * len(ctx.case.poas)
    assert all(f.effect.value == "supports" and "обязательные поля на месте" in f.message for f in out)
    assert out[0].refs == (ctx.case.poas[0].ref("number"),)

    empty = _ctx()
    empty.case = dataclasses.replace(empty.case, poas=(), paper_poas=())
    (f,) = checks._format_supports(empty)
    assert (f.rule_id, f.effect.value) == ("R-FMT-001", "not_verified")
    assert f.message == "В пакете нет документа, подтверждающего полномочия"


def test_format_supports_eio_direct_points_to_egrul_document():
    m = manifest("ok_director")
    case = load_case_inline(m)
    (f,) = [x for x in verify_case(case).findings if x.rule_id == "R-FMT-001"]
    assert f.message == "Сведения ЕГРЮЛ приняты"
    assert f.refs[0].doc_id == case.egrul.doc_id and f.refs[0].locator == "$"
