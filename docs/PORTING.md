# Что взято из других проектов автора

Все исходные репозитории — `github.com/KonkovDV`, лицензия MIT, один автор. Переносились паттерны и небольшие модули, а не целые системы: предметная область другая, и тащить строительную логику в банковский продукт нельзя.

| Идея | Откуда (путь в исходном репозитории) | Где в «Мандате» | Что изменено |
|---|---|---|---|
| Свёртка вердикта без ложного «да», профили строгости | AeroBIM: `application/services/package_outcome.py`, `capability_policy.py` | `domain/verdict.py` | четыре банковских вердикта, «тишина — не успех» на уровне групп проверок |
| Модель не пишет вердикт, находки советника — только справка | AeroBIM: `application/services/determinism_gate.py`, `domain/llm_advisory.py`, ADR-001 | `advisory/gate.py`, `docs/adr/ADR-001-verdict-ownership.md` | добавлен шлюз дословных цитат |
| Находка без источника не публикуется, стабильный id | AeroBIM: `domain/finding_provenance.py`, `evidence_provenance.py` | `domain/model.py` (`Finding`, `EvidenceRef`) | локаторы XPath, JSON, текст, реестр; без абсолютных путей файловой системы |
| Безопасный разбор XML | AeroBIM: `core/security/xml_limits.py` | `intake/xml_safety.py` | лимиты под размер МЧД |
| Пакет правил с хешем, статусом и проверкой при загрузке | AeroBIM: `json_norm_rule_pack_loader.py`, `norm_pack_hash.py`, `norm_rule_eligibility.py` | `rules/pack.py`, `data/rules.json` | неутверждённое правило не опровергает в профиле `strict` |
| Машина состояний разбора человеком | AeroBIM: `domain/review_state_machine.py` | `review/state_machine.py` | банковские роли, основание обязательно |
| Псевдонимизация HMAC | AeroBIM: `domain/hybrid/privacy_guard.py`, `sensitive_entities.py` | `privacy/pii.py` | добавлены ИНН и СНИЛС с контрольными числами, паспорт РФ — в AeroBIM их не было |
| OpenAI-совместимый клиент с белым списком хостов и маркерами данных | AeroBIM: `infrastructure/adapters/openai_compat_llm_provider.py` | `advisory/llm.py` | без облачного хоста по умолчанию, псевдонимизация перед отправкой |
| Интервал Вилсона, объём выборки, κ Коэна | AeroBIM: `domain/study_design.py`, `eval_statistics.py` | `eval/metrics.py` | только нужные функции |
| Журнал решений: хеш-цепочка, HMAC, свидетель `.head`, внешний якорь | SynAPS-ProgramPlan: `src/synaps_programplan/journal.py` | `audit/journal.py` | банковские действия |
| Пояснение моделью только по фактам, фильтр чисел и дат | SynAPS-ProgramPlan: `src/synaps_programplan/copilot.py` | `advisory/copilot.py` | факты — находки отчёта |
| Реестр утверждений и запрещённые формулировки | SynAPS-ProgramPlan: `CLAIMS_REGISTRY.md`, `BANNED_CLAIMS.txt`, `scripts/lint_claims.py` | те же файлы | проверка без учёта регистра |
| `evidence.json`: каждое число со слайда — с командой воспроизведения | SynAPS-ProgramPlan: `evidence.json`, `scripts/build_evidence.py` | `scripts/build_evidence.py` | — |
| Удостоверение W3C VC с подписью Ed25519 | fsp-talent: `docs/adr/0007-w3c-verifiable-credentials.md`, `backend/app/services/vc/issuer.py` | `audit/attestation.py` | удостоверение о результате проверки полномочия, без ФИО |
| Контрольное число ИНН | fsp-talent: `backend/app/services/inn.py` | `domain/identifiers.py` | добавлены ОГРН, ОГРНИП, СНИЛС, КПП и генераторы |
| CI с минимальными правами | fsp-talent: `.github/workflows/ci.yml` | `.github/workflows/ci.yml` | — |
| Трассировка «норма → реализация → тест» | OpenRebar: `docs/NORMATIVE_TRACEABILITY.md` | `docs/RULES.md`, тест `test_every_rule_traces_to_an_existing_test` | генерируется из пакета, тест не даёт разойтись |
| Хеш-набор доказательств, явные границы утверждений | OMEGA-MATH: `scripts/verify_evidence.py`, протокол claim boundary | `reproducibility_hash`, `LIMITS.md` | — |

## Что добавлено в 0.2.0 по ревизии мирового уровня (не из репозиториев автора)

| Идея | Откуда | Где в «Мандате» |
|---|---|---|
| Списки статусов удостоверений | W3C Bitstring Status List v1.0 | `audit/status_list.py`, ADR-007 |
| Дерево Меркла и доказательство включения | RFC 6962 / RFC 9162 (прозрачные журналы) | `audit/merkle.py`, ADR-007 |
| Независимая проверка ядра правил | Cedar, verification-guided development | `rules/cedar_export.py`, `policies/cedar/`, ADR-006 |
| Пара «риск — покрытие» | selective classification (Geifman, El-Yaniv, 2017) | `eval/run.py` |

Развивает идеи AeroBIM и fsp-talent: свойство передоверия проверяется так же, как AeroBIM проверяет свёртку вердикта, а удостоверение из fsp-talent получило статус отзыва.

## Что сознательно не взято

- **SynAPS** (оптимизатор расписаний): для очереди из сотен кейсов избыточен; идея диспетчера ATCS — в дорожной карте для очереди исключений.
- **OpenRebar** (C#, армирование): другая предметная область; ImageSharp под Split License не подходит банку.
- **Lime**: только документация, кода нет.
- **OMEGA-MATH**: облачные провайдеры моделей не подходят для контура банка.
- **AeroBIM целиком**: модель находки пронизана IFC; переносились отдельные модули.
