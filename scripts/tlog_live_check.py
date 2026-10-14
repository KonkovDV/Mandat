"""Ручная проверка на живых журналах Sigstore (сеть; в CI не запускается).

Rekor v2 (log2025-1): два checkpoint подряд, подпись Ed25519, доказательство согласованности по тайлам.
Rekor v1: последняя запись — SET, включение, checkpoint; согласованность через /api/v1/log/proof.
Ключи журналов — из TrustedRoot Sigstore (TUF, sigstore/root-signing).

    python scripts/tlog_live_check.py [--wait 15]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from mandat.audit import anchor, tlog  # noqa: E402

TRUSTED_ROOT = "https://raw.githubusercontent.com/sigstore/root-signing/main/targets/trusted_root.json"


def _open(request: urllib.request.Request) -> Any:
    return urllib.request.urlopen(request, timeout=30)  # noqa: S310 - адреса фиксированы выше


def _keys() -> dict[str, bytes]:
    root = json.loads(tlog.read_url(_open, TRUSTED_ROOT))
    out = {}
    for item in root["tlogs"]:
        der = base64.b64decode(item["publicKey"]["rawBytes"])
        pem = serialization.load_der_public_key(der).public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        out[item["baseUrl"]] = pem
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=15.0, help="пауза между checkpoint v2, с")
    args = parser.parse_args(argv)
    keys = _keys()
    v2 = next(u for u in keys if "log20" in u)
    origin = v2.split("://", 1)[1]
    state = tlog.CheckpointState()
    fetched: list[str] = []

    def fetch(path: str) -> bytes:
        fetched.append(path)
        return tlog.read_url(_open, f"{v2}/{path}")

    for _ in range(2):
        cp = tlog.verify_checkpoint(fetch("checkpoint").decode(), keys[v2], origin=origin)
        problem = state.observe(*cp, tlog.tile_prover(fetch))
        print(f"v2 {origin}: размер {cp[1]}, согласованность: {problem or 'ok'}")
        if problem:
            return 1
        time.sleep(args.wait)
    print(f"v2: тайлов загружено {len(fetched) - 2}")

    v1 = "https://rekor.sigstore.dev"
    sink = anchor.RekorSink(v1, b"", _open, log_key_pem=keys[v1], state=tlog.CheckpointState())
    info = sink._get("/api/v1/log")
    index = int(info["treeSize"]) - 1
    entries = sink._get(f"/api/v1/log/entries?logIndex={index}")
    uuid, entry = next(iter(entries.items()))
    entry["logIndex"] = int(entry["logIndex"])
    _, checkpoint = anchor._verify_rekor_entry(uuid, entry, keys[v1])
    print(f"v1: запись {entry['logIndex']}: SET, включение и checkpoint ({checkpoint[1]}) — ok")
    problem = sink.consistency_problem(checkpoint)
    print(f"v1: согласованность с текущим checkpoint: {problem or 'ok'}")
    return 1 if problem else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
