"""Собрать приватный ноутбук Kaggle: замер консультанта на модели в контуре (Ollama на видеокарте T4).

    python scripts/kaggle/build.py --user <аккаунт Kaggle>
    kaggle kernels push -p var/kaggle
    kaggle kernels status <аккаунт>/mandat-onprem-eval
    kaggle kernels output <аккаунт>/mandat-onprem-eval -p var/kaggle_out

В сценарий ноутбука упаковываются пакет ``src/mandat``, замер ``scripts/eval_advisory.py`` с его наборами
(``examples/advisory``, ``examples/cases``), ``pyproject.toml`` и окружение прогона ``scripts/kaggle/eval.env``.
Данные синтетические; ноутбук приватный, с T4 и интернетом (установка Ollama, загрузка модели). Итог прогона —
``out/summary.json`` в выводе ноутбука, он кладётся в ``docs/eval/``. Ключ Kaggle в файлы не пишется.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARTS = (
    "src/mandat",
    "scripts/eval_advisory.py",
    "scripts/kaggle/eval.env",
    "examples/advisory",
    "examples/cases",
    "pyproject.toml",
)
MODEL = "hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M"
SLUG = "mandat-onprem-eval"


def payload() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for part in PARTS:
            path = ROOT / part
            for f in sorted([path] if path.is_file() else path.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    tar.add(f, arcname=f"mandat/{f.relative_to(ROOT).as_posix()}")
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="аккаунт Kaggle, владелец ноутбука")
    ap.add_argument("--model", default=MODEL, help="модель Ollama (hf.co/<репозиторий GGUF>:<квантование>)")
    ap.add_argument("--out", default=str(ROOT / "var" / "kaggle"))
    args = ap.parse_args()
    template = (Path(__file__).parent / "template.py").read_text("utf-8")
    code = template.replace("@@PAYLOAD@@", base64.b64encode(payload()).decode()).replace("@@MODEL@@", args.model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{SLUG}.py").write_text(code, "utf-8")
    meta = {
        "id": f"{args.user}/{SLUG}",
        "title": SLUG,
        "code_file": f"{SLUG}.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,
        "machine_shape": "NvidiaTeslaT4",
        "dataset_sources": [],
        "kernel_sources": [],
        "competition_sources": [],
        "model_sources": [],
    }
    (out / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), "utf-8")
    print(f"{out}: {SLUG}.py {len(code) // 1024} КБ, модель {args.model}")


if __name__ == "__main__":
    main()
