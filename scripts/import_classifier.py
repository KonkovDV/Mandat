"""Снимок классификатора полномочий из выгрузки справочника ЕСНСИ.

    python scripts/import_classifier.py выгрузка.csv --out src/mandat/data/authority_classifier.json

Выгрузка — CSV (разделитель «;» или «,») или JSON-массив с полями CODE, MNEMONIC, NAME,
STARTED, REVOKED. В снимок записываются источник и его sha256: по ним видно, из какой
выгрузки взяты коды. Коды вручную не добавляются.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mandat.rules.classifier import import_esnsi


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = Path(args.source).read_bytes()
    source = f"{Path(args.source).name} sha256={hashlib.sha256(raw).hexdigest()}"
    payload = import_esnsi(raw, source=source)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"записей: {len(payload['entries'])} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
