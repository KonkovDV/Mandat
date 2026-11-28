"""Ноутбук Kaggle: замер консультанта «Мандата» на модели в контуре — Ollama на видеокарте этой же машины.

Сценарий собирает ``scripts/kaggle/build.py``: подставляет упакованный проект и модель. Шаги: распаковка, зависимости
ядра (Python 3.11+, defusedxml, cryptography), установка Ollama, загрузка модели, прогрев, затем
``scripts/eval_advisory.py`` против Ollama на 127.0.0.1 с окружением из ``scripts/kaggle/eval.env``. К модели
обращается только замер, через ``eval_complete``; сам сценарий запускает команды установки и ``ollama``.
Итог — в /kaggle/working/out: summary.json (сводка), eval.json (по вызовам), table.md, ollama.log.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

PAYLOAD = "@@PAYLOAD@@"
MODEL = "@@MODEL@@"
WORKERS, TIMEOUT = 2, 120
WORK = Path("/kaggle/working")
OUT, PROJECT = WORK / "out", WORK / "mandat"
T0 = time.monotonic()


def sh(cmd: str, *, check: bool = True, env: dict[str, str] | None = None) -> str:
    print(f"\n[{time.monotonic() - T0:6.0f} с] $ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env, cwd=PROJECT)  # noqa: S602
    print(r.stdout[-4000:], r.stderr[-2000:], flush=True)
    if check and r.returncode:
        raise SystemExit(f"не удалось: {cmd}")
    return r.stdout


def timed(cmd: str, **kw: object) -> tuple[str, float]:
    t = time.monotonic()
    out = sh(cmd, **kw)  # type: ignore[arg-type]
    return out, round(time.monotonic() - t, 1)


def env_file(path: Path) -> dict[str, str]:
    rows = [x.split("=", 1) for x in path.read_text("utf-8").splitlines() if "=" in x and not x.startswith("#")]
    return {k.strip(): v.strip() for k, v in rows}


def python() -> str:
    """Интерпретатор для ядра: свой, если он 3.11+, иначе 3.11 через uv."""
    if sys.version_info >= (3, 11):  # noqa: UP036 — Python ноутбука может быть старше, чем нужен ядру
        sh(f"{sys.executable} -m pip install -q 'defusedxml>=0.7.1' 'cryptography>=45'")
        return sys.executable
    sh("pip install -q uv && uv venv -q -p 3.11 /kaggle/py311")
    sh("uv pip install -q -p /kaggle/py311/bin/python 'defusedxml>=0.7.1' 'cryptography>=45'")
    return "/kaggle/py311/bin/python"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)), mode="r:gz") as tar:
        tar.extractall(WORK, filter="data")
    gpu = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", check=False).strip()
    py = python()
    sh("apt-get -qq update > /dev/null && apt-get -qq install -y zstd > /dev/null", check=False)  # нужен установщику
    sh("curl -fsSL https://ollama.com/install.sh | sh")
    version = sh("ollama --version", check=False).strip().split()[-1:]
    server_env = dict(os.environ, OLLAMA_CONTEXT_LENGTH="8192", OLLAMA_KEEP_ALIVE="2h")
    server_env["OLLAMA_NUM_PARALLEL"] = str(WORKERS)
    with (OUT / "ollama.log").open("w") as log:
        server = subprocess.Popen(["ollama", "serve"], stdout=log, stderr=subprocess.STDOUT, env=server_env)  # noqa: S607
        for _ in range(120):
            if subprocess.run(["ollama", "list"], capture_output=True).returncode == 0:  # noqa: S607
                break
            time.sleep(1)
        _, pull_s = timed(f"ollama pull {MODEL}")
        _, warm_s = timed(f"ollama run {MODEL} 'Ответь одним словом: готов'")
        env = dict(os.environ, PYTHONPATH="src", **env_file(PROJECT / "scripts/kaggle/eval.env"))
        cmd = f"{py} scripts/eval_advisory.py --models {MODEL} --workers {WORKERS} --timeout {TIMEOUT}"
        table, eval_s = timed(f"{cmd} --out {OUT}/eval.json", env=env)
        ps = sh("ollama ps", check=False).strip().splitlines()
        server.terminate()
    (OUT / "table.md").write_text(table, "utf-8")
    result = json.loads((OUT / "eval.json").read_text("utf-8"))[0]
    repo, _, quant = MODEL.removeprefix("hf.co/").partition(":")
    summary = {
        "model": repo.removesuffix("-GGUF"),
        "weights": MODEL,
        "quantization": quant,
        "server": f"Ollama {' '.join(version)}".strip(),
        "gpu": gpu,
        "date": time.strftime("%Y-%m-%d", time.gmtime()),
        "charter": {k: result["charter"][k] for k in ("tp", "fp", "fn", "precision", "recall", "errors")},
        "explain": result["explain"],
        "median_s": result["median_s"],
        "calls": result["calls"],
        "workers": WORKERS,
        "timeout_s": TIMEOUT,
        "seconds": {"pull": pull_s, "warmup": warm_s, "benchmark": eval_s},
        "ollama_ps": ps,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    shutil.rmtree(PROJECT, ignore_errors=True)  # в выводе ноутбука — только out/


if __name__ == "__main__":
    main()
