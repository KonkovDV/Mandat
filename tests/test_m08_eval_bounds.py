"""M-08: граница по видам дефектов рядом с границей по кейсам и отложенный seed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mandat.eval import run
from mandat.eval.metrics import wilson_interval

ROOT = Path(__file__).resolve().parents[1]


def test_bound_by_defect_type_is_wider_than_by_case():
    m = run.run_corpus(2)
    negatives = sum(1 for e in run.DEFECTS.values() if e != "confirmed")
    assert m["negative_defect_types"] == negatives
    assert m["false_confirmed_defect_types"] == []
    assert m["false_confirmed_rate_upper95_by_defect_type"] == wilson_interval(0, negatives)[1]
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
