"""Снимок классификатора полномочий из выгрузки справочника ЕСНСИ или JSON ФНС.

    python scripts/import_classifier.py выгрузка.csv --out src/mandat/data/authority_classifier.json
    python scripts/import_classifier.py GetAllRecords.json --out снимок.json   # JSON ФНС, kpmp.nalog.gov.ru

Команда читает только файл и в сеть не ходит: выгрузку ФНС скачивают отдельно (docs/CLASSIFIER.md). Формат ФНС
определяется по полю classRecords.

Выгрузка — CSV (разделитель «;» или «,») или JSON-массив с полями CODE, MNEMONIC, NAME,
STARTED, REVOKED. В снимок записываются источник и его sha256: по ним видно, из какой
выгрузки взяты коды. Коды вручную не добавляются.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mandat.rules.classifier import import_esnsi, import_kpmp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = Path(args.source).read_bytes()
    source = f"{Path(args.source).name} sha256={hashlib.sha256(raw).hexdigest()}"
    is_kpmp = raw.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{") and b'"classRecords"' in raw
    payload = import_kpmp(raw, source=source) if is_kpmp else import_esnsi(raw, source=source)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"записей: {len(payload['entries'])} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
