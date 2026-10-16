"""Собирает evidence.json: каждое число из презентации — отсюда, с командой воспроизведения.

    python scripts/build_evidence.py            # тесты, демо, корпус
    python scripts/build_evidence.py --no-tests # без прогона pytest
    python scripts/build_evidence.py --verify-source  # evidence.json собран из этих исходников?

Перенесено по образцу SynAPS-ProgramPlan (evidence.json + CLAIMS_REGISTRY.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from bench import bench

from mandat import ENGINE
from mandat.eval.run import HOLDOUT_SEED, run_corpus
from mandat.examples import DEMO, OVERLAYS
from mandat.intake.case_loader import load_case_dir
from mandat.rules.classifier import default_classifier
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]


try:
    import pytest_cov  # noqa: F401

    COVERAGE_ARGS: tuple[str, ...] = ("--cov=mandat", "--cov-report=json:var/coverage.json", "--cov-fail-under=0")
except ModuleNotFoundError:
    COVERAGE_ARGS = ()


def pytest_summary() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:warnings", *COVERAGE_ARGS],
        cwd=ROOT,
        # Число автотестов в этот момент ещё собирается: lint_claims внутри прогона его не сверяет.
        env={**os.environ, "MANDAT_EVIDENCE_BUILDING": "1"},
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|error)", tail)}
    tail = next((line for line in reversed(proc.stdout.strip().splitlines()) if re.search(r"\d+ passed", line)), tail)
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|error)", tail)}
    out: dict = {"command": "python -m pytest -q", "returncode": proc.returncode, "summary": tail, **counts}
    cov = ROOT / "var" / "coverage.json"
    if COVERAGE_ARGS and cov.exists():
        out["coverage_percent"] = round(json.loads(cov.read_text(encoding="utf-8"))["totals"]["percent_covered"], 1)
    return out


# Исходники, от которых зависят числа в evidence.json. Сам evidence.json и документы сюда не входят.
SOURCE_DIRS = ("src", "tests", "scripts", "examples")


def source_digest() -> str:
    """SHA-256 по путям и содержимому отслеживаемых git файлов исходников, как они лежат на диске."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *SOURCE_DIRS], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(p for p in listed if p):
        path = ROOT / raw.decode("utf-8")
        if path.is_file():
            digest.update(raw + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def source_binding() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {
        "base_commit": head or None,
        "sources": list(SOURCE_DIRS),
        "sha256": source_digest(),
        "check": "python scripts/build_evidence.py --verify-source",
    }


def verify_source() -> int:
    recorded = (json.loads((ROOT / "evidence.json").read_text(encoding="utf-8")).get("source") or {}).get("sha256")
    actual = source_digest()
    if recorded != actual:
        print(f"evidence.json собран не из этих исходников: {recorded} != {actual}. Пересоберите: make evidence")
        return 1
    print(f"evidence.json соответствует исходникам ({actual[:16]}…)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--per-defect", type=int, default=6)
    parser.add_argument("--verify-source", action="store_true")
    args = parser.parse_args()
    if args.verify_source:
        return verify_source()

    pack, catalog, classifier = default_pack(), default_catalog(), default_classifier()
    demo = {}
    for folder in [*DEMO, *OVERLAYS]:
        report = verify_case(load_case_dir(ROOT / "examples" / "cases" / folder)).as_dict()
        demo[folder] = {"verdict": report["verdict"], "reproducibility_hash": report["reproducibility_hash"]}
    corpus = run_corpus(args.per_defect)
    corpus.pop("mismatches")
    strict = run_corpus(args.per_defect, profile="strict")
    holdout = run_corpus(args.per_defect, seed=HOLDOUT_SEED)
    holdout_summary = {
        "command": f"mandat eval --per-defect {args.per_defect} --seed {HOLDOUT_SEED}",
        **{
            k: holdout[k]
            for k in (
                "seed",
                "cases",
                "agreement",
                "false_confirmed",
                "false_confirmed_rate_upper95",
                "false_confirmed_defect_types",
                "false_confirmed_rate_upper95_by_defect_type",
                "coverage",
            )
        },
        "mismatches": len(holdout["mismatches"]),
        "boundary": "отложенный seed: те же шаблоны с другими значениями; подгонку под генератор не исключает",
    }
    strict_summary = {
        "command": f"mandat eval --per-defect {args.per_defect} --profile strict",
        "cases": strict["cases"],
        "false_confirmed": strict["false_confirmed"],
        "coverage": strict["coverage"],
        "abstention_rate": strict["abstention_rate"],
        "boundary": (
            "строгий профиль не подтверждает по черновикам правил и по снимку ЕГРЮЛ из пакета кейса; "
            "без СКЗИ подпись не проверена. Доля покрытия — в этом файле, не в старых слайдах"
        ),
    }
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "engine": ENGINE,
        "source": source_binding(),
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
        "synthetic_corpus_holdout": holdout_summary,
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
        "tests": None,
    }
    target = ROOT / "evidence.json"
    # Сначала записать свежие числа без тестов, потом прогнать pytest. Иначе тест
    # test_evidence_matches_current_engine сверяется со старым evidence.json, падает, и в файл попадает
    # «N−1 passed, 1 failed» — так возникло расхождение 714 против 715.
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_tests:
        evidence["tests"] = pytest_summary()
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("rule_pack", "tests")}, ensure_ascii=False, indent=2))
    return 0 if (evidence["tests"] is None or evidence["tests"]["returncode"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
