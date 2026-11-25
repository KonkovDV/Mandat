"""Снимает настоящий вывод демо для видео: команды CLI и ответ HTTP API.

    python submission/video/capture.py      # из корня репозитория, пакет установлен (make install)

Пишет submission/video/out/outputs.json: команда, код выхода и вывод каждого шага. Порядок шагов —
как в сценарии показа docs/DEMO.md: демо-кейсы, отмена основной МЧД, захват, журнал, захват до платежа
(mandat showcase), запрос через API и допуск платежа по OpenID AuthZEN 1.0.

Для допуска по AuthZEN нужен день операции «сегодня»: сервер сверяет его с часами. Кейс 14 копируется в var/video
со сдвигом всех дат на одно и то же число дней, проверяется ядром, по отчёту выпускается удостоверение на nonce,
а `mandat serve` с этой связкой ключей отвечает на настоящий curl. Ключи стенда живут только в var/video.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"
WORK = ROOT / "var" / "video"
AUTHZEN_CASE = "14_old_director_charter_cleared"
AUDIENCE = "bank-dbo-msb"
STATUS = ["--public-key-file", "var/demo/public_key.txt", "--status-dir", "var/demo/status"]

STEPS = [
    ("demo", ["mandat", "demo"]),
    ("verify_before", ["mandat", "attest-verify", "var/demo/attestations/01_ok.json", *STATUS]),
    ("monitor_revoked", ["mandat", "monitor", "examples/events/poa_revoked.json", "--status-dir", "var/demo/status"]),
    ("verify_revoked", ["mandat", "attest-verify", "var/demo/attestations/03_subdelegation_ok.json", *STATUS]),
    ("monitor_takeover", ["mandat", "monitor", "examples/events/takeover.json", "--status-dir", "var/demo/status"]),
    # Без --status-dir: сроки показываются тревогами и не меняют статусы удостоверений стенда для следующих сцен.
    ("monitor_expiring", ["mandat", "monitor", "examples/events/poa_expiring.json"]),
    ("verify_after", ["mandat", "attest-verify", "var/demo/attestations/01_ok.json", *STATUS]),
    ("journal_verify", ["mandat", "journal-verify", "var/demo/journal.jsonl"]),
    ("journal_proof", ["mandat", "journal-proof", "var/demo/journal.jsonl", "--seq", "3"]),
    ("showcase", ["mandat", "showcase", "--out", "var/showcase"]),
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


@contextmanager
def serving(extra_env: dict[str, str]) -> Iterator[tuple[int, str]]:
    """Поднять `mandat serve` на свободном порту стенда; отдаёт порт и токен, после блока сервер гасится."""
    token = secrets.token_urlsafe(32)
    port = free_port()
    env = {**os.environ, "MANDAT_ENV": "demo", "MANDAT_API_TOKEN": token, **extra_env}
    server = subprocess.Popen(  # noqa: S603
        [str(Path(sys.executable).with_name("mandat")), "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)  # noqa: S310
                break
            except OSError:
                time.sleep(0.2)
        yield port, token
    finally:
        server.terminate()
        server.wait(timeout=10)


def curl(shown: list[str], port: int, token: str, files: dict[str, Path]) -> tuple[int, dict[str, object]]:
    """Выполнить показанную команду curl: адрес, токен и файл тела подставляются только в настоящий вызов."""
    real = []
    for arg in shown:
        arg = arg.replace("8000", str(port)).replace("$MANDAT_API_TOKEN", token)
        for name, path in files.items():
            arg = arg.replace(f"@{name}", f"@{path}")
        real.append(arg)
    done = subprocess.run(  # noqa: S603
        [*real, "-w", "\n%{http_code}"], capture_output=True, text=True, timeout=60, check=False
    )
    if done.returncode != 0:
        raise SystemExit(f"curl: код {done.returncode}: {done.stderr.strip()}")
    payload, _, code = done.stdout.rpartition("\n")
    return int(code), json.loads(payload)


def posted(path: str, body: str) -> list[str]:
    return [
        "curl",
        "-s",
        "-X",
        "POST",
        f"http://127.0.0.1:8000{path}",
        "-H",
        "Authorization: Bearer $MANDAT_API_TOKEN",
        "-H",
        "Content-Type: application/json",
        "--data-binary",
        f"@{body}",
    ]


def api_step(case: str) -> dict[str, object]:
    """POST /v1/cases через curl на поднятый `mandat serve`: в видео показана ровно эта команда."""
    body = OUT / f"{case}.json"
    body.write_text(json.dumps(api_case(case), ensure_ascii=False), encoding="utf-8")
    shown = posted("/v1/cases", f"{case}.json")
    with serving({}) as (port, token):
        code, report = curl(shown, port, token, {f"{case}.json": body})
    return {"cmd": " ".join(shown), "code": code, "report": report}


ISO_DAY = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def case_for_today(case: str, target: Path) -> tuple[date, int]:
    """Копия кейса, где все даты сдвинуты так, чтобы день операции пришёлся на сегодня по Москве.

    Сдвиг одинаковый для МЧД, ЕГРЮЛ, устава и манифеста: сроки и порядок событий не меняются.
    """
    from mandat.audit.attestation import BANK_TZ

    source = ROOT / "examples" / "cases" / case
    manifest = json.loads((source / "case.json").read_text(encoding="utf-8"))
    today = datetime.now(UTC).astimezone(BANK_TZ).date()
    shift = today - date.fromisoformat(manifest["operation"]["date"])
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for file in source.iterdir():
        text = file.read_text(encoding="utf-8")
        moved = ISO_DAY.sub(lambda m: (date.fromisoformat(m.group(1)) + shift).isoformat(), text)
        (target / file.name).write_text(moved, encoding="utf-8")
    return today, shift.days


def authzen_step() -> dict[str, object]:
    """Допуск платежа по OpenID AuthZEN 1.0: первый запрос — decision true, повтор того же nonce — false."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from mandat.audit import attestation
    from mandat.audit.status_list import StatusRegistry
    from mandat.intake.case_loader import load_case_dir
    from mandat.services.verify import verify_case

    work = WORK / "authzen"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    today, shifted = case_for_today(AUTHZEN_CASE, work / "case")
    report = verify_case(load_case_dir(work / "case"), now=datetime.now(UTC)).as_dict()
    if report["verdict"] != "confirmed":
        raise SystemExit(f"{AUTHZEN_CASE} на {today}: {report['verdict']}, а для допуска нужно «подтверждено»")
    key = Ed25519PrivateKey.generate()
    registry = StatusRegistry.open(work / "status")
    representative_key = attestation.load_or_create_pseudonym_key(work / "representative.key")
    index = registry.allocate(
        {
            "case_id": report["case_id"],
            "org_inn": report["organization"]["inn"],
            "verdict": report["verdict"],
            "poa_numbers": report["chain"]["poa_numbers"],
            "chain_kind": report["chain"]["kind"],
        }
    )
    nonce = f"dbo-payment-{secrets.token_hex(8)}"
    credential = attestation.issue(
        report,
        key,
        status=registry.credential_status(index),
        pseudonym_key=representative_key,
        audience=AUDIENCE,
        nonce=nonce,
    )
    registry.save(key)
    keyring = work / "keyring.json"
    keyring.write_text(
        json.dumps({"keys": [{"kid": attestation.public_key_multibase(key), "status": "active"}]}), encoding="utf-8"
    )
    operation = report["operation"]
    evaluation = {
        "subject": {"type": "representative", "id": report["requester"]["inn"]},
        "resource": {"type": "organization", "id": report["organization"]["inn"]},
        "action": {
            "name": operation["code"],
            "properties": {"operation_date": operation["date"], "amount_rub": operation["amount_rub"]},
        },
        "context": {"credential": credential, "nonce": nonce, "audience": AUDIENCE, "operation_risk": "payment"},
    }
    body = work / "evaluation.json"
    body.write_text(json.dumps(evaluation, ensure_ascii=False), encoding="utf-8")
    shown = posted("/access/v1/evaluation", "evaluation.json")
    env = {
        "MANDAT_ATTESTATION_KEYRING": str(keyring),
        "MANDAT_STATUS_DIR": str(work / "status"),
        "MANDAT_REPRESENTATIVE_KEY_FILE": str(work / "representative.key"),
    }
    with serving(env) as (port, token):
        first_code, first = curl(shown, port, token, {"evaluation.json": body})
        replay_code, replay = curl(shown, port, token, {"evaluation.json": body})
    if first.get("decision") is not True or replay.get("decision") is not False:
        raise SystemExit(f"AuthZEN: ожидались допуск и отказ повтору, получено {first} и {replay}")
    return {
        "cmd": " ".join(shown),
        "code": first_code,
        "answer": first,
        "replay_code": replay_code,
        "replay": replay,
        "request": {k: v for k, v in evaluation.items() if k != "context"},
        "shifted_days": shifted,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    out = {name: run(cmd) for name, cmd in STEPS}
    out["api"] = api_step("05_director_changed")
    out["authzen"] = authzen_step()
    (OUT / "outputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, step in out.items():
        print(f"{name}: код {step['code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
