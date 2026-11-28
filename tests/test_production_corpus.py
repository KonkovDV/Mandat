"""Поток кейсов по профилю (data/load_profiles.json): состав, детерминизм, условия и проверка профиля.

Профиль задаёт смесь видов и пограничные условия банка: общий клиент с многими представителями, повторная
проверка той же МЧД, момент запроса к реестру с часовым поясом, последний день срока. Каждая доля — допущение
или ссылка на источник. Ожидаемый вердикт выводит генератор, а не движок; здесь сверяется, что без условий
он совпадает с движком для каждого вида, и что условия меняют его только там, где меняют нормы.
"""

from __future__ import annotations

import copy
import json

import pytest

from mandat.data import load_json
from mandat.eval import run, synth
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case

PROFILES = synth.load_mixes()


def _verdict(scenario: synth.Scenario) -> str:
    return verify_case(load_case_inline(scenario.inline_manifest())).verdict.value


def _without_id(scenario: synth.Scenario) -> tuple[dict, dict]:
    return {k: v for k, v in scenario.manifest.items() if k != "case_id"}, scenario.files


def test_shipped_profiles_are_valid_and_cover_every_kind():
    assert synth.validate_mixes(load_json("load_profiles.json")) == []
    kinds = [entry["kind"] for entry in PROFILES["vtb_prod"]["mix"]]
    assert sorted(kinds) == sorted(synth.KINDS)
    assert set(synth.DEFECTS) <= set(synth.KINDS)


def test_vtb_prod_is_mostly_clean_with_a_long_tail():
    mix = PROFILES["vtb_prod"]["mix"]
    clean = sum(e["weight"] for e in mix if e["group"] == "чистый пакет")
    assert clean > 0.75
    assert max(e["weight"] for e in mix if e["group"] != "чистый пакет") <= 0.03
    assert all(e["basis"].startswith("допущение") for e in mix)


def test_every_kind_expected_verdict_matches_the_engine():
    for kind, expected in synth.KINDS.items():
        scenario = synth.make_case({"kind": kind, "seed": 11})
        assert scenario.expected == expected
        assert _verdict(scenario) == expected, kind


def test_stream_is_deterministic_sized_and_unique():
    first = list(synth.production_recipes(400, profiles=PROFILES))
    assert first == list(synth.production_recipes(400, profiles=PROFILES))
    assert len(first) == 400
    assert len({r["case_id"] for r in first}) == 400
    assert first != list(synth.production_recipes(400, seed=1, profiles=PROFILES))


def test_repeat_check_is_the_same_package_later_in_the_stream():
    recipes = list(synth.production_recipes(600, profiles=PROFILES))
    position = {r["case_id"]: i for i, r in enumerate(recipes)}
    repeats = [r for r in recipes if r.get("repeat_of")]
    assert repeats
    for repeat in repeats[:20]:
        assert position[repeat["repeat_of"]] < position[repeat["case_id"]]
        original = recipes[position[repeat["repeat_of"]]]
        assert _without_id(synth.make_case(repeat)) == _without_id(synth.make_case(original))


def test_shared_client_keeps_organization_and_director_but_not_representative():
    one = synth.make_case({"kind": "ok_charter_cleared", "seed": 5, "org_seed": 77})
    two = synth.make_case({"kind": "ok", "seed": 6, "org_seed": 77})
    alone = synth.make_case({"kind": "ok_charter_cleared", "seed": 5})
    assert one.manifest["organization"] == two.manifest["organization"] != alone.manifest["organization"]
    director = [json.loads(s.files["egrul.json"])["executives"][0]["inn"] for s in (one, two)]
    assert director[0] == director[1]
    assert one.manifest["requester"]["inn"] != two.manifest["requester"]["inn"]
    assert one.manifest["requester"] == alone.manifest["requester"]
    assert _verdict(one) == one.expected == "confirmed"


def test_build_without_org_seed_is_unchanged():
    for kind in ("ok", "ok_subdelegation", "director_changed", "fresh_director_admin"):
        plain, explicit = synth.build(kind, 9), synth.build(kind, 9, org_seed=None)
        assert (plain.manifest, plain.files) == (explicit.manifest, explicit.files)


def test_registry_moment_age_is_counted_in_moscow_days():
    """Тот же момент в другом поясе — тот же снимок: возраст и ожидаемый вердикт от записи не зависят."""
    assert synth.moscow_age_days("2026-10-18T23:30:00+03:00") == synth.moscow_age_days("2026-10-19T06:30:00+10:00") == 2
    assert synth.moscow_age_days("2026-10-19T00:30:00+03:00") == synth.moscow_age_days("2026-10-18T21:30:00+00:00") == 1
    stale = synth.make_case({"kind": "ok_charter_cleared", "seed": 3, "registry_moment": "2026-10-18T23:30:00+03:00"})
    fresh = synth.make_case({"kind": "ok_charter_cleared", "seed": 3, "registry_moment": "2026-10-19T00:30:00+03:00"})
    revoked = synth.make_case({"kind": "revoked", "seed": 3, "registry_moment": "2026-10-17T10:00:00+03:00"})
    assert (stale.expected, fresh.expected, revoked.expected) == ("insufficient_data", "confirmed", "refuted")
    assert _verdict(stale) == "insufficient_data" and _verdict(fresh) == "confirmed" and _verdict(revoked) == "refuted"
    entry = next(iter(stale.manifest["registry"].values()))
    assert entry == {"status": "active", "observed_at": "2026-10-18T23:30:00+03:00"}


def test_term_on_the_last_day_holds_and_a_day_later_refutes():
    for kind in ("ok_charter_cleared", "ok", "director_changed"):
        last = synth.make_case({"kind": kind, "seed": 4, "term": "last_day"})
        ended = synth.make_case({"kind": kind, "seed": 4, "term": "ended_yesterday"})
        assert last.expected == synth.KINDS[kind] == _verdict(last)
        assert ended.expected == "refuted" == _verdict(ended)
    with pytest.raises(ValueError, match="не применяется"):
        synth.make_case({"kind": "ok_subdelegation", "seed": 4, "term": "last_day"})
    with pytest.raises(ValueError, match="не применяется"):
        synth.make_case({"kind": "ok_director", "seed": 4, "registry_moment": "2026-10-20T10:00:00+03:00"})


def test_stream_conditions_follow_the_profile():
    recipes = list(synth.production_recipes(3000, profiles=PROFILES))
    summary = synth.summarize(recipes, PROFILES, "vtb_prod")
    assert summary["cases"] == 3000
    assert summary["groups"]["чистый пакет"] / 3000 > 0.7
    assert set(summary["conditions"]) == set(synth.CONDITIONS)
    assert summary["shared_clients"] >= 1
    assert all(r["kind"] in synth.TERM_BASES for r in recipes if r.get("term"))
    assert not any(r["kind"] in synth.MOMENT_EXCLUDED for r in recipes if r.get("registry_moment"))
    shared = [r for r in recipes if r.get("org_seed") is not None]
    assert len({r["org_seed"] for r in shared}) < len(shared) / 5  # у клиента от 10 до 200 представителей


def _broken(change) -> list[str]:
    payload = copy.deepcopy(load_json("load_profiles.json"))
    change(payload["profiles"]["vtb_prod"])
    return synth.validate_mixes(payload)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda p: p["mix"][0].update(weight=0.9), "в сумме"),
        (lambda p: p["mix"][0].update(kind="no_such_kind"), "неизвестный вид"),
        (lambda p: p["mix"][1].update(kind=p["mix"][0]["kind"]), "указан дважды"),
        (lambda p: p["mix"][2].update(basis="так сложилось"), "basis"),
        (lambda p: p["mix"][3].pop("group"), "группа"),
        (lambda p: p["mix"][4].update(weight=-0.01), "weight"),
        (lambda p: p["conditions"].update(weekend={"share": 0.1, "basis": "допущение"}), "неизвестное условие"),
        (lambda p: p["conditions"]["repeat_check"].update(share=1.5), "доля от 0 до 1"),
        (lambda p: p["conditions"]["registry_moment"]["offsets"].update({"MSK": 0.0}), "недопустимый ключ"),
        (lambda p: p["conditions"]["registry_moment"]["age_days"].update({"0": 0.5}), "в сумме"),
        (lambda p: p["conditions"]["many_representatives"].update(size=[50, 10]), "size"),
        (lambda p: p["conditions"]["term_last_day"].pop("basis"), "поля"),
        (lambda p: p.update(extra=1), "лишние поля"),
    ],
)
def test_validation_names_the_broken_field(change, problem):
    problems = _broken(change)
    assert any(problem in text for text in problems), problems


def test_load_mixes_rejects_a_broken_file(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": {"x": {"description": "", "seed": 1, "mix": [], "conditions": {}}}}))
    with pytest.raises(ValueError, match="профили потока"):
        synth.load_mixes(path)
    with pytest.raises(ValueError, match="нет профиля"):
        list(synth.production_recipes(1, "no_such_mix", profiles=PROFILES))


def test_cli_writes_recipes_and_a_reproducible_case(tmp_path, capsys):
    out = tmp_path / "corpus.jsonl"
    assert synth.main(["--cases", "25", "--out", str(out), "--with-manifests"]) == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 25 and all({"recipe", "expected", "manifest"} <= set(row) for row in rows)
    assert json.loads(capsys.readouterr().out)["cases"] == 25
    recipe = rows[0]["recipe"]
    assert synth.main(["--recipe", json.dumps(recipe), "--case-dir", str(tmp_path / "case")]) == 0
    written = json.loads((tmp_path / "case" / "case.json").read_text(encoding="utf-8"))
    assert written["case_id"] == recipe["case_id"] and written["expected_verdict"] == rows[0]["expected"]


def test_run_production_reports_confusion_and_reproduction(monkeypatch):
    result = run.run_production(120, processes=1)
    assert result["cases"] == 120 and sum(result["confusion"].values()) == 120
    assert result["repeat_verdict_changed"] == []
    assert {"false_confirmed", "false_confirmed_rate_upper95", "mismatches_by_condition"} <= set(result)
    real = run._verify_recipe
    monkeypatch.setattr(run, "_verify_recipe", lambda job: {**real(job), "predicted": "confirmed"})
    forced = run.run_production(30, processes=1)
    assert forced["false_confirmed"] == sum(v for k, v in forced["expected"].items() if k != "confirmed")
    assert "--recipe" in forced["false_confirmed_cases"][0]["reproduce"]


def test_clopper_pearson_holds_on_stream_sizes():
    """На десятках тысяч кейсов граница считается, не переполняясь, и лежит рядом с интервалом Вилсона."""
    from mandat.eval.metrics import clopper_pearson, wilson_interval

    lower, upper = clopper_pearson(300, 60_000)
    assert 0.0044 < lower < 0.005 < upper < 0.0057
    assert abs(upper - wilson_interval(300, 60_000)[1]) < 2e-4
    assert clopper_pearson(3, 120) == clopper_pearson(3, 120)  # прямая сумма: прежний путь
    assert clopper_pearson(1, 6_000)[1] < 0.001
