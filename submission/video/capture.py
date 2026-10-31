"""Снимает настоящий вывод демо для видео: команды CLI и ответ HTTP API.

    python submission/video/capture.py      # из корня репозитория, пакет установлен (make install)

Пишет submission/video/out/outputs.json: команда, код выхода и вывод каждого шага. Порядок шагов —
как в сценарии показа docs/DEMO.md: демо-кейсы, отмена основной МЧД, захват, журнал, допуск через API.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"
STATUS = ["--public-key-file", "var/demo/public_key.txt", "--status-dir", "var/demo/status"]

STEPS = [
    ("demo", ["mandat", "demo"]),
    ("verify_before", ["mandat", "attest-verify", "var/demo/attestations/01_ok.json", *STATUS]),
    ("monitor_revoked", ["mandat", "monitor", "examples/events/poa_revoked.json", "--status-dir", "var/demo/status"]),
    ("verify_revoked", ["mandat", "attest-verify", "var/demo/attestations/03_subdelegation_ok.json", *STATUS]),
    ("monitor_takeover", ["mandat", "monitor", "examples/events/takeover.json", "--status-dir", "var/demo/status"]),
    ("verify_after", ["mandat", "attest-verify", "var/demo/attestations/01_ok.json", *STATUS]),
    ("journal_verify", ["mandat", "journal-verify", "var/demo/journal.jsonl"]),
    ("journal_proof", ["mandat", "journal-proof", "var/demo/journal.jsonl", "--seq", "3"]),
]


def run(cmd: list[str]) -> dict[str, object]:
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)  # noqa: S603
    return {"cmd": " ".join(cmd), "code": done.returncode, "out": (done.stdout + done.stderr).rstrip("\n")}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def api_case(case: str) -> dict[str, object]:
    """Манифест кейса для POST /v1/cases: документы из каталога кейса передаются содержимым, а не путём."""
    folder = ROOT / "examples" / "cases" / case
    manifest = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    for doc in manifest["documents"]:
        doc["content"] = (folder / doc.pop("path")).read_text(encoding="utf-8")
    for key in ("title", "expected_verdict"):
        manifest.pop(key, None)
    return manifest


def api_step(case: str) -> dict[str, object]:
    """POST /v1/cases через curl на поднятый `mandat serve`: в видео показана ровно эта команда."""
    token = secrets.token_urlsafe(32)
    port = free_port()
    env = {**os.environ, "MANDAT_ENV": "demo", "MANDAT_API_TOKEN": token}
    body = OUT / f"{case}.json"
    body.write_text(json.dumps(api_case(case), ensure_ascii=False), encoding="utf-8")
    server = subprocess.Popen(  # noqa: S603
        [str(Path(sys.executable).with_name("mandat")), "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shown = [
        "curl",
        "-s",
        "-X",
        "POST",
        "http://127.0.0.1:8000/v1/cases",
        "-H",
        "Authorization: Bearer $MANDAT_API_TOKEN",
        "-H",
        "Content-Type: application/json",
        "--data-binary",
        f"@{case}.json",
    ]
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)  # noqa: S310
                break
            except OSError:
                time.sleep(0.2)
        real = [
            a.replace("8000", str(port)).replace("$MANDAT_API_TOKEN", token).replace(f"@{case}.json", f"@{body}")
            for a in shown
        ]
        done = subprocess.run(  # noqa: S603
            [*real, "-w", "\n%{http_code}"], capture_output=True, text=True, timeout=60, check=False
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
    if done.returncode != 0:
        raise SystemExit(f"curl: код {done.returncode}: {done.stderr.strip()}")
    payload, _, code = done.stdout.rpartition("\n")
    return {"cmd": " ".join(shown), "code": int(code), "report": json.loads(payload)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    out = {name: run(cmd) for name, cmd in STEPS}
    out["api"] = api_step("05_director_changed")
    (OUT / "outputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, step in out.items():
        print(f"{name}: код {step['code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
