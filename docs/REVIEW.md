# Разбор кейса человеком: роли и правила

Решения по находке M-09 (часть a). Код: `src/mandat/review/state_machine.py`, `src/mandat/api/principals.py`;
тесты: `tests/test_m09a_review_roles.py`. Хранение состояния разбора — часть b (ниже); маршруты API (часть c) — отдельным PR;
до него находка M-09 не закрыта.

## Роли

| Роль | Что может | Чего не может |
|---|---|---|
| `operator` | подать жалобу (`appeal`) от имени клиента; открыть кейс (`open`); закрыть кейс после решения (`close`) | решать: `accept`, `reject`, `request_docs` |
| `reviewer` | открыть кейс; решать: `accept`, `reject`, `request_docs`; закрыть кейс | подавать жалобу |
| `auditor` | только чтение | двигать кейс любым действием |
| `admin` | управлять пользователями и настройками | двигать кейс любым действием, в том числе решать |

Роли клиента нет: жалобу клиента подаёт `operator`, в журнале у записи `review_appeal` стоит `on_behalf_of: "client"`.

## Специализация reviewer

У `reviewer` есть атрибут `specialization`: `lawyer`, `compliance`, `security` или `client_service`.
В `MANDAT_API_USERS` он задаётся полем `specialization` и допустим только у `reviewer`.

Если у кейса задана `required_specialization`, решать его (`accept`, `reject`, `request_docs`) может только
`reviewer` с этой специализацией. Специализация решающего пишется в журнал (`details.specialization`).

Прежние имена ролей разбора (`lawyer`, `compliance`, `security`, `client_service`) принимаются как
`reviewer` с этой специализацией: так старые вызовы продолжают работать.

## Независимость решения

Решать кейс (`accept`, `reject`, `request_docs`) нельзя тому, кто:

- создал кейс (`created_by`);
- подал жалобу по нему (`appeal` в истории);
- принял решение, которое обжалуется (`appealed_decision_by`).

Открытие кейса (`open`) в счёт не идёт: открывший может его решать.

## Переходы

| Из | Действие | В | Роли |
|---|---|---|---|
| `auto_decided` | `appeal` | `queued` | `operator` |
| `queued` | `open` | `in_review` | `operator`, `reviewer` |
| `docs_requested` | `open` | `in_review` | `operator`, `reviewer` |
| `in_review` | `request_docs` | `docs_requested` | `reviewer` |
| `in_review` | `accept` | `accepted` | `reviewer` |
| `in_review` | `reject` | `rejected` | `reviewer` |
| `accepted`, `rejected` | `close` | `closed` | `operator`, `reviewer` |

`close` — обычное действие со своими правилами. В части c оно будет одним из действий маршрута
`POST /review/{action}`, а не отдельным маршрутом.

## Хранение состояния (часть b)

`Store.review_get(case_id)` возвращает состояние разбора и версию; `Store.review_put(case_id, state, expected_version)`
пишет, только если текущая версия равна ожидаемой (0 — разбора ещё нет), и возвращает новую версию. Иначе —
`ReviewVersionConflict` с ожидаемой и текущей версией; в части c это ответ 409.

Сравнение и запись атомарны во всех хранилищах: в памяти — под блокировкой процесса, в SQLite — в одной транзакции
`BEGIN IMMEDIATE` (между процессами на одной машине), в Redis — одним Lua-скриптом (между машинами). Тест
`tests/test_m09b_review_store.py`: из N параллельных записей с одной версией проходит ровно одна, остальные получают
конфликт версии; для SQLite — и между процессами.

Хранилище не проверяет переходы и роли: это делает `ReviewCase` (выше). Состояние в памяти не переживает рестарт.

## SLA

Значения по умолчанию — P1 4 ч, P2 24 ч (и P3 72 ч) в `src/mandat/services/next_steps.py`. Они не взяты ни из
какой нормы; реальные сроки задаёт банк. Настройка значений без правки кода — в части c.
