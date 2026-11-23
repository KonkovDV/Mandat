"""Затравочный корпус для фазз-целей: настоящие файлы демо-кейсов из examples/cases.

ClusterFuzzLite берёт архив ``<цель>_seed_corpus.zip`` из каталога сборки. Манифест для ``fuzz_case``
собирается так же, как его присылает API: документы лежат строкой в поле ``content``.

    python fuzz/seed_corpus.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = sorted(p for p in (ROOT / "examples" / "cases").iterdir() if p.is_dir())


def inline_manifest(case_dir: Path) -> bytes:
    manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    for doc in manifest.get("documents", []):
        path = doc.pop("path", None)
        if path:
            doc["content"] = (case_dir / path).read_text(encoding="utf-8")
    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def seeds() -> dict[str, list[tuple[str, bytes]]]:
    out: dict[str, list[tuple[str, bytes]]] = {
        "fuzz_emchd": [],
        "fuzz_egrul": [],
        "fuzz_privacy": [],
        "fuzz_case": [],
        "fuzz_monitoring": [(p.name, p.read_bytes()) for p in sorted((ROOT / "examples" / "events").glob("*.json"))],
    }
    for case in CASES:
        for xml in sorted(case.glob("*.xml")):
            out["fuzz_emchd"].append((f"{case.name}-{xml.name}", xml.read_bytes()))
        for name, target in (("egrul.json", "fuzz_egrul"), ("charter.txt", "fuzz_privacy")):
            if (case / name).is_file():
                out[target].append((f"{case.name}-{name}", (case / name).read_bytes()))
        out["fuzz_case"].append((f"{case.name}.json", inline_manifest(case)))
    return out


def main(target_dir: str) -> int:
    out = Path(target_dir)
    for fuzzer, files in seeds().items():
        with zipfile.ZipFile(out / f"{fuzzer}_seed_corpus.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                archive.writestr(name, data)
        print(f"{fuzzer}: {len(files)} файлов")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
