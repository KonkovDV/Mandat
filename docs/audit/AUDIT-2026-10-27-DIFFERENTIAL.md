# Дифференциальная проверка, 27.10.2026

Проверка дерева `9386cc4` и исправления к нему. Срез 27.10 из PR [#127](https://github.com/KonkovDV/Mandat/pull/127) не переписывается: здесь сверка кода с независимыми эталонами, новые находки и исправления. Каждое исправление держит тест, который падает на `9386cc4`.

Шкала: **К** критично, **В** высокая, **С** средняя, **Н** низкая.

## Метод

Реализации стандартов сверены не с собственными тестами, а с внешними эталонами.

| Что сверяли | Эталон | Результат |
|---|---|---|
| Каноническая запись JCS (RFC 8785) | пакет `rfc8785` 0.1.4, 20 000 случайных документов и 20 000 чисел ([эксперимент](../../tools/experiments/jcs_differential/README.md)) | байты совпали; расходилось только целое 2**53 (находка 6) |
| base58btc в `proofValue` и ключах | пакет `base58` 2.1.1, 5000 случайных строк | совпало |
| Дерево Меркла (RFC 6962) | корни деревьев из 1–8 листьев из тестовых данных Certificate Transparency | совпало; доказательство с чужим номером записи не проходит |
| Доказательство согласованности (RFC 9162, разд. 2.1.4.2) | перебор всех пар размеров до 40 и замена каждого хеша доказательства нулями | верные доказательства проходят, ни одна подмена не прошла |
| Списки статусов (W3C Bitstring Status List) | порядок битов, длина не меньше 131 072, GZIP и base64url, привязка `id` и эпохи | соответствует |
| Статистика оценки | ручной пересчёт: 1 − 0,025^(1/288), 1 − 0,025^(1/48), 1 − 0,025^(1/114), 1/(1 + z²/294), n для 0,9 ± 0,05 | 1,27 %, 7,40 %, 3,18 %, 0,9871 и 139 совпали с `evidence.json` |
| Строгий JSON | RFC 7493, разд. 2.1 и 2.2 | находки 1 и 2 |
| Внешние входы API и вебхуков | `TestClient`: суррогат в манифесте, повтор ключа в теле разбора, метка времени из сотен цифр | находки 2, 7 и 8 |

## Находки

| # | Находка | Сер. | Исправление | Тест |
|---|---|---|---|---|
| 1 | `1e309` проходит строгий разбор как бесконечность ([#122](https://github.com/KonkovDV/Mandat/issues/122)) | С | число вне IEEE 754 — `StrictJsonError` | `test_overflow_to_infinity_is_rejected` |
| 2 | Экранированный одиночный суррогат (`"\ud800"`) проходит строгий разбор. Дальше он не кодируется в UTF-8, и `POST /v1/cases` отвечает 500, а не 422. Целое длиннее предела цифр интерпретатора давало голый `ValueError` | С | одиночный суррогат и сверхдлинное целое — `StrictJsonError` | `test_api_manifest_with_lone_surrogate_is_422_not_500` |
| 3 | Срок `9999-12-31` роняет выпуск удостоверения `OverflowError`; нечитаемый срок оставляет окно 30 дней ([#126](https://github.com/KonkovDV/Mandat/issues/126)) | С | нулевое окно: удостоверение выпускается, но проверку срока не проходит | `test_unreadable_term_gives_a_zero_window_not_thirty_days` |
| 4 | Смена руководителя не гасит удостоверение руководителя, который действовал без МЧД ([#124](https://github.com/KonkovDV/Mandat/issues/124)) | В | `EIO_CHANGED` отзывает удостоверения цепочки `eio_direct`, без имени нового руководителя — приостанавливает; права по МЧД не трогает | `test_monitor_revokes_only_the_directors_own_attestation_on_director_change` |
| 5 | `MANDAT_REQUIRE_ATTESTATION_STATUS=0` выключает проверку статуса в `pilot` и `prod` ([#112](https://github.com/KonkovDV/Mandat/issues/112)) | С | в защищённом режиме — ошибка конфигурации: `serve` и `attest-verify` не запускаются | `test_status_check_cannot_be_turned_off_in_pilot` |
| 6 | JCS принимает целое 2**53, эталон `rfc8785` его отвергает. RFC 7493 (разд. 2.2) считает точными только целые до 2**53 − 1: у 2**53 и 2**53 + 1 одно значение IEEE 754 | Н | граница ±(2**53 − 1) | `test_integer_bound_is_max_safe_integer` |
| 7 | Метка времени вебхука из 400 цифр роняет `signature_ok` с `OverflowError`, из 5000 — с `ValueError`, до проверки подписи. Отправитель не аутентифицирован. Ответ read-back с такой меткой ломает проверку внешнего якоря вместо «не подтверждено» | С | метка — не длиннее 12 цифр, иначе подпись не сходится | `test_oversized_or_foreign_timestamp_is_a_mismatch_not_an_exception` |
| 8 | Тело `POST /v1/cases/{id}/review/{action}` читается нестрого: два поля `reason` дают 200, в журнал идёт последнее значение. Остальные POST тело проверяют (R12) | Н | та же зависимость `strict_json_object` | `test_review_body_with_a_duplicate_key_is_422_and_not_journaled` |
| 9 | В `RULES.md` банкротство в `demo` помечено обязательным, хотя профиль его снимает ([#111](https://github.com/KonkovDV/Mandat/issues/111)) | С | генератор смотрит снятие раньше обязательности | `test_rules_doc_profile_table_matches_the_profiles` |
| 10 | Хеш исходников в `evidence.json` 20.10 переписали без прогона pytest: тестов там 1491, а в дереве 1554. `--verify-source` проверял только хеш | С | `evidence.json` хранит число собранных тестов, `--verify-source` сверяет его с деревом | шаг CI `build_evidence.py --verify-source` |

## Отвергнутое решение: не-символы

RFC 7493 запрещает в строках и не-символы (U+FFFE, U+FDD0 и подобные). Эта проверка была написана и снята до отправки. Тот же `strict_loads` читает журнал, а основание решения в API разбора проходит через обычный разбор FastAPI. Рецензент с U+FFFE в основании делал весь журнал «повреждённым» для `journal-verify`. Unicode Corrigendum #9 (2013) разрешает не-символы в обмене, а JCS записывает их однозначно. Поэтому они остаются; тест `test_noncharacters_are_kept_because_the_journal_is_read_strictly` держит это решение.

## Поправка к срезу 27.10

В срезе 27.10 сказано, что I-JSON не хочет неэкранированный U+2028. Это не так. RFC 7493 (разд. 2.1) запрещает только суррогаты и не-символы. U+2028 допускают и RFC 8259, и I-JSON, а RFC 8785 (разд. 3.2.2.2) требует оставить его без экранирования. Тест `test_valid_pairs_and_line_separator_are_kept`.

## Перенесено из других репозиториев автора

| Практика | Откуда | Где теперь |
|---|---|---|
| Снимок числа тестов по `pytest --collect-only` | SynAPS-GridPlan, `scripts/export_test_count.py` | `scripts/build_evidence.py`: поле `tests.collected` и сверка в `--verify-source` (находка 10). Число одинаково на Python 3.11–3.13 |
| Статический анализ CodeQL | SynAPS-RepairFlow, `.github/workflows/codeql.yml` | `.github/workflows/codeql.yml`: права токена по умолчанию пустые, действие закреплено тем же коммитом, что и загрузка SARIF Scorecard |

Не перенесено: поиск секретов по шаблонам из SynAPS-GridPlan (в репозитории лежат открытые тестовые ключи векторов eddsa-jcs-2022, нужен список исключений), ночная кампания отказов и `mutmut` в CI из SynAPS-RepairFlow (у «Мандата» 8814 мутаций сценариев идут в каждом прогоне, мутации кода — вручную, [MUTATION](../testing/MUTATION.md)).

## Что осталось

- Отзыв удостоверений `eio_direct` касается всех удостоверений организации без МЧД, выпущенных до обработки события; новому руководителю нужна новая проверка ([LIMITS](../../LIMITS.md#безопасность-и-защита-данных)).
- Открытыми остаются [#99](https://github.com/KonkovDV/Mandat/issues/99) (Windows и `fcntl`), [#100](https://github.com/KonkovDV/Mandat/issues/100) (маршрут допуска) и задачи волны B.
- Число тестов и замер скорости в `evidence.json` пересобраны на Linux, Python 3.13.

## Как повторить

```bash
python -m pytest tests/test_r12_strict_json.py tests/test_jcs.py tests/test_m04_request_binding.py \
  tests/test_services.py tests/test_audit_status.py tests/test_serve_guard.py tests/test_interfaces.py \
  tests/test_webhooks.py tests/test_m09c_review_api.py
python scripts/build_evidence.py --verify-source
```
