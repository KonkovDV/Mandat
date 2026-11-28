"""Сравнение моделей для советника на двух задачах «Мандата».

1. Устав: ограничения полномочий директора из 18 фрагментов уставов (`examples/advisory/charters.jsonl`, у каждого
   эталон). Ответ модели проходит тот же шлюз, что в работе (`ground_candidates`): факт без дословной цитаты
   отбрасывается. Считаем верные факты, лишние (выдуманный порог или вид) и пропущенные.
2. Пояснение: вердикт и находки 14 демо-кейсов (`examples/cases`) объясняются сотруднику через `explain`;
   считаем, сколько фраз прошло фильтр и сколько отброшено.

Модель вызывается через `mandat.advisory.llm.eval_complete`: единственная точка выхода к модели остаётся
в `advisory/llm.py`, а в защищённых режимах (pilot, prod) оценка не запускается.

    python scripts/eval_advisory.py --models mistralai/mistral-small-3.2-24b-instruct,qwen/qwen3-32b \
        --out var/eval/advisory.json

Адрес и ключ — в EVAL_LLM_BASE_URL и EVAL_LLM_API_KEY, хост модели — в MANDAT_LLM_ALLOWED_HOSTS, например:

    MANDAT_LLM_ALLOWED_HOSTS=openrouter.ai EVAL_LLM_BASE_URL=https://openrouter.ai/api/v1 EVAL_LLM_API_KEY=… \
        python scripts/eval_advisory.py --models …
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mandat.advisory.explainer import explain, facts_of
from mandat.advisory.gate import ground_candidates
from mandat.advisory.llm import charter_messages, eval_complete, parse_facts
from mandat.domain.model import TextDocument
from mandat.intake.case_loader import load_case_dir
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "Может ли представитель провести эту операцию и что делать сотруднику?"
# Для OpenRouter: только провайдеры без хранения запросов, стоимость вызова в ответе, без рассуждений вслух.
EXTRA = {"provider": {"data_collection": "deny"}, "usage": {"include": True}, "reasoning": {"enabled": False}}
TIMEOUT = 90.0


def call(base: str, key: str, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    extra = dict(EXTRA)
    if model.startswith("openai/gpt-oss"):  # у этих моделей рассуждение не выключается, только сокращается
        extra["reasoning"] = {"effort": "low"}
    t0 = time.monotonic()
    try:
        out = eval_complete(base, model, key, messages, extra=extra, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — сбой вызова учитывается как ошибка модели
        return {"ok": False, "error": type(exc).__name__, "s": time.monotonic() - t0, "cost": 0.0}
    return {"ok": True, "text": out["text"], "s": time.monotonic() - t0, "cost": float(out["usage"].get("cost") or 0)}


def charter_item(base: str, key: str, model: str, row: dict[str, Any]) -> dict[str, Any]:
    text = row["text"]
    doc = TextDocument(row["id"], hashlib.sha256(text.encode()).hexdigest(), "charter_text", text)
    messages = charter_messages(text)
    res = call(base, key, model, messages)
    if not res["ok"]:
        return {"id": row["id"], **res, "tp": 0, "fp": 0, "fn": len(row["expected"]), "json": False}
    raw = parse_facts(res["text"])
    got = ground_candidates(raw, doc, seen=messages[1]["content"]).accepted
    want = [(e["kind"], e["value"]) for e in row["expected"]]
    left = list(want)
    tp = fp = 0
    for fact in got:
        key_kind = fact.kind
        match = next(
            (w for w in left if w[0] == key_kind and (key_kind != "approval_threshold" or w[1] == fact.value)), None
        )
        if match is None:
            fp += 1
        else:
            tp += 1
            left.remove(match)
    return {
        "id": row["id"], "ok": True, "s": res["s"], "cost": res["cost"], "json": bool(raw) or "{" in res["text"],
        "tp": tp, "fp": fp, "fn": len(left),
        "got": [(f.kind, f.value) for f in got],
    }  # fmt: skip


def explain_item(base: str, key: str, model: str, case_dir: Path) -> dict[str, Any]:
    report = verify_case(load_case_dir(case_dir)).as_dict()
    meta: dict[str, Any] = {}

    def complete(messages: list[dict[str, str]]) -> str:
        meta.update(call(base, key, model, messages))
        return str(meta.get("text") or "")

    kept, dropped = explain(QUESTION, facts_of(report), complete)
    return {"id": case_dir.name, "ok": meta.get("ok", False), "s": meta.get("s", 0.0), "cost": meta.get("cost", 0.0),
            "kept": len(kept), "dropped": dropped}  # fmt: skip


def run_model(base: str, key: str, model: str, workers: int) -> dict[str, Any]:
    rows = [json.loads(x) for x in (ROOT / "examples/advisory/charters.jsonl").read_text("utf-8").splitlines() if x]
    cases = sorted(p for p in (ROOT / "examples/cases").iterdir() if p.is_dir())
    with ThreadPoolExecutor(workers) as pool:
        ch = list(pool.map(lambda r: charter_item(base, key, model, r), rows))
        ex = list(pool.map(lambda c: explain_item(base, key, model, c), cases))
    tp, fp, fn = (sum(x[k] for x in ch) for k in ("tp", "fp", "fn"))
    calls = ch + ex
    return {
        "model": model,
        "charter": {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / ((tp + fp) or 1), 3),
                    "recall": round(tp / ((tp + fn) or 1), 3), "errors": sum(not x["ok"] for x in ch),
                    "cases_with_false_fact": sum(x["fp"] > 0 for x in ch)},
        "explain": {"kept": sum(x["kept"] for x in ex), "dropped": sum(x["dropped"] for x in ex),
                    "errors": sum(not x["ok"] for x in ex)},
        "median_s": round(statistics.median(x["s"] for x in calls), 2),
        "cost_usd": round(sum(x.get("cost", 0.0) for x in calls), 5),
        "calls": len(calls),
        "details": {"charter": ch, "explain": ex},
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--out", default="var/eval/advisory.json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=TIMEOUT, help="предел одного вызова, с")
    args = ap.parse_args()
    globals()["TIMEOUT"] = args.timeout
    base = os.environ.get("EVAL_LLM_BASE_URL", "")
    key = os.environ.get("EVAL_LLM_API_KEY", "")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for model in (m.strip() for m in args.models.split(",") if m.strip()):
        results.append(run_model(base, key, model, args.workers))
        # Файл переписывается после каждой модели: долгий прогон, прерванный на середине, не теряет готовое.
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    head = ("модель", "устав: верно / лишних / пропущено", "кейсов с лишним фактом")
    head += ("пояснение: фраз / отброшено", "медиана, с", "цена прогона, $")
    print("| " + " | ".join(head) + " |\n|" + "---|" * len(head))
    for r in results:
        c, e = r["charter"], r["explain"]
        cells = (r["model"], f"{c['tp']} / {c['fp']} / {c['fn']}", c["cases_with_false_fact"])
        cells += (f"{e['kept']} / {e['dropped']}", r["median_s"], r["cost_usd"])
        print("| " + " | ".join(map(str, cells)) + " |")


if __name__ == "__main__":
    main()
