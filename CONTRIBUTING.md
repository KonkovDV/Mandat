# Как вносить изменения

Здесь — как предложить правку, какие проверки пройти и как оформить коммит. Что сделано и что осталось — в [дорожной карте](docs/ROADMAP.md). Правила общения — в [кодексе поведения](CODE_OF_CONDUCT.md).

## Порядок работы

1. Заведите issue по шаблону: ошибка или предложение. Об уязвимостях не пишут в issues, порядок описан в [SECURITY.md](SECURITY.md).
2. Работайте в отдельной ветке от свежего `main`. Прямых коммитов в `main` нет, PR поверх другого PR не открывается.
3. Один PR — одна задача. Откройте его по шаблону и напишите в описании `Closes #N`.
4. В `main` вливает владелец ([CODEOWNERS](.github/CODEOWNERS)), и только PR с зелёным CI. Защита ветки требует свежие проверки `analysis`, `docker`, `package` и `test` на Python 3.11, 3.12 и 3.13. Влитую ветку удаляет владелец.
5. Правка правила (`src/mandat/data/rules.json`, `src/mandat/rules/checks.py`, Cedar в `policies/`) идёт со ссылкой на норму и с тестом. Утверждает правило только подписанная запись юриста банка ([ADR-009](docs/adr/ADR-009-rule-approval-records.md)): строка `status` в `rules.json` статус не меняет.

## Проверки перед PR

Команды повторяют CI (`.github/workflows/ci.yml`). Зависимости ставятся только из lock-файлов с хешами, как в CI. `make install` делает то же, `make check` — короче: линтеры, `pytest` без покрытия, сверка утверждений и ссылок, но без `--cov` и `--verify-source`.

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install --require-hashes -r requirements/pip.lock
python -m pip install --require-hashes -r requirements/dev.lock -r requirements/build.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m ruff check src tests scripts tools
python -m ruff format --check src tests scripts tools
python -m mypy
python -m pytest --cov=mandat
python scripts/lint_claims.py
python scripts/check_links.py
python scripts/build_evidence.py --verify-source
```

- Правка в `src`, `tests`, `scripts` или `examples` меняет хеш исходников в `evidence.json`. Руками его не правят: пересборка — `make evidence` на Linux (Docker или WSL), иначе поедут версия Python и замеры скорости. `build_evidence.py --verify-source` сверяет хеш исходников и число собранных тестов.
- На Windows без WSL и Docker гоняйте затронутые тесты, полный прогон делает CI (задача `windows`).
- CI сверх этого проверяет, что ни один тест не пропущен (`scripts/check_no_skips.py`), ищет известные уязвимости в зависимостях (`pip-audit`) и в образе (Trivy), проверяет рабочие процессы `actionlint`. Пороги и что блокирует влитие — в [SECURITY.md](SECURITY.md#зависимости-и-сканеры). Если `pip-audit` красный, пакет поднимают `uv pip compile --upgrade-package` и обновляют lock; проверку не отключают.
- Числа в документах сверяются с [реестром утверждений](CLAIMS_REGISTRY.md). Формулировки из [BANNED_CLAIMS.txt](BANNED_CLAIMS.txt) не используются даже с отрицанием: `lint_claims.py` ищет подстроку.
- Секреты, токены и реальные персональные данные не попадают ни в код, ни в логи, ни в коммиты. В отслеживаемых файлах нет трейлеров соавторства и локальных временных путей: это проверяет `test_no_coauthor_trailers_or_local_paths_in_repository`.

## Сборка пакета

Так же, как задача `package` в CI, на Python 3.12. У файлов в `dist/` нет подписи: подписанный артефакт есть только у прогона `main` ([как проверить](SECURITY.md#проверить-пакет)).

```bash
python -m pip install --require-hashes -r requirements/tools.lock
python -m pip install --require-hashes -r requirements/build.lock
python -m build --no-isolation
```

`build` берётся из `tools.lock`, бэкенд `setuptools` — из `build.lock`. Без `--no-isolation` pip скачал бы бэкенд без хеша.

## Коммиты

Сообщения — по [соглашению о коммитах](https://www.conventionalcommits.org/ru/v1.0.0/): `fix: …`, `feat: …`, `docs: …`, область в скобках по желанию (`fix(api): …`). Один коммит — одно логическое изменение; сообщение говорит, что изменено и зачем. Новые зависимости ядра добавляются только с обоснованием в PR.
