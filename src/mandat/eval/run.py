"""Прогон движка по синтетическому корпусу и метрики.

Главная защитная метрика — доля ложных «подтверждено» среди кейсов, где
подтверждения быть не должно, с верхней границей точного двустороннего интервала Клоппера — Пирсона 95 %
(«*_upper95»); поля «*_wilson95» — интервал Вилсона.
Для системы с правом воздержаться («на разбор», «недостаточно данных») считаем
пару «риск — покрытие» (selective classification): покрытие — доля кейсов с
автоматическим вердиктом, риск — доля ошибок среди них.
Кейсы одного вида дефекта строит один шаблон, они не независимы: граница по кейсам занижает
неопределённость. Поэтому рядом считается граница по видам дефектов (кластерам): вид «ложно
подтверждён», если ложно подтверждён хотя бы один его кейс.
Синтетика проверяет логику правил; точность на реальных документах измеряется
только на пилоте (docs/EVALUATION.md).
"""

from __future__ import annotations

import json
import multiprocessing
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mandat.eval.metrics import clopper_pearson, required_n, wilson_interval
from mandat.eval.synth import DEFECTS, build, conditions_of, load_mixes, make_case, production_recipes
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case

# Отложенный seed (holdout): правила и тесты на нём не отлаживаются, он используется только в evidence.json.
# Отлавливает подгонку под конкретные значения seed по умолчанию, но не под сам генератор.
HOLDOUT_SEED = 20261016


def run_corpus(n_per_defect: int = 3, *, seed: int = 7, profile: str = "demo") -> dict[str, Any]:
    rows = []
    for defect, expected in DEFECTS.items():
        for k in range(n_per_defect):
            scenario = build(defect, seed=seed * 1000 + k)
            report = verify_case(load_case_inline(scenario.inline_manifest()), profile=profile)
            rows.append(
                {
                    "case_id": scenario.case_id,
                    "defect": defect,
                    "expected": expected,
                    "predicted": report.verdict.value,
                }
            )

    total = len(rows)
    correct = sum(r["expected"] == r["predicted"] for r in rows)
    negatives = [r for r in rows if r["expected"] != "confirmed"]
    false_confirmed = sum(r["predicted"] == "confirmed" for r in negatives)
    refute_rows = [r for r in rows if r["expected"] == "refuted"]
    refute_hits = sum(r["predicted"] == "refuted" for r in refute_rows)
    abstained = sum(r["predicted"] in {"needs_review", "insufficient_data"} for r in rows)
    auto = [r for r in rows if r["predicted"] in {"confirmed", "refuted"}]
    auto_errors = sum(r["predicted"] != r["expected"] for r in auto)
    negative_types = sorted({r["defect"] for r in negatives})
    false_types = sorted({r["defect"] for r in negatives if r["predicted"] == "confirmed"})
    confusion = Counter((r["expected"], r["predicted"]) for r in rows)
    return {
        "profile": profile,
        "seed": seed,
        "cases": total,
        "defect_types": len(DEFECTS),
        "agreement": correct / total,
        "agreement_wilson95": wilson_interval(correct, total),
        "false_confirmed": false_confirmed,
        "negative_cases": len(negatives),
        "false_confirmed_rate_upper95": clopper_pearson(false_confirmed, len(negatives))[1],
        "negative_defect_types": len(negative_types),
        "false_confirmed_defect_types": false_types,
        "false_confirmed_rate_upper95_by_defect_type": clopper_pearson(len(false_types), len(negative_types))[1],
        "refute_recall": refute_hits / len(refute_rows) if refute_rows else None,
        "refute_recall_wilson95": wilson_interval(refute_hits, len(refute_rows)),
        "abstention_rate": abstained / total,
        "coverage": len(auto) / total,
        "auto_risk": auto_errors / len(auto) if auto else None,
        "auto_risk_upper95": clopper_pearson(auto_errors, len(auto))[1] if auto else None,
        "n_for_recall_0_9_pm_0_05": required_n(0.9, 0.05),
        "confusion": {f"{e}->{p}": c for (e, p), c in sorted(confusion.items())},
        "mismatches": [r for r in rows if r["expected"] != r["predicted"]],
        "boundary": "синтетический корпус: проверка логики правил, не точность на документах банка",
    }


# --- Поток по профилю (data/load_profiles.json): вердикты на десятках тысяч кейсов --------------------------


def _verify_recipe(job: tuple[dict[str, Any], str]) -> dict[str, Any]:
    recipe, profile = job
    scenario = make_case(recipe)
    report = verify_case(load_case_inline(scenario.inline_manifest()), profile=profile)
    return {
        "case_id": scenario.case_id,
        "kind": scenario.defect,
        "expected": scenario.expected,
        "predicted": report.verdict.value,
        "repeat_of": recipe.get("repeat_of"),
        "conditions": conditions_of(recipe),
    }


def _reproduce(recipe: dict[str, Any]) -> str:
    text = json.dumps(recipe, ensure_ascii=False, separators=(",", ":"))
    return f"python -m mandat.eval.synth --recipe '{text}' --case-dir var/repro && mandat verify var/repro"


def run_production(
    cases: int = 10_000,
    mix: str = "vtb_prod",
    *,
    seed: int | None = None,
    profile: str = "demo",
    processes: int | None = None,
    profiles_path: Path | None = None,
) -> dict[str, Any]:
    """Прогон потока по профилю: матрица «ожидалось → получено», ложные «подтверждено» с рецептом повтора.

    Ожидаемый вердикт кейса выводит генератор из вида и условий (``make_case``), а не движок. Повторная
    проверка той же МЧД обязана дать тот же вердикт, что и первая: расхождение — недетерминизм движка.
    ``processes`` — число процессов (по умолчанию все ядра); 1 — в этом процессе.
    """
    profiles = load_mixes(profiles_path)
    recipes = list(production_recipes(cases, mix, seed=seed, profiles=profiles))
    by_id = {r["case_id"]: r for r in recipes}
    jobs = [(recipe, profile) for recipe in recipes]
    workers = processes or multiprocessing.cpu_count()
    started = time.perf_counter()
    if workers == 1:
        rows = [_verify_recipe(job) for job in jobs]
    else:
        with multiprocessing.get_context("spawn").Pool(workers) as pool:
            rows = pool.map(_verify_recipe, jobs, chunksize=max(1, len(jobs) // (workers * 16)))
    elapsed = time.perf_counter() - started
    total = len(rows)
    confusion = Counter((r["expected"], r["predicted"]) for r in rows)
    try:
        import resource

        # ru_maxrss в Linux — килобайты: пик процесса и самого большого из рабочих процессов.
        peak_kb = max(resource.getrusage(who).ru_maxrss for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN))
        peak_rss_mb: float | None = round(peak_kb / 1024, 1)
    except (ImportError, OSError):  # Windows: модуля resource нет
        peak_rss_mb = None
    negatives = [r for r in rows if r["expected"] != "confirmed"]
    false_confirmed = [r for r in negatives if r["predicted"] == "confirmed"]
    first = {r["case_id"]: r["predicted"] for r in rows if not r["repeat_of"]}
    unstable = [
        r["case_id"] for r in rows if r["repeat_of"] and first.get(r["repeat_of"]) not in {None, r["predicted"]}
    ]
    mismatched = [r for r in rows if r["expected"] != r["predicted"]]
    by_kind: Counter[str] = Counter()
    by_condition: Counter[str] = Counter()
    for r in mismatched:
        by_kind[f"{r['kind']}: {r['expected']}->{r['predicted']}"] += 1
        for name in r["conditions"] or ["без условий"]:
            by_condition[f"{name}: {r['expected']}->{r['predicted']}"] += 1
    correct = total - len(mismatched)
    return {
        "mix": mix,
        "seed": seed if seed is not None else profiles[mix]["seed"],
        "profile": profile,
        "cases": total,
        "kinds": len({r["kind"] for r in rows}),
        "processes": workers,
        "elapsed_s": round(elapsed, 2),
        "cases_per_s": round(total / elapsed, 1) if elapsed else None,
        "peak_rss_mb": peak_rss_mb,
        "expected": dict(Counter(r["expected"] for r in rows)),
        "predicted": dict(Counter(r["predicted"] for r in rows)),
        "agreement": correct / total if total else None,
        "agreement_wilson95": wilson_interval(correct, total) if total else None,
        "false_confirmed": len(false_confirmed),
        "false_confirmed_rate_upper95": clopper_pearson(len(false_confirmed), len(negatives))[1] if negatives else None,
        "false_confirmed_cases": [{**r, "reproduce": _reproduce(by_id[r["case_id"]])} for r in false_confirmed[:20]],
        "repeat_checks": sum(1 for r in rows if r["repeat_of"]),
        "repeat_verdict_changed": unstable[:20],
        "confusion": {f"{e}->{p}": c for (e, p), c in sorted(confusion.items())},
        "mismatches_by_kind": dict(by_kind.most_common()),
        "mismatches_by_condition": dict(by_condition.most_common()),
        "boundary": "синтетический поток: доли — допущения профиля, не статистика банка",
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m mandat.eval.run --mix vtb_prod --cases 10000``: код 1, если есть ложные «подтверждено»."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m mandat.eval.run", description="прогон потока по профилю")
    parser.add_argument("--mix", default="vtb_prod")
    parser.add_argument("--cases", type=int, default=10_000)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--profile", default="demo", choices=["demo", "strict"])
    parser.add_argument("--processes", type=int)
    parser.add_argument("--profiles", type=Path, help="свой файл профилей вместо data/load_profiles.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = run_production(
        args.cases,
        args.mix,
        seed=args.seed,
        profile=args.profile,
        processes=args.processes,
        profiles_path=args.profiles,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if result["false_confirmed"] or result["repeat_verdict_changed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
