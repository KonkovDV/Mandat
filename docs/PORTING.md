# Что взято из других проектов автора

Откуда в «Мандат» пришли паттерны и небольшие модули и что в них изменено. Переносились идеи и отдельные файлы, а не системы целиком: предметная область другая.

Все исходные репозитории — `github.com/KonkovDV`, один автор, лицензия MIT. Существование репозиториев, лицензию и каждый путь из таблиц сверили через GitHub API 13.10.2026. У AeroBIM пути даны относительно `backend/src/aerobim/`, а у файлов без каталога — по имени файла. Два репозитория в таблицах не названы («другой репозиторий автора»), их пути не проверялись.

## Перенесено

| Идея | Откуда | Где в «Мандате» | Что изменено |
|---|---|---|---|
| Свёртка без ложного «да», профили строгости | AeroBIM: `application/services/package_outcome.py`, `capability_policy.py` | `domain/verdict.py` | четыре банковских вердикта, «тишина — не успех» на уровне групп |
| Модель не пишет вердикт | AeroBIM: `application/services/determinism_gate.py`, `domain/llm_advisory.py` | `advisory/gate.py`, [ADR-001](adr/ADR-001-verdict-ownership.md) | добавлен шлюз дословных цитат |
| Находка без источника не публикуется | AeroBIM: `domain/finding_provenance.py`, `evidence_provenance.py` | `domain/model.py` (`Finding`, `EvidenceRef`) | локаторы XPath, JSON, текст, реестр; без абсолютных путей |
| Безопасный разбор XML | AeroBIM: `core/security/xml_limits.py` | `intake/xml_safety.py` | лимиты под размер МЧД |
| Пакет правил с хешем и статусом | AeroBIM: `json_norm_rule_pack_loader.py`, `norm_pack_hash.py`, `norm_rule_eligibility.py` | `rules/pack.py`, `data/rules.json` | в `strict` неутверждённое правило не опровергает |
| Машина состояний разбора | AeroBIM: `domain/review_state_machine.py` | `review/state_machine.py` | банковские роли, основание обязательно |
| Псевдонимизация HMAC | AeroBIM: `domain/hybrid/privacy_guard.py`, `sensitive_entities.py` | `privacy/pii.py` | ИНН и СНИЛС с контрольными числами, паспорт и телефон в том числе с пробелами, точками и словом «номер» |
| Клиент модели с белым списком хостов | AeroBIM: `infrastructure/adapters/openai_compat_llm_provider.py` | `advisory/llm.py` | без облачного хоста по умолчанию, псевдонимизация |
| Интервал Вилсона, объём выборки, κ Коэна | AeroBIM: `domain/study_design.py`, `eval_statistics.py` | `eval/metrics.py` | только нужные функции |
| Журнал с хеш-цепочкой, HMAC и свидетелем | SynAPS-ProgramPlan: `src/synaps_programplan/journal.py` | `audit/journal.py` | банковские действия |
| Пояснение только по фактам | SynAPS-ProgramPlan: `src/synaps_programplan/copilot.py` | `advisory/explainer.py` | факты — находки отчёта |
| Реестр утверждений, запрещённые фразы, `evidence.json` | SynAPS-ProgramPlan: `CLAIMS_REGISTRY.md`, `BANNED_CLAIMS.txt`, `scripts/lint_claims.py`, `scripts/build_evidence.py` | те же файлы | проверка без учёта регистра, сверка чисел |
| Вторая свёртка вердикта | SynAPS-ProgramPlan: проверка плана отдельно от решателя | `domain/verdict_oracle.py` | расхождение ловит `test_verdict_oracle_agrees_with_fold` |
| Удостоверение W3C VC с Ed25519 | fsp-talent: `docs/adr/0007-w3c-verifiable-credentials.md`, `backend/app/services/vc/issuer.py` | `audit/attestation.py` | результат проверки полномочия, без ФИО |
| Контрольное число ИНН | fsp-talent: `backend/app/services/inn.py` | `domain/identifiers.py` | добавлены ОГРН, ОГРНИП, СНИЛС, КПП |
| CI с минимальными правами | fsp-talent: `.github/workflows/ci.yml` | `.github/workflows/ci.yml` | — |
| Подпись исходящих событий | fsp-talent: `backend/app/services/webhooks.py` | `services/webhooks.py` | Standard Webhooks, без следования редиректам |
| Трассировка «норма → код → тест» | OpenRebar: `docs/NORMATIVE_TRACEABILITY.md` | [RULES](RULES.md), `test_every_rule_traces_to_an_existing_test` | генерируется из пакета |
| Хеш набора доказательств, границы утверждений | другой репозиторий автора | `reproducibility_hash`, [LIMITS](../LIMITS.md) | — |
| Любой сигнал может остановить решение | другой репозиторий автора | `services/monitoring.py` | отмена МЧД в одной пачке с захватом всё равно отзывает удостоверение |
| Ненайденное значение — не соответствие | другой репозиторий автора | R-JNT-001, R-CHR-001 | нет `ПрСовмПолн` (и у одного представителя) или пустая сумма не дают «подтверждено» |
| Слова не шире доказательств | другой репозиторий автора | `advisory/explainer.py` (`_polarity`) | пояснение не скажет «подтверждено», цитируя опровержение |
| Сильнейшее возражение до итога | другой репозиторий автора | `services/next_steps.py`, поле `objection` | у «подтверждено» сотрудник видит главный сигнал риска |
| Снимок числа тестов по `pytest --collect-only` в CI | SynAPS-GridPlan: `scripts/export_test_count.py` | `scripts/build_evidence.py` (`collected_count`, `--verify-source`) | число хранится в `evidence.json`, а не в отдельном файле; хеш исходников без свежего прогона больше не проходит (27.10) |
| Статический анализ CodeQL | SynAPS-RepairFlow: `.github/workflows/codeql.yml` | `.github/workflows/codeql.yml` | права токена по умолчанию пустые, действие закреплено тем же коммитом, что и загрузка SARIF Scorecard (27.10) |

Не из репозиториев автора, а из стандартов и статей: списки статусов (W3C Bitstring Status List), дерево Меркла (RFC 9162), перекрёстная проверка на Cedar, пара «риск — покрытие». Источники — [SOTA](SOTA.md) и [LITERATURE](LITERATURE.md).

## Не взято

- SynAPS — оптимизатор расписаний, для очереди из сотен кейсов избыточен.
- OpenRebar целиком — другая предметная область (C#, армирование), а зависимость ImageSharp идёт под Six Labors Split License.
- Lime — в репозитории нет кода.
- AeroBIM целиком — модель находки завязана на IFC.
- Согласия маркетплейса из fsp-talent (`consent_effects.py`) — у банка другое основание обработки.
- Форки клиентов Ethereum из профиля `KonkovDV` — чужой код и другая задача.
- Безымянный репозиторий целиком — он рассчитан на облачные модели, а банку нужен закрытый контур.
