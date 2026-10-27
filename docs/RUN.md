# Запуск, команды и API

Как запустить стенд, проверить удостоверения и журнал, поднять API и какие переменные окружения он читает. Коротко про демо — в [README](../README.md#запустить-демо). Нужен Python 3.11 или новее; CI проверяет 3.11–3.14.

Linux, macOS или WSL, без установки в систему:

```bash
./run-demo.sh
```

На Windows без WSL пакет пока не запускается: модуль списков статусов делает `import fcntl` ([границы](../LIMITS.md#безопасность-и-защита-данных)). Родной запуск `.\run-demo.ps1` вернёт задача A4 ([#99](https://github.com/KonkovDV/Mandat/issues/99)).

Из установленного пакета:

```bash
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

Для проверок как в CI зависимости ставятся из lock-файлов с хешами ([CONTRIBUTING](../CONTRIBUTING.md#проверки-перед-pr)). Проверка на Cedar ставится отдельно: `pip install -e ".[dev,api,cedar]"`.

## API

Docker поднимает API только на localhost: `docker compose up --build`. Сначала положите в `.env` токен или список пользователей (образец в `.env.example`). Без них compose не стартует, чтобы стенд не оказался открыт с паролем по умолчанию.

Режимов доступа два. Нужен хотя бы один: без обоих `mandat serve` и контейнер из `docker-compose.yml` не стартуют и пишут, что задать.

| Режим | Как включить | Что получается |
|---|---|---|
| Стенд | `MANDAT_API_TOKEN` | один токен с правами администратора, `case_id` задаёт клиент, повтор даёт 409, перезапись через `?replace=true`. Если задан и `MANDAT_API_USERS`, этот токен не действует |
| Пользователи | `MANDAT_API_USERS`: JSON-массив или путь к файлу | у каждого имя, роль и SHA-256 токена; два пользователя с одним токеном не допускаются. `case_id` выдаёт сервер, клиентский сохраняется как `client_case_id` |

| Роль | Создаёт кейсы и события | Видит кейсы | Действия разбора |
|---|---|---|---|
| `operator` | да | только свои | `appeal`, `open`, `close`, `require` |
| `reviewer` | да | только свои, в разборе — любые | `open`, `request_docs`, `accept`, `reject`, `close`, `require` |
| `auditor` | нет | все | нет |
| `admin` | да | все | нет |

Чужой кейс отвечает 404, как несуществующий. Токен и строку для списка выпускает `mandat api-user --name ivanova --role operator`, сам токен показывается один раз.

### Маршруты

Контракт — [`docs/api/openapi.json`](api/openapi.json), Swagger на `/docs` включается `MANDAT_API_DOCS=1`.

| Маршрут | Кто вызывает | Что делает |
|---|---|---|
| `GET /health` | без токена | процесс жив: движок, настроен ли доступ, режим, хранилище |
| `GET /readyz` | без токена | готовность: журнал, хранилище, ключи; 200 или 503, в теле только имена проверок |
| `POST /v1/cases` | `operator`, `reviewer`, `admin` | проверка кейса по манифесту JSON; `?profile=`, `?replace=true`, заголовок `Idempotency-Key` |
| `GET /v1/cases/{id}` | владелец кейса, `auditor`, `admin` | отчёт JSON |
| `GET /v1/cases/{id}/report.html` | то же | карточка сотрудника |
| `GET /v1/cases/{id}/review` | то же и `reviewer` | состояние разбора и версия для `expected_version` |
| `POST /v1/cases/{id}/review/{action}` | `operator`, `reviewer` | действие разбора: `appeal`, `open`, `accept`, `reject`, `request_docs`, `close`, `require` ([разбор](REVIEW.md)) |
| `GET /v1/review/queue` | любая роль; `operator` видит свои кейсы | кейсы, ждущие человека, по сроку |
| `POST /v1/events` | `operator`, `reviewer`, `admin` | оценка событий мониторинга; статусы удостоверений не меняет, их пишет `mandat monitor` |

Окно лимита частоты и отчёты лежат в хранилище `MANDAT_API_STORE`:

| Значение | Когда | Что нужно |
|---|---|---|
| пусто или `memory` | один процесс, стенд | ничего |
| `sqlite:///var/api.db` | несколько воркеров на одной машине: `mandat serve --workers 4` | только стандартная библиотека, файл в режиме WAL |
| `redis://хост:6379/0` | несколько машин за балансировщиком | `pip install "mandat[redis]"` |

С `--workers` больше одного и хранилищем в памяти `mandat serve` не стартует: у каждого воркера был бы свой лимит и свои отчёты.

## Переменные окружения

Образец без значений — `.env.example`. Секреты в репозиторий не кладутся ([SECURITY](../SECURITY.md#секреты)).

### Доступ к API

| Переменная | Зачем | По умолчанию |
|---|---|---|
| `MANDAT_API_TOKEN` | токен режима стенда | пусто |
| `MANDAT_API_USERS` | пользователи и роли | пусто. Если не заданы ни токен, ни пользователи, API отвечает 503 на всё, кроме `/health` и `/readyz` |
| `MANDAT_API_DOCS` | `1` включает Swagger на `/docs` | выключено |
| `MANDAT_API_RATE_PER_MIN` | запросов в минуту с одного адреса, `0` снимает ограничение | 120 |
| `MANDAT_API_STORE` | общее хранилище лимита и отчётов: `memory`, `sqlite:///…`, `redis://…` | `memory` |
| `MANDAT_API_MAX_CONCURRENCY` | одновременных соединений у `mandat serve`; сверх них сразу 503, без очереди до таймаута | 64 |
| `MANDAT_API_JOURNAL` | журнал аудита HTTP API: запись о проверке пишется до сохранения отчёта; журнал не цел или недоступен — 503, отчёт не сохраняется | на стенде не ведётся; в `pilot` и `prod` без него `POST /v1/cases` и действия разбора отвечают 503 |

### Режим, журнал и ключи

| Переменная | Зачем | По умолчанию |
|---|---|---|
| `MANDAT_ENV` | `demo`, `dev`, `test` или `pilot`, `prod` | `demo` |
| `MANDAT_JOURNAL_KEY` | ключ HMAC журнала | на стенде по желанию, в `pilot` и `prod` обязателен |
| `MANDAT_JOURNAL_SEGMENT_RECORDS` | записей в сегменте журнала; при заданном якоре закрытый сегмент уходит наружу | 1000 |
| `MANDAT_PII_KEY` | ключ псевдонимизации ПДн | пусто: на стенде ключ разработки, в `pilot` и `prod` без ключа псевдонимизация отказывает |
| `MANDAT_SIGNER`, `MANDAT_SIGNING_KEY_FILE`, `MANDAT_SIGNING_KEY_PASSPHRASE` и настройки PKCS#11, Vault, KMS | ключ подписи удостоверений | `file`; подробно — [подписант удостоверений](KEYS.md#подписант-удостоверений) |
| `MANDAT_JOURNAL_ANCHOR`, `MANDAT_JOURNAL_ANCHOR_*`, `MANDAT_SIGSTORE_*` | внешний якорь журнала: приёмники, доверие к TSA и Rekor, свидетели | якорь не задан; подробно — [внешний якорь](KEYS.md#внешний-якорь-журнала) |

### Удостоверения, события и разбор

| Переменная | Зачем | По умолчанию |
|---|---|---|
| `MANDAT_STATUS_MAX_AGE_DAYS` | общий предел возраста списка статусов, дней; с окном уровня риска берётся минимум | не задан: окна по уровню риска |
| `MANDAT_STATUS_MAX_AGE_BY_RISK` | JSON «уровень риска → дни»; в `pilot` и `prod` может только сократить окна | `payment`, `transfer`, `high` — около часа; `archive_receipt`, `archive`, `low`, `default` — 7 дней |
| `MANDAT_REQUIRE_ATTESTATION_STATUS` | `1` или `0`: нужна ли проверка статуса в `attest-verify` и `validate_attestation` | в `pilot` и `prod` нужна, и `0` там — ошибка конфигурации: `serve` не запускается, `attest-verify` отказывает (кроме `--crypto-only`), `validate_attestation` удостоверение не принимает ([#112](https://github.com/KonkovDV/Mandat/issues/112)). Допуск операции (`authorize_operation`) требует статус всегда |
| `MANDAT_WEBHOOK_SECRET` | секрет подписи событий мониторинга, `whsec_…` (HMAC) или `whsk_…` (Ed25519, `v1a`, `mandat webhook-secret --asymmetric`); несколько через пробел для смены ключа, первый текущий. Новый печатает `mandat webhook-secret` | подпись не ставится |
| `MANDAT_REVIEW_SLA_HOURS` | сроки разбора в часах по приоритетам, JSON вида `{"P1": 2, "P2": 8}` ([REVIEW](REVIEW.md#sla)) | P1 4, P2 24, P3 72 (не из норм) |
| `MANDAT_CLASSIFIER_MAX_AGE_DAYS` | на сколько дней снимок классификатора может отставать от операции; дальше — разбор ([классификатор](CLASSIFIER.md)) | 3 (не из норм) |

### Модель

| Переменная | Зачем | По умолчанию |
|---|---|---|
| `MANDAT_LLM_BASE_URL` | адрес модели с OpenAI-совместимым `/chat/completions` | пусто: модель выключена |
| `MANDAT_LLM_MODEL` | имя модели в запросе | `local-model` |
| `MANDAT_LLM_API_KEY` | ключ доступа к модели, если он нужен | пусто |
| `MANDAT_LLM_ALLOWED_HOSTS` | хосты модели через запятую; другой хост — ошибка | `127.0.0.1,localhost` |
| `MANDAT_LLM_ALLOW_INSECURE_LOOPBACK` | `1` разрешает в `pilot` и `prod` `http` к модели на `127.0.0.1`, `localhost` или `::1` | выключено: в `pilot` и `prod` только `https` |

## Перед сдачей

`make check` на Linux, в WSL или Docker. По шагам — как в CI: `python -m ruff check src tests scripts tools`, `python -m ruff format --check src tests scripts tools`, `python -m mypy`, `python -m pytest --cov=mandat`, `python scripts/lint_claims.py`, `python scripts/check_links.py`, `python scripts/build_evidence.py --verify-source`. Числа для слайдов пересобирает `make evidence` на Linux.
