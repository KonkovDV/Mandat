"""Время проверки одного кейса на демо-кейсах: разбор документов + правила + свёртка + отчёт.

    python scripts/bench.py --rounds 20

Число зависит от машины; в материалах указывать вместе с машиной и командой.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

from mandat.intake.case_loader import load_case_dir
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]


def bench(rounds: int = 20) -> dict:
    cases = sorted((ROOT / "examples" / "cases").iterdir())
    verify_case(load_case_dir(cases[0]))  # прогрев
    samples = []
    for _ in range(rounds):
        for case_dir in cases:
            start = time.perf_counter()
            verify_case(load_case_dir(case_dir)).as_dict()
            samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return {
        "command": f"python scripts/bench.py --rounds {rounds}",
        "cases": len(samples),
        "median_ms": round(statistics.median(samples), 2),
        "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 2),
        "machine": f"{platform.machine()}, Python {platform.python_version()}",
        "scope": "разбор XML и JSON, 32 правила, свёртка, отчёт; без сети и без модели",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    print(json.dumps(bench(parser.parse_args().rounds), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
