"""Собирает evidence.json: каждое число из презентации — отсюда, с командой воспроизведения.

    python scripts/build_evidence.py            # тесты, демо, корпус
    python scripts/build_evidence.py --no-tests # без прогона pytest
    python scripts/build_evidence.py --verify-source  # evidence.json собран из этих исходников?

Утверждения с этими числами — в CLAIMS_REGISTRY.md; числа в текстах с evidence.json сверяет scripts/lint_claims.py.
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
from mandat.eval import repeat
from mandat.eval.run import HOLDOUT_SEED, run_corpus, run_production
from mandat.examples import DEMO, OVERLAYS
from mandat.intake.case_loader import load_case_dir
from mandat.rules.classifier import default_classifier
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]
# Поток пакетов по профилю банка (eval/synth.py, docs/CAPACITY.md) на каждой сборке: seed закреплён,
# поэтому сборки сравнимы между собой; прогон идёт на всех ядрах и занимает секунды.
STREAM_MIX, STREAM_CASES, STREAM_SEED = "vtb_prod", 10_000, 2027


try:
    import pytest_cov  # noqa: F401

    COVERAGE_ARGS: tuple[str, ...] = ("--cov=mandat", "--cov-report=json:var/coverage.json", "--cov-fail-under=0")
except ModuleNotFoundError:
    COVERAGE_ARGS = ()


def collected_count() -> int:
    """Число тестов в дереве по ``pytest --collect-only``, без запуска.

    ``--verify-source`` сверяет его с прогоном в evidence.json: хеш исходников один не ловит, что числа
    в evidence.json от старого прогона: 20.10 хеш переписали без pytest, и 1491 осталось при 1554 тестах.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if proc.returncode != 0 or match is None:
        raise SystemExit("pytest --collect-only не собрал тесты:\n" + proc.stdout[-2000:] + proc.stderr[-2000:])
    return int(match.group(1))


def pytest_summary() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:warnings", *COVERAGE_ARGS],
        cwd=ROOT,
        # Число автотестов в этот момент ещё собирается: lint_claims внутри прогона его не сверяет.
        env={**os.environ, "MANDAT_EVIDENCE_BUILDING": "1"},
        capture_output=True,
        text=True,
    )
    lines = proc.stdout.strip().splitlines()
    tail = next((line for line in reversed(lines) if re.search(r"\d+ passed", line)), lines[-1] if lines else "")
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|error)", tail)}
    out: dict = {
        "command": "python -m pytest -q",
        "returncode": proc.returncode,
        "summary": tail,
        **counts,
        "collected": collected_count(),
    }
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


def untracked_sources() -> list[str]:
    """Файлы в каталогах исходников, которых нет в git и которые git не игнорирует.

    Хеш видит только файлы git. evidence.json, собранный до ``git add`` нового теста или примера, после коммита
    в CI не сходится: так main был красным 22.11 и 23.11.2026. Поэтому сборка с такими файлами не идёт.
    """
    listed = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *SOURCE_DIRS],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    return sorted(p.decode("utf-8") for p in listed if p)


def source_binding() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {
        "base_commit": head or None,
        "sources": list(SOURCE_DIRS),
        "sha256": source_digest(),
        "check": "python scripts/build_evidence.py --verify-source",
    }


def verify_source() -> int:
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    recorded = (evidence.get("source") or {}).get("sha256")
    actual = source_digest()
    if recorded != actual:
        print(f"evidence.json собран не из этих исходников: {recorded} != {actual}. Пересоберите: make evidence")
        return 1
    # Хеш можно переписать и без прогона: число тестов в evidence.json должно совпасть с деревом.
    tests = evidence.get("tests") or {}
    ran = int(tests.get("passed") or 0) + int(tests.get("skipped") or 0)
    collected = collected_count()
    if ran != collected:
        print(f"evidence.json: тестов в прогоне — {ran}, в дереве — {collected}. Пересоберите: make evidence")
        return 1
    print(f"evidence.json соответствует исходникам ({actual[:16]}…) и числу тестов ({collected})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--per-defect", type=int, default=6)
    parser.add_argument("--verify-source", action="store_true")
    args = parser.parse_args()
    if args.verify_source:
        return verify_source()
    stray = untracked_sources()
    if stray:
        shown = ", ".join(stray[:5]) + (" …" if len(stray) > 5 else "")
        raise SystemExit(
            f"в {', '.join(SOURCE_DIRS)} есть файлы вне git: {shown}. Добавьте их (git add) или уберите "
            "и соберите снова: хеш исходников видит только файлы git, и CI отвергнет такой evidence.json"
        )

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
    # Повторные клиенты (eval/repeat.py): сколько платежей идёт к человеку без реестра заключений юриста и с ним.
    clients = repeat.run().as_dict()
    if clients["false_confirmed_with_registry"] or clients["false_confirmed_without_registry"]:
        raise SystemExit("повторные клиенты: платёж с ограничением устава подтверждён, evidence.json не собран")
    repeat_summary = {
        "command": "mandat eval",
        "clients": clients["clients"],
        "payments": clients["operations"],
        "to_human_without": clients["to_human_without_registry"],
        "to_human_with": clients["to_human_with_registry"],
        "breakdown": clients["reasons_with_registry"],
        "restricted_payments": clients["restricted_operations"],
        "confirmed_restricted": clients["false_confirmed_with_registry"],
        "boundary": clients["boundary"],
    }
    stream = run_production(STREAM_CASES, STREAM_MIX, seed=STREAM_SEED)
    if stream["false_confirmed"]:
        raise SystemExit(
            f"поток {STREAM_MIX}: ложных «подтверждено» {stream['false_confirmed']}, evidence.json не собран; "
            f"повтор первого: {stream['false_confirmed_cases'][0]['reproduce']}"
        )
    stream_gate = {
        "command": f"python -m mandat.eval.run --mix {STREAM_MIX} --cases {STREAM_CASES} --seed {STREAM_SEED}",
        "cases": stream["cases"],
        "agreement": stream["agreement"],
        "false_confirmed": stream["false_confirmed"],
        "profile": stream["mix"],
        "seed": stream["seed"],
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
        "repeat_clients": repeat_summary,
        "stream_gate": stream_gate,
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
    if not args.no_tests:
        # Матрица готовности к пилоту: «проверено» ставит прогон тестов её пунктов, а не запись в документе.
        # Документ чек-листа собирается до полного прогона: его сверку проверяет тест в этом же прогоне.
        import gen_pilot_checklist

        evidence["pilot_readiness"] = gen_pilot_checklist.readiness()
        gen_pilot_checklist.TARGET.write_text(
            gen_pilot_checklist.render(gen_pilot_checklist.checklist(), evidence), encoding="utf-8"
        )
    # Сначала записать свежие числа без тестов, потом прогнать pytest. Иначе тест
    # test_evidence_matches_current_engine сверяется со старым evidence.json, падает, и в файл попадает
    # «N−1 passed, 1 failed» — так возникло расхождение 714 против 715.
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_tests:
        evidence["tests"] = pytest_summary()
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("rule_pack", "tests")}, ensure_ascii=False, indent=2))
    if evidence["tests"] is not None and evidence["tests"]["returncode"] == 0:
        # Тексты для жюри: числа в метках ev: и форма слова за ними («2537 автотестов») — из записанного файла.
        sys.path.insert(0, str(ROOT / "scripts"))
        import sync_evidence_marks

        return sync_evidence_marks.main(["--write"])
    return 0 if (evidence["tests"] is None or evidence["tests"]["returncode"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
