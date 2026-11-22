#!/bin/bash -eu
# Сборка фазз-целей fuzz/fuzz_*.py для ClusterFuzzLite. Зависимости ставятся только из lock-файла с хешами,
# atheris уже есть в образе. Код «Мандата» берётся из src без установки пакета, данные пакета (правила, схемы,
# разметка МЧД) PyInstaller забирает в бинарник целиком.
pip3 install --require-hashes -r requirements/runtime.lock
export PYTHONPATH="$SRC/mandat/src:$SRC/mandat/fuzz"
for fuzzer in fuzz/fuzz_*.py; do
  compile_python_fuzzer "$fuzzer" --collect-data mandat
done
python3 fuzz/seed_corpus.py "$OUT"
