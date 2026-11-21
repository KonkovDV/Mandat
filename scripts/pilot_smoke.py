"""Проверка пилотного compose: создать кейс, перезапустить контейнер, убедиться, что кейс и журнал на месте.

Запускается из CI после ``docker compose up --wait``. Только стандартная библиотека и mandat.eval.synth.
Это smoke-проверка персистентности тома, а не доказательство отказоустойчивости или exactly-once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "deploy/compose.pilot.yml"]


def call(path: str, *, data: dict | None = None, token: str = "") -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, headers=headers)  # noqa: S310 - фиксированный адрес loopback
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    token = os.environ["MANDAT_API_TOKEN"]
    from mandat.eval.synth import build

    status, ready = call("/readyz")
    assert status == 200 and ready["status"] == "ready", (status, ready)
    assert call("/health")[0] == 200

    manifest = build("ok", seed=4).inline_manifest()
    status, report = call("/v1/cases?profile=strict", data=manifest, token=token)
    assert status == 200, (status, report)
    case_id = report["case_id"]

    subprocess.run([*COMPOSE, "restart", "--no-deps", "mandat"], check=True)
    subprocess.run([*COMPOSE, "up", "--wait", "--wait-timeout", "120"], check=True)

    status, again = call(f"/v1/cases/{case_id}", token=token)
    assert status == 200 and again["case_id"] == case_id, (status, again)
    status, ready = call("/readyz")
    assert status == 200, (status, ready)
    journal = subprocess.run(
        [*COMPOSE, "exec", "-T", "mandat", "python", "-c", "print(sum(1 for _ in open('/app/var/journal.jsonl')))"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert int(journal) >= 1, journal
    print(f"ok: кейс {case_id} и журнал ({journal} зап.) пережили рестарт контейнера")
    return 0


if __name__ == "__main__":
    sys.exit(main())
