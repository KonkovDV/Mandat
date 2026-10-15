# Запуск, команды и API

Как запустить стенд, проверить удостоверения и журнал, поднять API и какие переменные окружения он читает. Коротко про демо — в [README](../README.md#запустить-демо). Нужен Python 3.11 или новее.

Windows, без установки в систему:

```powershell
.\run-demo.ps1
```

Из установленного пакета:

```powershell
pip install -e ".[dev,api]"
mandat demo
mandat verify examples/cases/10_old_director_poa --html var/10.html
mandat attest-verify var/demo/attestations/01_ok.json --public-key-file var/demo/public_key.txt --status-dir var/demo/status
mandat monitor examples/events/poa_revoked.json --status-dir var/demo/status
mandat monitor examples/events/takeover.json --status-dir var/demo/status
mandat attest-verify var/demo/attestations/01_ok.json --public-key-file var/demo/public_key.txt --status-dir var/demo/status
mandat journal-proof var/demo/journal.jsonl --seq 3
python -m pytest
```

Порядок важен для демонстрации статусов. Первый `attest-verify` идёт до событий и выходит с кодом 0: удостоверение действует. Событие захвата из `takeover.json` приостанавливает удостоверения организации, поэтому второй `attest-verify` печатает «приостановлено» и выходит с кодом 3. Это ожидаемый результат, а не сбой: код 3 значит «подпись верна, но статус не "действует"». Команда печатает и вердикт из удостоверения: удостоверение опровергнутого кейса тоже действительно, решение по операции принимается по вердикту. Код 2 — ошибка вызова/конфигурации (нет ключа издателя, в `pilot`/`prod` нет `--status-dir` без `--crypto-only`, битые аргументы). Код 4 — подпись верна, но удостоверение не принимается: истёк срок, чужой издатель или назначение, профиль demo в защищённом режиме. Чтобы вернуться к исходному состоянию, снова выполните `mandat demo`.

На Linux и macOS команды те же. Проверка на Cedar ставится отдельно: `pip install -e ".[dev,api,cedar]"`.

## API

Docker поднимает API только на localhost: `docker compose up --build`. Сначала положите в `.env` токен или список пользователей (образец в `.env.example`). Без них compose не стартует, чтобы стенд не оказался открыт с паролем по умолчанию.

Режимов доступа два. Нужен хотя бы один: без обоих `mandat serve` и контейнер из `docker-compose.yml` не стартуют и пишут, что задать.

| Режим | Как включить | Что получается |
|---|---|---|
| Стенд | `MANDAT_API_TOKEN` | один токен с правами администратора, `case_id` задаёт клиент, повтор даёт 409, перезапись через `?replace=true`. Если задан и `MANDAT_API_USERS`, этот токен не действует |
| Пользователи | `MANDAT_API_USERS`: JSON-массив или путь к файлу | у каждого имя, роль и SHA-256 токена; два пользователя с одним токеном не допускаются. `case_id` выдаёт сервер, клиентский сохраняется как `client_case_id` |

| Роль | Создаёт кейсы и события | Видит кейсы |
|---|---|---|
| `operator` | да | только свои |
| `reviewer` | да | только свои |
| `auditor` | нет | все |
| `admin` | да | все |

Чужой кейс отвечает 404, как несуществующий. Токен и строку для списка выпускает `mandat api-user --name ivanova --role operator`, сам токен показывается один раз.

Окно лимита частоты и отчёты лежат в хранилище `MANDAT_API_STORE`:

| Значение | Когда | Что нужно |
|---|---|---|
| пусто или `memory` | один процесс, стенд | ничего |
| `sqlite:///var/api.db` | несколько воркеров на одной машине: `mandat serve --workers 4` | только стандартная библиотека, файл в режиме WAL |
| `redis://хост:6379/0` | несколько машин за балансировщиком | `pip install "mandat[redis]"` |

С `--workers` больше одного и хранилищем в памяти `mandat serve` не стартует: у каждого воркера был бы свой лимит и свои отчёты.

## Переменные окружения

| Переменная | Зачем | По умолчанию |
|---|---|---|
| `MANDAT_API_TOKEN` | токен режима стенда | пусто |
| `MANDAT_API_USERS` | пользователи и роли | пусто. Если не заданы ни токен, ни пользователи, API отвечает 503 на всё, кроме `/health` |
| `MANDAT_API_DOCS` | `1` включает Swagger на `/docs` | выключено |
| `MANDAT_API_RATE_PER_MIN` | запросов в минуту с одного адреса, `0` снимает ограничение | 120 |
| `MANDAT_API_STORE` | общее хранилище лимита и отчётов: `memory`, `sqlite:///…`, `redis://…` | `memory` |
| `MANDAT_WEBHOOK_SECRET` | секрет подписи событий мониторинга, `whsec_…` (HMAC) или `whsk_…` (Ed25519, `v1a`, `mandat webhook-secret --asymmetric`); несколько через пробел для смены ключа, первый текущий. Новый печатает `mandat webhook-secret` | подпись не ставится |
| `MANDAT_ENV` | `demo`, `dev`, `test` или `pilot`, `prod` | `demo` |
| `MANDAT_JOURNAL_KEY` | ключ HMAC журнала | на стенде по желанию, в `pilot` и `prod` обязателен |
| `MANDAT_API_JOURNAL` | журнал аудита HTTP API: запись о проверке пишется до сохранения отчёта; журнал не цел или недоступен — 503, отчёт не сохраняется | на стенде не ведётся; в `pilot` и `prod` без него `POST /v1/cases` отвечает 503 |
| `MANDAT_PII_KEY` | ключ псевдонимизации ПДн | пусто: на стенде ключ разработки, в `pilot` и `prod` без ключа псевдонимизация отказывает |
| `MANDAT_LLM_*` | локальная модель и белый список хостов | модель выключена |

## Перед сдачей

`make check` на Linux. На Windows по шагам: `python -m ruff check src tests scripts tools`, `python -m ruff format --check src tests scripts tools`, `python -m mypy`, `python -m pytest`, `python scripts/lint_claims.py`, `python scripts/check_links.py`. Числа для слайдов пересобирает `python scripts/build_evidence.py`.

