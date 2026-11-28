"""Правовой радар — каталог норм со сроками действия, горизонт нормы и переход R-FMT-002 под 315-ФЗ.

Даты взяты из официального опубликования (docs/REGULATORY.md): 315-ФЗ вступает в силу с 01.09.2027 (ст. 2),
приказы Минцифры № 856 и № 857 действуют до 01.03.2028 (п. 3 каждого), № 1001 — с 01.02.2026.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET  # синтетика теста, внешних данных нет

import pytest
from conftest import ROOT, rule_effects

from mandat.cli import main
from mandat.data import load_json
from mandat.eval.synth import build
from mandat.intake.case_loader import load_case_dir, load_case_inline
from mandat.rules.pack import RulePackError, default_pack, validate_pack
from mandat.services.verify import verify_case

_ROOT_SV = "Документ/Довер/СвДов"
CONFIRMED = "14_old_director_charter_cleared"  # демо-кейс, который на стенде даёт «подтверждено»


def _pack(**changes) -> object:
    """Пакет правил с правкой: ``norm_<ID>={...}`` для нормы, ``rule_<ID>={...}`` для параметров правила."""
    payload = copy.deepcopy(load_json("rules.json"))
    for key, patch in changes.items():
        kind, _, target = key.partition("_")
        target = target.replace("_", "-")
        if kind == "norm":
            row = next(n for n in payload["norms"] if n["id"] == target)
            row.update(patch)
        else:
            row = next(r for r in payload["rules"] if r["id"] == target)
            row["params"].update(patch)
    return validate_pack(payload)


def _issued(day: str):
    def mutate(sc) -> None:
        root = ET.fromstring(sc.files["poa-root.xml"])  # noqa: S314
        element = root.find(_ROOT_SV)
        assert element is not None
        element.set("ДатаВыдДовер", day)
        sc.files["poa-root.xml"] = ET.tostring(root, encoding="unicode").encode("utf-8")

    return mutate


def _verify(defect: str = "ok", *, mutate=None, pack=None):
    scenario = build(defect, seed=11)
    if mutate is not None:
        mutate(scenario)
    return verify_case(load_case_inline(scenario.inline_manifest()), profile="demo", pack=pack)


def _messages(report, rule_id: str) -> list[str]:
    return [f.message for f in report.findings if f.rule_id == rule_id]


# --------------------------------------------------------------------------- каталог норм


def test_norm_catalogue_dates_match_rule_parameters():
    pack = default_pack()
    norms = {n.id: n for n in pack.norms}
    assert norms["MINC-857"].in_force_until.isoformat() == "2028-03-01"
    assert norms["MINC-856"].in_force_until.isoformat() == "2028-03-01"
    fmt3 = pack.get("R-FMT-003")
    assert fmt3.param("kpp_snils_from") == norms["MINC-857"].in_force_from.isoformat()
    assert fmt3.param("power_name_from") == norms["MINC-1001"].in_force_from.isoformat()
    assert pack.get("R-FMT-002").param("unified_form_from") == norms["FZ-315-2026"].in_force_from.isoformat()
    assert all(n.url.startswith("https://publication.pravo.gov.ru/document/") for n in pack.norms)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"url": "https://www.consultant.ru/document/cons_doc_LAW_5142/"}, "не официального опубликования"),
        ({"url": "http://publication.pravo.gov.ru/document/0001202110080020"}, "не официального опубликования"),
        ({"in_force_until": "2021-01-01"}, "кончается раньше"),
        ({"in_force_until": "01.03.2028"}, "не дата ISO"),
        ({"rules": ["R-NOPE-001"]}, "нет в пакете"),
        ({"rules": []}, "не названы правила"),
        ({"legal_force": "letter"}, "юридическая сила"),
        ({"title": " "}, "нет названия"),
    ],
)
def test_norm_catalogue_rejects_bad_rows(patch, message):
    with pytest.raises(RulePackError, match=message):
        _pack(norm_MINC_857=patch)


def test_norm_catalogue_rejects_duplicates_and_mute_rules():
    payload = copy.deepcopy(load_json("rules.json"))
    payload["norms"].append(dict(payload["norms"][1]))
    with pytest.raises(RulePackError, match="повторный id нормы"):
        validate_pack(payload)
    payload = copy.deepcopy(load_json("rules.json"))
    payload["norms"][1]["rules"] = ["R-TERM-002"]
    effects = next(r for r in payload["rules"] if r["id"] == "R-TERM-002")["effects"]
    assert "needs_review" not in effects
    with pytest.raises(RulePackError, match="не умеют «на разбор»"):
        validate_pack(payload)


# --------------------------------------------------------------------------- горизонт нормы


def _demo(name: str, pack=None):
    case = load_case_dir(ROOT / "examples" / "cases" / name)
    return case, verify_case(case, profile="demo", pack=pack)


def test_supports_go_to_review_when_the_basis_has_lost_force():
    case, before = _demo(CONFIRMED)
    assert before.verdict.value == "confirmed"
    assert not any("утратило силу" in f.message for f in before.findings)
    on = case.operation.on_date.isoformat()
    _, expired = _demo(CONFIRMED, _pack(norm_MINC_857={"in_force_until": on}))
    assert expired.verdict.value == "needs_review"
    for rule_id in ("R-FMT-002", "R-FMT-003", "R-COV-001"):
        assert "needs_review" in rule_effects(expired, rule_id), rule_id
        assert any("Приказ Минцифры России от 18.08.2021 № 857" in m for m in _messages(expired, rule_id))


def test_two_expired_acts_give_one_finding_per_rule():
    case, _ = _demo(CONFIRMED)
    on = case.operation.on_date.isoformat()
    _, report = _demo(CONFIRMED, _pack(norm_MINC_857={"in_force_until": on}, norm_MINC_856={"in_force_until": on}))
    horizon = [m for m in _messages(report, "R-COV-001") if "утратило силу" in m]
    assert len(horizon) == 1
    assert "№ 857" in horizon[0] and "№ 856" in horizon[0]


def test_refutation_is_not_touched_by_the_horizon():
    case, before = _demo("04_subdelegation_forbidden")
    assert before.verdict.value == "refuted"
    on = case.operation.on_date.isoformat()
    _, report = _demo("04_subdelegation_forbidden", _pack(norm_MINC_857={"in_force_until": on}))
    assert report.verdict.value == "refuted"


# --------------------------------------------------------------------------- 315-ФЗ и форма МЧД


def test_r_fmt_002_form_by_issue_date_after_315_fz():
    """С 01.09.2027 доверенность — по единой форме ЕПГУ или дополнительной форме (ч. 1 ст. 17.5 63-ФЗ)."""
    old = _verify(mutate=_issued("2027-08-31"))
    assert rule_effects(old, "R-FMT-002") == {"supports"}
    new = _verify(mutate=_issued("2027-09-01"))
    assert rule_effects(new, "R-FMT-002") == {"needs_review"}
    assert any("315-ФЗ" in m and "в списке банка не значится" in m for m in _messages(new, "R-FMT-002"))
    accepted = _verify(mutate=_issued("2027-09-01"), pack=_pack(rule_R_FMT_002={"forms_after_unified": ["EMCHD_1"]}))
    assert rule_effects(accepted, "R-FMT-002") == {"supports"}


# --------------------------------------------------------------------------- календарь норм


def test_law_calendar_lists_coming_changes_and_flags_a_stale_pack(capsys):
    assert main(["law-calendar", "--on", "2026-11-17", "--days", "365"]) == 0
    report = json.loads(capsys.readouterr().out)
    events = {n["id"]: n["events"] for n in report["norms"]}
    assert events["FZ-315-2026"] == [{"event": "вступает в силу", "date": "2027-09-01"}]
    assert report["stale"] == []
    assert main(["law-calendar", "--on", "2027-12-01", "--days", "120"]) == 0
    soon = json.loads(capsys.readouterr().out)
    assert {"event": "утрачивает силу", "date": "2028-03-01"} in {n["id"]: n for n in soon["norms"]}["MINC-857"][
        "events"
    ]
    assert main(["law-calendar", "--on", "2028-03-01"]) == 1
    stale = json.loads(capsys.readouterr().out)
    assert set(stale["stale"]) == {"MINC-857", "MINC-1001", "MINC-856"}
    assert main(["law-calendar", "--on", "01.03.2028"]) == 2


def test_law_calendar_without_on_takes_the_moscow_day(capsys, monkeypatch):
    """29.02.2028 21:30 UTC — уже 01.03.2028 по Москве: приказы Минцифры утратили силу, хотя по UTC ещё 29-е."""
    from datetime import UTC, datetime

    import mandat.cli

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2028, 2, 29, 21, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(mandat.cli, "datetime", Frozen)
    assert main(["law-calendar"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["on"] == "2028-03-01"
    assert set(report["stale"]) == {"MINC-857", "MINC-1001", "MINC-856"}


# --------------------------------------------------------------------------- 253-ФЗ: реструктуризация юрлица


def test_org_debt_restructuring_is_explained_after_the_bankruptcy_reform():
    """С 27.07.2027 (253-ФЗ) реструктуризация долгов применяется и к юрлицу: вердикт тот же, пояснение верное."""
    import dataclasses
    from datetime import date

    from test_mutants_checks import _bnk_ctx

    from mandat.ports import BankruptcyStatus as B
    from mandat.rules import checks

    ctx = _bnk_ctx(B.DEBT_RESTRUCTURING, B.NONE)
    (before,) = checks._bankruptcy(ctx)
    assert before.effect.value == "needs_review"
    assert "не подходит к этому лицу" in before.message
    operation = dataclasses.replace(ctx.case.operation, on_date=date(2027, 7, 27))
    ctx.case = dataclasses.replace(ctx.case, operation=operation)
    (after,) = checks._bankruptcy(ctx)
    assert after.effect.value == "needs_review"
    assert "реструктуризация долгов юрлица (127-ФЗ в ред. 253-ФЗ)" in after.message
    assert default_pack().get("R-EIO-004").param("org_debt_restructuring_from") == "2027-07-27"
