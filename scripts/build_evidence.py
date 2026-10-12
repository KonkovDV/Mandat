"""Собирает evidence.json: каждое число из презентации — отсюда, с командой воспроизведения.

    python scripts/build_evidence.py            # тесты, демо, корпус
    python scripts/build_evidence.py --no-tests # без прогона pytest

Перенесено по образцу SynAPS-ProgramPlan (evidence.json + CLAIMS_REGISTRY.md).
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from bench import bench

from mandat import ENGINE
from mandat.eval.run import run_corpus
from mandat.examples import DEMO
from mandat.intake.case_loader import load_case_dir
from mandat.rules.classifier import default_classifier
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]


def pytest_summary() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:warnings"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|error)", tail)}
    return {"command": "python -m pytest -q", "returncode": proc.returncode, "summary": tail, **counts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--per-defect", type=int, default=6)
    args = parser.parse_args()

    pack, catalog, classifier = default_pack(), default_catalog(), default_classifier()
    demo = {}
    for folder in DEMO:
        report = verify_case(load_case_dir(ROOT / "examples" / "cases" / folder)).as_dict()
        demo[folder] = {"verdict": report["verdict"], "reproducibility_hash": report["reproducibility_hash"]}
    corpus = run_corpus(args.per_defect)
    corpus.pop("mismatches")
    strict = run_corpus(args.per_defect, profile="strict")
    strict_summary = {
        "command": f"mandat eval --per-defect {args.per_defect} --profile strict",
        "cases": strict["cases"],
        "false_confirmed": strict["false_confirmed"],
        "coverage": strict["coverage"],
        "abstention_rate": strict["abstention_rate"],
        "boundary": (
            "строгий профиль без СКЗИ и утверждённых юристом правил: автоматически подтверждаются только "
            "действия руководителя по ЕГРЮЛ, остальное уходит человеку — так задумано"
        ),
    }
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "engine": ENGINE,
        "python": platform.python_version(),
        "rule_pack": {**pack.summary(), "rules": len(pack.rules)},
        "operations_catalog": {
            "version": catalog.version,
            "sha256": catalog.sha256,
            "synthetic": catalog.synthetic_codes,
        },
        "demo_cases": {"command": "mandat demo", "results": demo},
        "synthetic_corpus": {"command": f"mandat eval --per-defect {args.per_defect}", **corpus},
        "synthetic_corpus_strict": strict_summary,
        "subdelegation_property": {
            "command": "python -m pytest tests/test_properties.py tests/test_cedar.py",
            "chains": 384,
            "property": (
                "«подтверждено» ⇔ передоверие разрешено, не шире и не дольше родительской МЧД и покрывает операцию"
            ),
            "cedar_differential": "tests/test_cedar.py, нужен extra [cedar]",
        },
        "classifier": classifier.summary(),
        "latency": bench(20),
        "tests": None if args.no_tests else pytest_summary(),
    }
    (ROOT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("rule_pack", "tests")}, ensure_ascii=False, indent=2))
    return 0 if (evidence["tests"] is None or evidence["tests"]["returncode"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
