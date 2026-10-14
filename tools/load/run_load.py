"""Нагрузочный стенд API на asyncio + httpx: ступенчатый рост, перегрузка, выдержка (soak).

Поднимает ``mandat serve`` отдельным процессом с выбранным хранилищем, гоняет POST /v1/cases
(разбор и проверка кейса) и GET /v1/cases/{id}, меряет p50/p95/p99, RPS, долю ошибок, RSS
сервера, а в конце проверяет целостность журнала решений.

    python tools/load/run_load.py --store memory --stages 1,4,16,32 --stage-seconds 10
    python tools/load/run_load.py --store sqlite --soak-seconds 120
    python tools/load/run_load.py --smoke            # короткий прогон для CI

Это нагрузка одной машины: клиент и сервер делят CPU. Цифры — для сравнения версий, а не SLA.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mandat.audit import journal  # noqa: E402
from mandat.eval import synth  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def _manifests(n: int) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        kinds = sorted(synth.DEFECTS)
        out.append(synth.build(kinds[i % len(kinds)], 1000 + i).inline_manifest())
    return out


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] * 1000


class Server:
    def __init__(self, store: str, rate: int, workdir: Path, workers: int = 1):
        self.port = _free_port()
        self.token = secrets.token_hex(32)
        self.journal = workdir / "journal.jsonl"
        env = {
            **os.environ,
            "MANDAT_ENV": "demo",
            "MANDAT_API_TOKEN": self.token,
            "MANDAT_API_RATE_PER_MIN": str(rate),
            "MANDAT_API_JOURNAL": str(self.journal),
            "MANDAT_API_STORE": {"memory": "", "sqlite": f"sqlite:///{workdir / 'api.db'}"}.get(store, store),
        }
        env.pop("MANDAT_API_USERS", None)
        # Журнал сервера — в файл, не в PIPE: непрочитанный PIPE заполняется (64 КБ) предупреждениями
        # uvicorn о превышении limit_concurrency, и сервер блокируется на записи в stderr (R3-LOAD-1).
        workdir.mkdir(parents=True, exist_ok=True)
        self.log = (workdir / "server.log").open("wb")
        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "mandat.cli", "serve", "--port", str(self.port), "--workers", str(workers)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self.log,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(self.base + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("сервер не поднялся")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


async def stage(server: Server, manifests: list[dict[str, Any]], users: int, seconds: float) -> dict[str, Any]:
    lat: list[float] = []
    codes: dict[int, int] = {}
    created: list[str] = []
    stop_at = time.perf_counter() + seconds
    headers = {"Authorization": f"Bearer {server.token}"}
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    async with httpx.AsyncClient(base_url=server.base, headers=headers, timeout=30, limits=limits) as client:

        async def user(u: int) -> None:
            i = u
            while time.perf_counter() < stop_at:
                m = dict(manifests[i % len(manifests)])
                m["case_id"] = f"load-{u}-{i}-{secrets.token_hex(3)}"
                i += users
                t0 = time.perf_counter()
                try:
                    r = await client.post("/v1/cases", json=m)
                    code = r.status_code
                    if code == 200:
                        created.append(m["case_id"])
                        g = await client.get(f"/v1/cases/{m['case_id']}")
                        code = code if g.status_code == 200 else 1000 + g.status_code
                except httpx.HTTPError:
                    code = 0
                lat.append(time.perf_counter() - t0)
                codes[code] = codes.get(code, 0) + 1

        started = time.perf_counter()
        await asyncio.gather(*(user(u) for u in range(users)))
        elapsed = time.perf_counter() - started
    total = sum(codes.values())
    errors = sum(v for k, v in codes.items() if k not in (200, 429))
    return {
        "users": users,
        "requests": total,
        "rps": round(total / elapsed, 1),
        "p50_ms": round(pct(lat, 0.50), 1),
        "p95_ms": round(pct(lat, 0.95), 1),
        "p99_ms": round(pct(lat, 0.99), 1),
        "error_rate": round(errors / total, 4) if total else 0.0,
        "codes": {str(k): v for k, v in sorted(codes.items())},
        "rss_kb": _rss_kb(server.proc.pid),
        "created": len(created),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--store", default="memory", help="memory, sqlite или redis://…")
    p.add_argument("--stages", default="1,4,16,32")
    p.add_argument("--stage-seconds", type=float, default=10)
    p.add_argument("--soak-seconds", type=float, default=0)
    p.add_argument("--overload", action="store_true", help="фаза с лимитом 120/мин: ожидаются 429")
    p.add_argument("--smoke", action="store_true", help="короткий прогон для CI с порогами")
    p.add_argument("--workers", type=int, default=1, help="воркеры uvicorn (нужно sqlite или redis)")
    p.add_argument("--out", help="JSON с результатами")
    args = p.parse_args(argv)
    if args.smoke:
        args.stages, args.stage_seconds = "4", 5
    manifests = _manifests(25)
    result: dict[str, Any] = {"store": args.store, "cpu": os.cpu_count(), "stages": []}
    with tempfile.TemporaryDirectory() as tmp:
        server = Server(args.store, 0, Path(tmp), args.workers)
        try:
            result["rss_start_kb"] = _rss_kb(server.proc.pid)
            for users in [int(x) for x in args.stages.split(",")]:
                r = asyncio.run(stage(server, manifests, users, args.stage_seconds))
                result["stages"].append(r)
                print(json.dumps(r, ensure_ascii=False), flush=True)
            if args.soak_seconds:
                soak = []
                for _ in range(max(1, int(args.soak_seconds // 30))):
                    soak.append(asyncio.run(stage(server, manifests, 8, min(30, args.soak_seconds))))
                    print(json.dumps({"soak": soak[-1]}, ensure_ascii=False), flush=True)
                result["soak"] = soak
            check = journal.verify_journal(server.journal)
            result["journal"] = {"records": check.records, "ok": check.ok, "reason": check.reason}
        finally:
            server.stop()
        if args.overload:
            limited = Server(args.store, 120, Path(tmp) / "o", args.workers)
            try:
                r = asyncio.run(stage(limited, manifests, 16, 5))
                result["overload"] = r
                print(json.dumps({"overload": r}, ensure_ascii=False), flush=True)
            finally:
                limited.stop()
    print(json.dumps({"journal": result["journal"]}, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.smoke:
        s = result["stages"][0]
        ok = result["journal"]["ok"] and s["error_rate"] == 0 and s["requests"] > 0
        print("smoke-load:", "OK" if ok else "FAIL")
        return 0 if ok else 1
    return 0 if result["journal"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
