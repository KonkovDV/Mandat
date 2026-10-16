# Как вносить изменения

## Порядок работы

1. Заведите issue по шаблону (ошибка, предложение) или сошлитесь на находку из `docs/audit/`.
2. Работайте в отдельной ветке; прямые коммиты в `main` не допускаются.
3. Откройте pull request по шаблону. В main сливается только PR с зелёным CI и одобрением владельца из `.github/CODEOWNERS`.
4. Изменение правила (`policies/`) требует ссылки на норму и теста; статус правила `draft` меняет только юрист банка.
5. Уязвимости не публикуются в issues: см. [SECURITY.md](SECURITY.md).

## Проверки перед PR

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install --require-hashes -r requirements/pip.lock
python -m pip install --require-hashes -r requirements/dev.lock -r requirements/build.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m ruff check src tests scripts tools
python -m ruff format --check src tests scripts tools
python -m mypy
python -m pytest
python scripts/lint_claims.py
```

Так же ставит зависимости CI: только lock-файлы с хешами, без незакреплённых extras. Подробности — в [AGENTS.md](AGENTS.md).

Числа в документах сверяются `lint_claims.py` с [CLAIMS_REGISTRY.md](CLAIMS_REGISTRY.md); формулировки из [BANNED_CLAIMS.txt](BANNED_CLAIMS.txt) не используются.

## Коммиты

Одно логическое изменение на коммит, сообщение описывает, что и зачем изменено. Новые зависимости ядра добавляются только с обоснованием в PR.
