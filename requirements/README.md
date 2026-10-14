# Lock-файлы зависимостей

Файлы с хешами (`--require-hashes`), общие для Python 3.11–3.13 (`--universal`).

- `runtime.lock` — ядро и extra `[api]`, ставится в Docker-образ.
- `dev.lock` — всё для CI: `[dev,api,cedar,redis]`.
- `build.lock` — сборочный бэкенд `setuptools` из `build.in`; пакет ставится с `--no-build-isolation`, иначе pip скачал бы бэкенд без хешей.

Пересборка после изменения `pyproject.toml`:

```bash
uv pip compile pyproject.toml --extra api --universal --python-version 3.11 --generate-hashes -o requirements/runtime.lock
uv pip compile pyproject.toml --extra dev --extra api --extra cedar --extra redis --universal --python-version 3.11 --generate-hashes -o requirements/dev.lock
uv pip compile requirements/build.in --universal --python-version 3.11 --generate-hashes -o requirements/build.lock
```

Сам пакет ставится поверх без зависимостей: `pip install --no-deps --no-build-isolation -e .`.
