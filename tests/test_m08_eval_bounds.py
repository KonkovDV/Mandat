"""M-08: граница по видам дефектов рядом с границей по кейсам и отложенный seed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mandat.eval import run
from mandat.eval.metrics import clopper_pearson, wilson_interval

ROOT = Path(__file__).resolve().parents[1]


def test_bound_by_defect_type_is_wider_than_by_case():
    m = run.run_corpus(2)
    negatives = sum(1 for e in run.DEFECTS.values() if e != "confirmed")
    assert m["negative_defect_types"] == negatives
    assert m["false_confirmed_defect_types"] == []
    assert m["false_confirmed_rate_upper95_by_defect_type"] == clopper_pearson(0, negatives)[1]
    assert m["false_confirmed_rate_upper95_by_defect_type"] > m["false_confirmed_rate_upper95"]
    assert m["seed"] == 7


def test_false_confirmed_type_counts_once(monkeypatch):
    monkeypatch.setattr(run, "DEFECTS", {"a": "refuted", "b": "needs_review"})
    monkeypatch.setattr(
        run, "build", lambda defect, seed: SimpleNamespace(case_id=f"{defect}-{seed}", inline_manifest=dict)
    )
    monkeypatch.setattr(run, "load_case_inline", lambda manifest: manifest)
    monkeypatch.setattr(
        run, "verify_case", lambda case, profile: SimpleNamespace(verdict=SimpleNamespace(value="confirmed"))
    )
    m = run.run_corpus(3)
    assert m["false_confirmed"] == 6
    assert m["false_confirmed_defect_types"] == ["a", "b"]
    assert m["false_confirmed_rate_upper95_by_defect_type"] == 1.0


def test_holdout_seed_is_reserved():
    """Отложенный seed не используется в тестах и коде, кроме evidence.json: на нём ничего не отлаживается."""
    seed = str(run.HOLDOUT_SEED)
    users = [
        p.relative_to(ROOT).as_posix()
        for top in ("src", "tests", "scripts")
        for p in (ROOT / top).rglob("*.py")
        if seed in p.read_text(encoding="utf-8")
    ]
    assert users == ["src/mandat/eval/run.py"]
    assert run.HOLDOUT_SEED != 7


def test_evidence_reports_holdout_separately():
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    holdout = evidence["synthetic_corpus_holdout"]
    assert holdout["seed"] == run.HOLDOUT_SEED != evidence["synthetic_corpus"]["seed"]
    assert holdout["cases"] == evidence["synthetic_corpus"]["cases"]
    assert "by_defect_type" in " ".join(evidence["synthetic_corpus"])


def test_documented_zero_failure_bounds():
    """Числа в EVALUATION «Как считаются границы»: Клоппер — Пирсон, Уилсон, односторонняя и правило трёх."""
    expected = {48: (7.40, 7.41, 6.05, 6.25), 288: (1.27, 1.32, 1.03, 1.04)}
    for n, (cp2, wilson, cp1, rule3) in expected.items():
        assert round(100 * (1 - 0.025 ** (1 / n)), 2) == cp2
        assert round(100 * wilson_interval(0, n)[1], 2) == wilson
        assert round(100 * (1 - 0.05 ** (1 / n)), 2) == cp1
        assert round(100 * 3 / n, 2) == rule3


# Эталон: scipy 1.18.1, scipy.stats.beta.ppf(0.975, x + 1, n - x) и beta.ppf(0.025, x, n - x + 1),
# посчитано в отдельном окружении вне репозитория, не этой функцией.
@pytest.mark.parametrize(
    ("x", "n", "lower", "upper"),
    [
        (0, 48, 0.0, 0.07397278534728022),
        (0, 288, 0.0, 0.012726928093101806),
        (2, 48, 0.005086467930032974, 0.1425409732013774),
    ],
)
def test_clopper_pearson_matches_reference(x, n, lower, upper):
    lo, hi = clopper_pearson(x, n)
    assert lo == pytest.approx(lower, abs=1e-9)
    assert hi == pytest.approx(upper, abs=1e-9)


def test_clopper_pearson_edges():
    assert clopper_pearson(0, 0) == (0.0, 1.0)
    assert clopper_pearson(48, 48)[1] == 1.0
    assert clopper_pearson(48, 48)[0] == pytest.approx(1 - 0.07397278534728022)
    with pytest.raises(ValueError):
        clopper_pearson(3, 2)


def test_corpus_bounds_are_clopper_pearson():
    m = run.run_corpus(2)
    negatives = sum(1 for r in run.DEFECTS.values() if r != "confirmed") * 2
    assert m["false_confirmed_rate_upper95"] == clopper_pearson(0, negatives)[1]
