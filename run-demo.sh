#!/usr/bin/env sh
# Демо без установки в систему: виртуальное окружение в .venv, отчёты в var/demo.
set -eu
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e .
mandat demo
mandat monitor examples/events/takeover.json
echo "Откройте var/demo/01_ok.html и var/demo/05_director_changed.html в браузере."
