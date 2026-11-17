#!/usr/bin/env bash
# R5.1 wrap-up: тесты → evidence.
# Использование: ./scripts/finish.sh
# PR и merge — отдельно через gh после зелёного CI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

echo "== pytest =="
"$PY" -m pytest -q -o addopts= -p no:warnings --no-cov
echo "== evidence =="
MANDAT_EVIDENCE_BUILDING=1 "$PY" scripts/build_evidence.py
echo "== claims lint =="
"$PY" scripts/lint_claims.py || true
echo "Готово. Дальше: commit, gh pr create, дождаться CI, squash-merge."
