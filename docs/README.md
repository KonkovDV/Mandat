# Карта документов

Какой файл открыть, чтобы ответить на свой вопрос. Если есть три минуты, достаточно [README](../README.md), [сценария показа](DEMO.md) и [реестра утверждений](../CLAIMS_REGISTRY.md).

## По роли

| Кто читает | С чего начать | Дальше |
|---|---|---|
| Жюри | [README](../README.md) | [реестр утверждений](../CLAIMS_REGISTRY.md), [конкуренты](COMPETITORS.md), [оценка](EVALUATION.md) |
| Юрист банка | [вопросы юристу](LEGAL_REVIEW.md), [нормы](REGULATORY.md) | [правила](RULES.md), [формат МЧД](FORMAT_NOTES.md), [предметная модель](DOMAIN.md) |
| ИБ (информационная безопасность) банка | [модель угроз](THREAT_MODEL.md), [аудит октября 2026](audit/AUDIT.md) | [защита данных](DATA_PROTECTION.md), [риски ИИ](AI_RISKS.md), [SECURITY](../SECURITY.md) |
| Владелец процесса | [пилот](PILOT.md) | [экономика](ECONOMICS.md), [дорожная карта](ROADMAP.md) |
| Инженер | [запуск и API](RUN.md) | [архитектура](ARCHITECTURE.md), [каркас MVP](MVP.md), [ключи](KEYS.md), [перенос идей](PORTING.md) |

## По вопросу

| Вопрос | Файл |
|---|---|
| Какую цифру можно сказать вслух | [реестр утверждений](../CLAIMS_REGISTRY.md) |
| Чего продукт пока не делает | [границы](../LIMITS.md) |
| Что уходит в модель и что остаётся персональными данными | [защита данных](DATA_PROTECTION.md) |
| Как человек разбирает кейс | [разбор](REVIEW.md) |
| Откуда коды полномочий | [классификатор](CLASSIFIER.md) |
| Где лежат ключи стенда и что нужно к пилоту | [ключи](KEYS.md) |
| Какая норма за каким правилом | [правила](RULES.md), [трассировка норм](audit/LEGAL_TRACEABILITY.md) |
| Почему смена руководителя не гасит доверенность | [ADR-002](adr/ADR-002-director-change.md) |
| Почему модель не выносит вердикт | [ADR-001](adr/ADR-001-verdict-ownership.md) |
| Почему Cedar, а не OpenFGA | [мировой уровень](SOTA.md) |
| Насколько проект готов | [ступени готовности](audit/READINESS_GATES.md) |
| Что говорят открытые источники и чего не хватает | [OSINT](research/OSINT-2026-10-14.md), [SOTA](research/SOTA-2026-10-14.md), [матрица требований](research/REQUIREMENTS_MATRIX.md), [Murder Board](audit/MURDER_BOARD-2026-10-14.md), [SOTA 15.10](research/SOTA-2026-10-15.md), [идеи из репозиториев](research/PORTFOLIO-IDEAS-2026-10-15.md), [контракт API](api/openapi.json) |
| Что нашёл аудит | [сверка 16.10](audit/AUDIT-2026-10-16-R7.md), [аудит октября 2026](audit/AUDIT.md), [аудит R3](audit/AUDIT-2026-10-14-R3.md), [аудит R4](audit/AUDIT-2026-10-15-R4.md), [глубокий проход 15.10](audit/AUDIT-2026-10-15-R5.md), [R5.1](audit/AUDIT-2026-10-15-R5.1.md), [R6 docs](audit/AUDIT-2026-10-15-R6-DOCS.md), [правовая база](research/LEGAL-BASIS-2026-10-15.md), [план 315-ФЗ](research/MIGRATION-315-FZ-R-FMT-001.md), [мутационное тестирование](testing/MUTATION.md); история: [первый аудит](AUDIT_2026-10.md), [закрытие аудита 12.10](AUDIT_CLOSURE.md), [расхождения презентации и кода](SUBMISSION_DRIFT.md) |
| Что и когда сдаём | [сдача](../submission/README.md) |
| Откуда источники | [источники](SOURCES.md), [реестр источников](audit/SOURCE_LEDGER.md), [литература](LITERATURE.md) |
| Как говорить с пользователями | [discovery](discovery/) |

Все решения архитектуры кратко — в [docs/adr](adr/).
