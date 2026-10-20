# Lock-файлы зависимостей

Файлы с хешами (`--require-hashes`), общие для Python 3.11 и новее (`--universal`); CI ставит их на 3.11–3.14.

- `runtime.lock` — ядро и extra `[api]`, ставится в Docker-образ.
- `dev.lock` — всё для CI: `[dev,api,cedar,redis]` (в `dev` входит `tuf` для тестов `audit/trust.py`).
- `pip.lock` — сам pip для CI, Docker и run-demo.
- `tools.lock` — build, cyclonedx-bom, pip-audit для CI; ставится в отдельное окружение.
- `build.lock` — сборочный бэкенд `setuptools` из `build.in`; пакет ставится с `--no-build-isolation`, иначе pip скачал бы бэкенд без хешей.

Пересборка после изменения `pyproject.toml`:

```bash
uv pip compile pyproject.toml --extra api --universal --python-version 3.11 --generate-hashes -o requirements/runtime.lock
uv pip compile pyproject.toml --extra dev --extra api --extra cedar --extra redis --universal --python-version 3.11 --generate-hashes -o requirements/dev.lock
uv pip compile requirements/pip.in --universal --python-version 3.11 --generate-hashes -o requirements/pip.lock
uv pip compile requirements/tools.in --universal --python-version 3.11 --generate-hashes -o requirements/tools.lock
uv pip compile requirements/build.in --universal --python-version 3.11 --generate-hashes -o requirements/build.lock
```

Сам пакет ставится поверх без зависимостей: `pip install --no-deps --no-build-isolation -e .`.

## Dependabot

Dependabot видит `pyproject.toml`, но не эти lock-файлы: в [#117](https://github.com/KonkovDV/Mandat/pull/117) он поднял `cedarpy` только в `pyproject.toml`, а `dev.lock` остался на прежней версии. Такой PR сам по себе ничего не обновляет: CI ставит зависимости из lock-файла. Перед влитием в ту же ветку добавляется пересборка lock-файлов командами выше. Для `cedarpy` версия закреплена точно, и дифференциальный тест `tests/test_cedar.py` должен пройти на новой версии.
