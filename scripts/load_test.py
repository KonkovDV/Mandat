"""Нагрузочный прогон HTTP API «Мандата» на потоке, похожем на банковский.

Два потока запросов, как в банке:

* проверка кейса — ``POST /v1/cases`` с пакетами потока ``vtb_prod`` (``mandat.eval.synth``), роль ``operator``;
* допуск операции ДБО — ``POST /v1/operations/authorize``, его ДБО зовёт перед каждым платежом юрлица, роль
  ``dbo`` со своим получателем (audience). Удостоверения на допуск выпускает сам стенд ключом издателя стенда.

Приход запросов — открытая модель (поток Пуассона, задержка считается от момента прихода, поэтому очередь
перед сервером видна в p95 и p99): ``steady`` — ровно R в секунду, ``morning_peak`` — утренний пик ×3 на 40 %
прогона, ``month_end`` — всплеск конца месяца ×5 на 20 % прогона. ``closed`` — замкнутая модель: C клиентов
шлют запросы подряд, это предел пропускной способности конфигурации.

    python scripts/load_test.py --scenario cases --arrival closed --concurrency 16 --seconds 15
    python scripts/load_test.py --scenario authorize --arrival month_end --rate 50 --workers 4 --store redis
    python scripts/load_test.py --matrix --out var/load/matrix.json      # SQLite и Redis × 1, 2, 4 воркера
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --token "$TOKEN" --scenario cases

Сервер поднимается как в пилоте (``MANDAT_ENV=pilot``): пользователи с ролями из ``MANDAT_API_USERS``
(токены выпускает ``mandat api-user``), журнал аудита с ключом HMAC, шифрованное хранилище, ключ ПДн, допуск с
ключом издателя, списками статусов и ключом представителя. Ключи и каталоги временные, Redis — свой, на
случайном порту. Клиент и сервер делят процессоры одной машины, сеть — loopback: цифры годятся для сравнения
конфигураций и поиска узких мест, а не как SLA.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import multiprocessing
import os
import platform
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AUDIENCE = "bank-dbo-load"
# Профиль прихода: (доля длительности, множитель базовой частоты).
ARRIVALS: dict[str, list[tuple[float, float]]] = {
    "steady": [(1.0, 1.0)],
    "morning_peak": [(0.3, 1.0), (0.4, 3.0), (0.3, 1.0)],
    "month_end": [(0.4, 1.0), (0.2, 5.0), (0.4, 1.0)],
}
MAX_SECONDS = 60.0
GRACE_SECONDS = 5.0  # после окна прихода ждём ответы не дольше: остальное — отказ по таймауту


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _tree_rss_kb(pid: int) -> int:
    """RSS процесса и всех его потомков (мастер uvicorn и воркеры), КБ."""
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
        except (OSError, IndexError, ValueError):
            continue
    total, todo = 0, [pid]
    while todo:
        current = todo.pop()
        try:
            for line in Path(f"/proc/{current}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            pass
        todo.extend(children.get(current, []))
    return total


class RssSampler(threading.Thread):
    """Пиковый RSS дерева процессов сервера за прогон."""

    def __init__(self, pid: int | None) -> None:
        super().__init__(daemon=True)
        self.pid, self.peak_kb, self._halt = pid, 0, threading.Event()

    def run(self) -> None:
        while self.pid and not self._halt.wait(0.25):
            self.peak_kb = max(self.peak_kb, _tree_rss_kb(self.pid))

    def stop(self) -> int:
        self._halt.set()
        self.join(2)
        return self.peak_kb


class Redis:
    """Свой redis-server на случайном локальном порту, без сохранения на диск; останавливается после прогона."""

    def __init__(self, workdir: Path) -> None:
        binary = shutil.which("redis-server")
        if binary is None:
            raise RuntimeError("нет redis-server: установите Redis 7 или выберите --store sqlite")
        self.port = _free_port()
        self.proc = subprocess.Popen(  # noqa: S603
            [binary, "--port", str(self.port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.1)
        self.stop()
        raise RuntimeError("redis-server не поднялся")

    def url(self, db: int) -> str:
        return f"redis://127.0.0.1:{self.port}/{db}"

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _api_user(name: str, role: str, audience: str | None = None) -> tuple[str, dict[str, Any]]:
    """Токен и строка ``MANDAT_API_USERS`` от ``mandat api-user`` — тот же путь, что у банка (docs/RUN.md)."""
    cmd = [sys.executable, "-m", "mandat.cli", "api-user", "--name", name, "--role", role, "--expires-in-days", "2"]
    if audience:
        cmd += ["--audience", audience]
    env = {**os.environ, "MANDAT_ENV": "pilot", "PYTHONPATH": str(ROOT / "src")}
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True).stdout  # noqa: S603
    token = re.search(r"\(показывается один раз\): (\S+)", out)
    if token is None:
        raise RuntimeError(f"mandat api-user: неожиданный вывод: {out[:200]}")
    return token.group(1), json.loads(out.strip().splitlines()[-1])


class Admission:
    """Издатель стенда: ключ, связка, ключ представителя, списки статусов и пул удостоверений «на сегодня»."""

    def __init__(self, directory: Path, credentials: int) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from mandat.audit import attestation
        from mandat.audit.attestation import BANK_TZ
        from mandat.audit.signing import key_id
        from mandat.audit.status_list import StatusRegistry
        from mandat.eval.synth import build
        from mandat.intake.case_loader import load_case_inline
        from mandat.services.verify import verify_case

        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.key = Ed25519PrivateKey.generate()
        (directory / "keyring.json").write_text(json.dumps({"keys": [{"kid": key_id(self.key.public_key())}]}))
        rep_key = secrets.token_bytes(32)
        rep_path = directory / "representative.key"
        rep_path.write_bytes(rep_key)
        rep_path.chmod(0o600)
        today = datetime.now(UTC).astimezone(BANK_TZ).date().isoformat()
        # Отчёты настоящих проверок синтетических клиентов; день операции — сегодня, профиль — strict, как в пилоте.
        reports = []
        for seed in range(1, 201):
            report = verify_case(load_case_inline(build("ok_charter_cleared", seed).inline_manifest())).as_dict()
            reports.append({**report, "profile": "strict", "operation": {**report["operation"], "date": today}})
        self.registry = StatusRegistry.open(directory / "status")
        self.bodies: list[bytes] = []
        for i in range(credentials):
            report = reports[i % len(reports)]
            nonce = f"n-{i:08d}-{secrets.token_hex(6)}"
            index = self.registry.allocate(
                {"org_inn": report["organization"]["inn"], "verdict": "confirmed", "poa_numbers": ["load"]}
            )
            credential = attestation.issue(
                report,
                self.key,
                status=self.registry.credential_status(index),
                pseudonym_key=rep_key,
                audience=AUDIENCE,
                nonce=nonce,
            )
            request = {
                "organization_inn": report["organization"]["inn"],
                "operation": "PAYMENT_SIGN",
                "operation_date": today,
                "amount_rub": report["operation"]["amount_rub"],
                "representative_inn": report["requester"]["inn"],
                "audience": AUDIENCE,
                "nonce": nonce,
            }
            body = {"credential": credential, "request": request, "operation_risk": "payment"}
            self.bodies.append(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.refresh()

    def refresh(self) -> None:
        """Переподписать списки: окно свежести списка для платежа — около часа (MANDAT_STATUS_MAX_AGE_BY_RISK)."""
        self.registry.save(self.key)
        self.saved_at = time.time()

    def env(self) -> dict[str, str]:
        return {
            "MANDAT_ATTESTATION_KEYRING": str(self.directory / "keyring.json"),
            "MANDAT_STATUS_DIR": str(self.directory / "status"),
            "MANDAT_REPRESENTATIVE_KEY_FILE": str(self.directory / "representative.key"),
        }


class Server:
    """``mandat serve`` в режиме пилота с выбранным хранилищем и числом воркеров."""

    def __init__(
        self, workdir: Path, store: str, workers: int, users: Path, admission: Admission | None, extra: dict[str, str]
    ) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "MANDAT_ENV": "pilot",
            "MANDAT_API_USERS": str(users),
            # Лимит частоты включён, как в банке, но не ограничивает: весь поток идёт с одного адреса.
            "MANDAT_API_RATE_PER_MIN": "100000000",
            "MANDAT_API_STORE": store,
            "MANDAT_API_JOURNAL": str(workdir / "journal.jsonl"),
            "MANDAT_JOURNAL_ANCHOR": f"file:{workdir / 'anchor.jsonl'}",
            "MANDAT_JOURNAL_KEY": secrets.token_hex(32),
            "MANDAT_PII_KEY": secrets.token_hex(32),
            "MANDAT_STORE_KEY": secrets.token_hex(32),
            **(admission.env() if admission else {}),
            **extra,
        }
        env.pop("MANDAT_API_TOKEN", None)
        self.log = (workdir / "server.log").open("wb")
        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "mandat.cli", "serve", "--port", str(self.port), "--workers", str(workers)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self.log,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(self.base + "/readyz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        self.stop()
        tail = (workdir / "server.log").read_text(errors="replace")[-800:]
        raise RuntimeError(f"сервер не поднялся: {tail}")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def case_bodies(cases: int, mix: str) -> list[bytes]:
    """Пакеты потока по профилю, уже сериализованные: клиент не тратит процессор на JSON во время прогона."""
    from mandat.eval.synth import production_corpus

    return [json.dumps(s.inline_manifest(), ensure_ascii=False).encode("utf-8") for s in production_corpus(cases, mix)]


def arrivals(profile: str, rate: float, seconds: float, seed: int = 1) -> list[float]:
    """Моменты прихода запросов (с начала прогона): поток Пуассона с частотой по участкам профиля."""
    rng = random.Random(seed)  # noqa: S311 — расписание нагрузки, не криптография
    out: list[float] = []
    start = 0.0
    for share, factor in ARRIVALS[profile]:
        end = start + share * seconds
        t = start
        while True:
            t += rng.expovariate(rate * factor)
            if t >= end:
                break
            out.append(t)
        start = end
    return out


async def _drive(job: dict[str, Any]) -> dict[str, Any]:
    """Один клиентский процесс: открытая модель по расписанию или замкнутая с ``users`` клиентами."""
    bodies: list[bytes] = job["bodies"]
    headers = {"Authorization": f"Bearer {job['token']}", "Content-Type": "application/json"}
    limits = httpx.Limits(max_connections=job["connections"], max_keepalive_connections=job["connections"])
    timeout = httpx.Timeout(30.0, pool=None)
    lat: list[float] = []
    codes: Counter[str] = Counter()
    allowed = Counter[str]()
    t0 = time.monotonic() + (job["start_at"] - time.time())
    stop_at = t0 + job["seconds"]

    async with httpx.AsyncClient(base_url=job["base"], headers=headers, timeout=timeout, limits=limits) as client:

        async def one(body: bytes, due: float) -> None:
            try:
                r = await client.post(job["path"], content=body)
                code = str(r.status_code)
                if job["authorize"] and r.status_code == 200:
                    allowed["allowed" if r.json().get("allowed") else "denied"] += 1
            except httpx.HTTPError as exc:
                code = type(exc).__name__
            lat.append(time.monotonic() - due)
            codes[code] += 1

        await asyncio.sleep(max(0.0, t0 - time.monotonic()))
        if job["schedule"] is None:
            # Пакеты проверок идут по кругу; удостоверения — нет: nonce одноразовый, повтор дал бы отказ.
            counter = itertools.count() if not job["authorize"] else iter(range(len(bodies)))

            async def user() -> None:
                while time.monotonic() < stop_at:
                    i = next(counter, None)
                    if i is None:
                        return
                    await one(bodies[i % len(bodies)], time.monotonic())

            await asyncio.gather(*(user() for _ in range(job["users"])))
        else:
            tasks = []
            for i, offset in enumerate(job["schedule"]):
                due = t0 + offset
                await asyncio.sleep(max(0.0, due - time.monotonic()))
                tasks.append(asyncio.create_task(one(bodies[i % len(bodies)], due)))
            done, pending = await asyncio.wait(tasks, timeout=GRACE_SECONDS) if tasks else (set(), set())
            for task in pending:
                task.cancel()
            codes["not_served"] += len(pending)
    return {"lat": lat, "codes": dict(codes), "allowed": dict(allowed), "end": time.time()}


def _client_proc(job: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_drive(job))


def pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    return round(sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))] * 1000, 1)


def run_load(
    *,
    base: str,
    token: str,
    path: str,
    bodies: list[bytes],
    arrival: str,
    rate: float,
    seconds: float,
    concurrency: int,
    connections: int,
    client_procs: int,
    server_pid: int | None,
) -> dict[str, Any]:
    """Один прогон: пропускная способность, p50/p95/p99, доля ошибок, пиковый RSS сервера."""
    seconds = min(seconds, MAX_SECONDS)
    schedule = None if arrival == "closed" else arrivals(arrival, rate, seconds)
    procs = max(1, client_procs)
    start_at = time.time() + 1.0
    jobs = []
    for k in range(procs):
        share = bodies[k::procs]
        jobs.append(
            {
                "base": base,
                "token": token,
                "path": path,
                "bodies": share,
                "authorize": path.endswith("/authorize"),
                "schedule": None if schedule is None else schedule[k::procs],
                "users": max(1, concurrency // procs),
                "connections": max(1, connections // procs),
                "seconds": seconds,
                "start_at": start_at,
            }
        )
    sampler = RssSampler(server_pid)
    sampler.start()
    if procs == 1:
        parts = [_client_proc(jobs[0])]
    else:
        with multiprocessing.get_context("spawn").Pool(procs) as pool:
            parts = pool.map(_client_proc, jobs)
    peak_kb = sampler.stop()
    lat = sorted(x for part in parts for x in part["lat"])
    codes: Counter[str] = Counter()
    allowed: Counter[str] = Counter()
    for part in parts:
        codes.update(part["codes"])
        allowed.update(part["allowed"])
    total = sum(codes.values())
    ok = codes.get("200", 0)
    window = max(part["end"] for part in parts) - start_at
    result: dict[str, Any] = {
        "path": path,
        "arrival": arrival,
        "offered_rps": None if schedule is None else round(len(schedule) / seconds, 1),
        "concurrency": concurrency if schedule is None else connections,
        "seconds": seconds,
        "requests": total,
        "throughput_rps": round(ok / window, 1) if window > 0 else 0.0,
        "p50_ms": pct(lat, 0.50),
        "p95_ms": pct(lat, 0.95),
        "p99_ms": pct(lat, 0.99),
        "max_ms": pct(lat, 1.0),
        "error_rate": round((total - ok) / total, 4) if total else 0.0,
        "codes": dict(sorted(codes.items())),
        "peak_rss_mb": round(peak_kb / 1024, 1) if peak_kb else None,
    }
    if allowed:
        result["admission"] = dict(allowed)
    return result


def _machine() -> dict[str, Any]:
    cpu = "unknown"
    try:
        cpu = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
        mem_kb = int(Path("/proc/meminfo").read_text().split()[1])
    except (OSError, StopIteration, ValueError):
        mem_kb = 0
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        commit = ""
    return {
        "cpu": cpu,
        "vcpu": os.cpu_count(),
        "ram_gb": round(mem_kb / 1024 / 1024, 1),
        "os": platform.platform(),
        "python": platform.python_version(),
        "commit": commit,
    }


def _store_spec(kind: str, workdir: Path, redis: Redis | None, db: int) -> str:
    if kind == "sqlite":
        return f"sqlite:///{workdir / 'api.db'}"
    if kind == "redis":
        if redis is None:
            raise RuntimeError("нет Redis")
        return redis.url(db)
    return kind  # memory или готовый redis://…


PLAN = [
    # (поток, приход, множитель базовой частоты, клиентов для closed)
    ("cases", "closed", 0, 16),
    ("cases", "steady", 1, 0),
    ("cases", "morning_peak", 1, 0),
    ("cases", "month_end", 1, 0),
    ("authorize", "closed", 0, 32),
    ("authorize", "month_end", 1, 0),
]


def matrix(args: argparse.Namespace) -> dict[str, Any]:
    """SQLite и Redis × 1/2/4 воркера: проверки кейсов (closed, steady, утро ×3, конец месяца ×5) и допуск."""
    out: dict[str, Any] = {"machine": _machine(), "started": datetime.now(UTC).isoformat(timespec="seconds")}
    out["settings"] = {
        "cases_rate": args.rate,
        "authorize_rate": args.authorize_rate,
        "seconds": args.seconds,
        "connections": args.connections,
        "client_procs": args.client_procs,
        "credentials": args.credentials,
        "mix": args.mix,
        "corpus_cases": args.corpus_cases,
        "server": "MANDAT_ENV=pilot: users, journal+HMAC, sealed store, PII key, rate limiter on, admission",
    }
    out["runs"] = []
    with tempfile.TemporaryDirectory(prefix="mandat-load-") as tmp:
        work = Path(tmp)
        bodies = case_bodies(args.corpus_cases, args.mix)
        admission = Admission(work / "admission", args.credentials)
        operator, op_entry = _api_user("load-operator", "operator")
        dbo, dbo_entry = _api_user("load-dbo", "dbo", AUDIENCE)
        users = work / "users.json"
        users.write_text(json.dumps([op_entry, dbo_entry]))
        redis = Redis(work) if "redis" in args.stores else None
        try:
            db = 0
            for store in args.stores:
                for workers in args.workers_list:
                    db += 1
                    if time.time() - admission.saved_at > 1800:
                        admission.refresh()
                    spec = _store_spec(store, work / f"{store}-{workers}", redis, db)
                    server = Server(work / f"{store}-{workers}", spec, workers, users, admission, {})
                    slices = iter(_slices(admission.bodies, sum(1 for p in PLAN if p[0] == "authorize")))
                    try:
                        for flow, arrival, factor, users_n in PLAN:
                            authorize = flow == "authorize"
                            rate = (args.authorize_rate if authorize else args.rate) * factor
                            result = run_load(
                                base=server.base,
                                token=dbo if authorize else operator,
                                path="/v1/operations/authorize" if authorize else "/v1/cases",
                                bodies=next(slices) if authorize else bodies,
                                arrival=arrival,
                                rate=rate,
                                seconds=args.seconds,
                                concurrency=users_n,
                                connections=args.connections,
                                client_procs=args.client_procs,
                                server_pid=server.proc.pid,
                            )
                            result.update(store=store, workers=workers)
                            out["runs"].append(result)
                            print(_line(result), flush=True)
                    finally:
                        server.stop()
        finally:
            if redis is not None:
                redis.stop()
    return out


def _slices(items: list[bytes], parts: int) -> list[list[bytes]]:
    """Непересекающиеся части пула удостоверений: nonce одноразовый, у каждого прогона свои."""
    size = len(items) // max(1, parts)
    return [items[i * size : (i + 1) * size] for i in range(parts)]


def _line(r: dict[str, Any]) -> str:
    where = f"{r.get('store', '?')}/{r.get('workers', '?')}w"
    offered = f" offered={r['offered_rps']}" if r["offered_rps"] is not None else f" c={r['concurrency']}"
    return (
        f"{where} {r['path'].rsplit('/', 1)[-1]:9} {r['arrival']:12}{offered} rps={r['throughput_rps']} "
        f"p50={r['p50_ms']} p95={r['p95_ms']} p99={r['p99_ms']} err={r['error_rate']} rss={r['peak_rss_mb']}MB"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--matrix", action="store_true", help="SQLite и Redis × 1/2/4 воркера, оба потока")
    p.add_argument("--scenario", choices=["cases", "authorize"], default="cases")
    p.add_argument("--arrival", choices=["closed", *ARRIVALS], default="closed")
    p.add_argument("--rate", type=float, default=20.0, help="базовая частота проверок кейсов, запросов в секунду")
    p.add_argument("--authorize-rate", type=float, default=50.0, help="базовая частота допусков в матрице")
    p.add_argument("--seconds", type=float, default=15.0, help=f"длительность прогона, не больше {MAX_SECONDS:.0f}")
    p.add_argument("--concurrency", type=int, default=16, help="клиентов в замкнутой модели")
    p.add_argument("--connections", type=int, default=32, help="соединений клиента в открытой модели")
    p.add_argument("--client-procs", type=int, default=2, help="процессов клиента")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--workers-list", default="1,2,4", help="воркеры для --matrix")
    p.add_argument("--store", default="sqlite", help="sqlite, redis, memory или redis://…")
    p.add_argument("--stores", default="sqlite,redis", help="хранилища для --matrix")
    p.add_argument("--mix", default="vtb_prod")
    p.add_argument("--corpus-cases", type=int, default=3000, help="пакетов потока, по кругу")
    p.add_argument("--credentials", type=int, default=10_000, help="удостоверений в пуле допуска")
    p.add_argument("--base-url", help="готовый сервер вместо своего (только --scenario cases)")
    p.add_argument("--token", help="токен operator для --base-url")
    p.add_argument("--out", type=Path, help="JSON с результатами")
    args = p.parse_args(argv)
    args.stores = [s for s in args.stores.split(",") if s]
    args.workers_list = [int(x) for x in args.workers_list.split(",") if x]
    if args.matrix:
        result = matrix(args)
    elif args.base_url:
        if args.scenario != "cases" or not args.token:
            p.error("--base-url: только --scenario cases и с --token (удостоверения выпускает свой стенд)")
        run = run_load(
            base=args.base_url.rstrip("/"),
            token=args.token,
            path="/v1/cases",
            bodies=case_bodies(args.corpus_cases, args.mix),
            arrival=args.arrival,
            rate=args.rate,
            seconds=args.seconds,
            concurrency=args.concurrency,
            connections=args.connections,
            client_procs=args.client_procs,
            server_pid=None,
        )
        print(_line(run))
        result = {"machine": _machine(), "runs": [run]}
    else:
        with tempfile.TemporaryDirectory(prefix="mandat-load-") as tmp:
            work = Path(tmp)
            authorize = args.scenario == "authorize"
            admission = Admission(work / "admission", args.credentials) if authorize else None
            role = ("load-dbo", "dbo", AUDIENCE) if authorize else ("load-operator", "operator", None)
            token, entry = _api_user(*role)
            users = work / "users.json"
            users.write_text(json.dumps([entry]))
            redis = Redis(work) if args.store == "redis" else None
            try:
                spec = _store_spec(args.store, work, redis, 1)
                server = Server(work / "server", spec, args.workers, users, admission, {})
                try:
                    run = run_load(
                        base=server.base,
                        token=token,
                        path="/v1/operations/authorize" if authorize else "/v1/cases",
                        bodies=admission.bodies if admission else case_bodies(args.corpus_cases, args.mix),
                        arrival=args.arrival,
                        rate=args.rate,
                        seconds=args.seconds,
                        concurrency=args.concurrency,
                        connections=args.connections,
                        client_procs=args.client_procs,
                        server_pid=server.proc.pid,
                    )
                finally:
                    server.stop()
            finally:
                if redis is not None:
                    redis.stop()
            run.update(store=args.store, workers=args.workers)
            print(_line(run))
            result = {"machine": _machine(), "runs": [run]}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
