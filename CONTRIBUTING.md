# Как вносить изменения

Как предложить изменение, какие проверки пройти и как оформить коммит. Порядок для исполнителей задач из очереди — в [AGENTS.md](AGENTS.md) и [плане работы](docs/HANDOFF_NEXT_QUOTA.md). Правила общения — [кодекс поведения](CODE_OF_CONDUCT.md).

## Порядок работы

1. Заведите issue по шаблону (ошибка, предложение) или сошлитесь на находку из `docs/audit/`. Уязвимости не публикуются в issues: см. [SECURITY.md](SECURITY.md).
2. Работайте в отдельной ветке от свежего `main`. Прямые коммиты в `main` не допускаются, PR поверх другого PR не открывается.
3. Один PR закрывает одну задачу или одну находку. Откройте его по шаблону, в описании — `Closes #N`.
4. В `main` сливается только PR с зелёным CI и одобрением владельца из `.github/CODEOWNERS`. Уже влитую ветку удаляет владелец, когда её коммиты уже в `main`.
5. Изменение правила (`src/mandat/data/rules.json`, `src/mandat/rules/checks.py`, Cedar в `policies/`) требует ссылки на норму и теста; статус правила `draft` меняет только юрист банка.

## Проверки перед PR

Команды ниже повторяют CI (`.github/workflows/ci.yml`). `make install` ставит зависимости так же, `make check` — короче и без двух шагов: гоняет линтеры, `pytest` без покрытия и сверку утверждений и ссылок, но не `--cov` и не `--verify-source`.

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

Зависимости ставятся так же, как в CI: только lock-файлы с хешами, без незакреплённых extras. Подробности — в [AGENTS.md](AGENTS.md).

- Правка в `src`, `tests`, `scripts` или `examples` меняет хеш исходников в `evidence.json`. Пересборка — `make evidence` на Linux (Docker или WSL): на Windows поедут версия Python и замеры скорости.
- На Windows без WSL и Docker полный `pytest` пока не собирается: модуль списков статусов делает `import fcntl`. Локально гоняйте затронутые тесты, полное доказательство — CI.
- CI сверх этого проверяет, что ни один тест не пропущен (`scripts/check_no_skips.py`), ищет известные уязвимости в зависимостях (`pip-audit`) и в образе (Trivy), проверяет рабочие процессы `actionlint`.

Числа в документах сверяются `lint_claims.py` с [CLAIMS_REGISTRY.md](CLAIMS_REGISTRY.md). Формулировки из [BANNED_CLAIMS.txt](BANNED_CLAIMS.txt) не используются даже с отрицанием: проверка ищет подстроку. Срезы с датой в имени и прошлые записи `CHANGELOG.md` не переписываются.

## Коммиты

[Соглашение о коммитах](https://www.conventionalcommits.org/ru/v1.0.0/) с кодом находки, если он есть: `fix(M-10): …`, `feat(M-03): …`, `docs(M-12): …`. Без находки — тип без области: `docs: …`. Одно логическое изменение на коммит, сообщение говорит, что изменено и зачем. Трейлеры соавторства, служебные пометки и эмодзи не ставятся. Новые зависимости ядра добавляются только с обоснованием в PR.
