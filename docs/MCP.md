# MCP: «Мандат» для ИИ-агентов банка

ИИ-агент, который действует за клиента, — готовит платёж, проводит его, ведёт корпоративного клиента, — тоже представитель. Прежде чем подписать или отправить операцию, он спрашивает «Мандат», как это делают сотрудник в консоли и ДБО через API. `mandat mcp` отдаёт агенту те же решения по Model Context Protocol (версия 2025-06-18, транспорт stdio): тот же движок правил, тот же допуск по удостоверению, тот же журнал аудита. Ответ агент получает готовым и переписать его не может.

## Инструменты

| Инструмент | Что получает | Что отвечает |
|---|---|---|
| `check_authority` | манифест кейса, как в теле `POST /v1/cases` ([контракт](api/case-manifest.schema.json)), и по желанию `profile` | вердикт; причины — находки правил, которые к нему привели; следующие шаги сотрудника и подразделение; `report_id` и `reproducibility_hash` |
| `admit_operation` | запрос OpenID AuthZEN, как у `/access/v1/evaluation` ([профиль](RUN.md#допуск-по-openid-authzen-10)) | `decision`, причины отказа, ссылка на доказательство: хеш отчёта, по которому выпущено удостоверение, и запись решения в журнале |
| `get_report` | `report_id` из ответа `check_authority` этой сессии | полный отчёт: цепочка полномочий, все находки с доказательствами и нормами, маршрут разбора |

Описания инструментов говорят вызывающей модели прямо: вердикт окончательный и считается правилами; «на разбор» и «недостаточно данных» передаются сотруднику банка вместе с `report_id` и следующими шагами, а не толкуются заново; `confirmed` — ещё не допуск, перед операцией нужен `admit_operation`. Каждый ответ несёт поле `what_to_do` с тем же указанием для конкретного исхода.

### `check_authority`

```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "check_authority", "arguments": {"manifest": {
  "case_id": "07_revoked",
  "organization": {"inn": "7729985006", "ogrn": "1257700518756", "name": "ООО «Ясная поляна»"},
  "operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20", "amount_rub": 480000},
  "documents": [{"id": "poa-root", "kind": "emchd_xml", "content": "<?xml …"}, {"id": "egrul", "kind": "egrul_json", "content": "{…}"}],
  "registry": {"1c90664d-d4ef-ee3d-9880-61334d0c9c34": "revoked"}}}}}
```

```json
{"jsonrpc": "2.0", "id": 2, "result": {"isError": false, "content": [{"type": "text", "text": "…тот же JSON строкой…"}],
 "structuredContent": {
  "report_id": "case-cacf2aa98c803052", "verdict": "refuted", "verdict_label": "ОПРОВЕРГНУТО", "hand_to_human": false,
  "what_to_do": "Полномочие опровергнуто правилами. Операцию не проводите и обход не ищите: передайте причины и следующие шаги сотруднику банка.",
  "reasons": [{"rule_id": "R-REG-001", "effect": "refutes", "message": "МЧД 1c90664d-d4ef-ee3d-9880-61334d0c9c34 отменена по данным реестра", "norm": "ГК РФ, ст. 188, 189; …"}],
  "next_steps": [{"action": "Запросить действующую МЧД; при недоступности реестра — повторить проверку", "unit": "Операционный блок МСБ"}],
  "reproducibility_hash": "93f850fbfbcc56a29f5cd1249afe459622134a711ad1f1f231dbd272914acf20"}}}
```

`case_id` выдаёт сервер, как в режиме пользователей API; присланный сохраняется в отчёте как `client_case_id`.

### `admit_operation`

```json
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "admit_operation", "arguments": {
  "subject": {"type": "representative", "id": "123456789012"},
  "resource": {"type": "organization", "id": "7707083893"},
  "action": {"name": "PAYMENT_SIGN", "properties": {"operation_date": "2026-11-28", "amount_rub": 500000}},
  "context": {"credential": {"…": "удостоверение «Мандата»"}, "nonce": "n-0123456789abcdef", "audience": "bank-agent-pay"}}}}
```

```json
{"decision": true, "context": {"checked_at": "2026-11-28T09:12:44+00:00"},
 "evidence": {"report_sha256": "93f850fb…", "journal_record": {"seq": 41, "hash": "5c1e…"}},
 "what_to_do": "Операция допущена по удостоверению «Мандата» один раз: этот nonce второго допуска не даст."}
```

Отказ — `decision: false` с причинами в `context.problems`, как у `/access/v1/evaluation`; повтор того же запроса — отказ с `already_admitted`. Здесь и дальше показан `structuredContent`, конверт ответа тот же, что выше.

### `get_report`

```json
{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_report", "arguments": {"report_id": "case-cacf2aa98c803052"}}}
```

Ответ — отчёт целиком: `case_id`, `verdict`, `chain`, `checks`, `findings` с `refs` и `norm_ref`, `next_steps`, `routing`. Чужой или выдуманный `report_id` — `isError: true` и текст «такого report_id эта сессия не выдавала».

## Подключение

Учётная запись агента — обычная учётная запись API. Агенту, который проверяет кейсы, нужна роль `operator`; агенту, который проводит операции, — `dbo` со своим получателем удостоверений:

```bash
mandat api-user --name agent-pay --role dbo --audience bank-agent-pay --expires-in-days 30
```

Строку из ответа команды добавьте в `MANDAT_API_USERS`, токен — в `MANDAT_MCP_TOKEN` в конфигурации MCP-клиента:

```json
{
  "mcpServers": {
    "mandat": {
      "command": "mandat",
      "args": ["mcp"],
      "env": {
        "MANDAT_ENV": "pilot",
        "MANDAT_MCP_TOKEN": "<токен учётной записи агента>",
        "MANDAT_API_USERS": "/etc/mandat/users.json",
        "MANDAT_API_JOURNAL": "/var/lib/mandat/journal.jsonl",
        "MANDAT_API_STORE": "sqlite:///var/lib/mandat/store.db",
        "MANDAT_ATTESTATION_KEYRING": "/etc/mandat/keyring.json",
        "MANDAT_STATUS_DIR": "/var/lib/mandat/status",
        "MANDAT_REPRESENTATIVE_KEY_FILE": "/etc/mandat/representative_hmac.key"
      }
    }
  }
}
```

В `pilot` и `prod` сервер читает те же ключи журнала, ПДн и хранилища и тот же внешний якорь журнала, что `mandat serve` ([переменные окружения](RUN.md#переменные-окружения)), и с той же неполной настройкой не стартует: без журнала, ключей или действующего токена — код 2 и причина в stderr. На стенде хватает `MANDAT_API_TOKEN` и того же значения в `MANDAT_MCP_TOKEN`; допуск включают переменные из раздела о [допуске операции](RUN.md#допуск-операции-для-дбо).

## Границы

- Модели внутри нет, в сеть сервер не ходит: вердикт и допуск считают правила с нормой и `authorize_operation` ([ADR-001](adr/ADR-001-verdict-ownership.md)); `tests/test_llm_chokepoint.py` держит эту границу для всего кода. В stdout идут только сообщения протокола, журнал сервера — в stderr.
- Кто спрашивает, решает учётная запись по токену, а не агент: проверяют кейсы `operator`, `reviewer` и `admin`, допуск в `pilot` и `prod` получает только `dbo` для своих `audiences`. Токен проверяется на каждом вызове: отзыв и срок действуют без перезапуска.
- Допуск тот же, что в HTTP, — `services.admission.admit`: те же отказы, тот же одноразовый `nonce` в общем хранилище `MANDAT_API_STORE`. `nonce`, израсходованный через HTTP, не даст допуска через MCP, и наоборот.
- Каждая проверка (`verify`), чтение отчёта (`read_case`) и решение о допуске (`authorize_operation`) пишутся в журнал `MANDAT_API_JOURNAL` с `via: mcp` и именем учётной записи. Журнал недоступен — ответа нет.
- Отчёты живут в памяти процесса и выдаются только своей сессии, последние 100: перебором `report_id` чужой кейс не прочитать. Сотруднику отчёт передаёт агент через `get_report`.
- Вход — строгий JSON с пределами HTTP API: сообщение до 8 МиБ, повтор ключа, `NaN` и вложенность глубже 64 — отказ; пределы манифеста те же, что у `POST /v1/cases`. Ошибка протокола — код JSON-RPC, ошибка инструмента — `isError: true` с причиной по-русски.
