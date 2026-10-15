# Идеи из репозиториев KonkovDV, 15.10.2026

Изучены 20 собственных публичных репозиториев KonkovDV: неглубокий клон main, файлы верхнего уровня, CI, тесты и документы. 27 форков клиентов Ethereum, спецификаций и тестов (go-ethereum, reth, consensus-specs и другие) не разбирались: это чужой код без доработок под «Мандат». Приватных репозиториев у KonkovDV нет: токен с доступом показывает 46 репозиториев, все публичные (проверено 15.10.2026, см. «Дополнение R5»).

Статусы:
- **взято** — перенесено в «Мандат» с тестом;
- **уже есть** — в «Мандате» это уже сделано;
- **отклонено** — с указанием причины.

| Репозиторий | Идея | Статус | Почему, где |
|---|---|---|---|
| Kontur | Зафиксированный контракт API (`contracts/openapi.yaml`) и проверка, что приложение с ним не разошлось | **взято** | `docs/api/openapi.json`, `scripts/export_openapi.py`, `test_committed_openapi_matches_app` |
| Kontur | Каждая операция API защищена, кроме явного списка открытых | **взято** | `test_every_route_except_health_requires_token`: новый маршрут без `Depends(auth)` ломает тест. `test_read_only_role_cannot_call_writing_routes` проверяет, что аудитор не может писать |
| Kontur | Офлайн-профиль compose, папка vendor | уже есть | ядро не ходит в сеть, модель по умолчанию выключена |
| SynAPS-ProgramPlan | `CLAIMS_REGISTRY.md`, `BANNED_CLAIMS.txt`, `evidence.json`, lint_claims | уже есть | перенесено раньше ([PORTING](../PORTING.md)) |
| AeroBIM | Происхождение у каждого вывода (finding provenance) | уже есть | `EvidenceRef`: XPath, ГРН, норма |
| AeroBIM, SynAPS, OpenRebar | Подписанные аттестации сборки, SBOM | уже есть | задача `package` в CI: CycloneDX и SLSA provenance |
| SynAPS-RepairFlow | Независимый «нотариус»: отдельный код сверяет готовый результат с исходной задачей | уже есть | Cedar независимо проверяет свойство передоверия; удостоверения проверяются отдельно от движка |
| SynAPS-RepairFlow | Мутационное тестирование (mutmut) ядра по ручному запуску | принято в R5 | в R4 отложено (причина была в объёме раунда); в R5 сделано, см. «Дополнение R5» |
| fsp-talent | gitleaks в CI | отклонено | в «Мандате» нет секретов в git (R3: Trivy secret, `var/` в `.gitignore`); новое действие расширяет цепочку поставок CI. Trivy fs secret гоняем вручную при аудите |
| fsp-talent | Подпись образа cosign | отклонено | образ не публикуется в реестр; провенанс пакета уже подписан |
| OpenRebar | actionlint для workflow | принято в R5 | в R4 отложено (причина была в объёме раунда); в R5 сделано, см. «Дополнение R5» |
| OpenRebar, SynAPS | `RELEASE_POLICY.md`, `MAINTAINERS.md`, `AGENTS.md` | отклонено | проект хакатона с одной командой; правила вклада уже в CONTRIBUTING |
| open-integrity-twin | `REDTEAM.md` с разделами «подтверждено / частично / вне области», `MODEL_CARD`, `DATA_CARD` | уже есть | [Murder Board](../audit/MURDER_BOARD-2026-10-14.md), [EVALUATION](../EVALUATION.md), [AI_RISKS](../AI_RISKS.md), [LIMITS](../../LIMITS.md) |
| RailBreak | `POSTMORTEM.md`, `RELEASE_CHECKSUMS.txt`, `PROVENANCE.md` | отклонено | постмортем пишется после финала; контрольные суммы и происхождение даёт задача `package` |
| RailBreak, Mandat | Свойства через Hypothesis | уже есть | `hypothesis` в `dev.lock` |
| gptheist | Цепочка агентов, где любой может остановить решение | уже есть по смыслу | любое правило с эффектом «нет» или «на разбор» останавливает «подтверждено» (ADR-001) |
| OMEGA-MATH | Каждое утверждение прослеживается до доказательства; реестр проверяется в CI | уже есть | lint_claims и `evidence.json` |
| Lime | Сначала документы, потом код | неприменимо | у «Мандата» уже есть код |
| brinevalue-os, wellguard-os, drillguard-os | Только советующий режим без пути управления, `run_tests.py` | уже есть по смыслу | модель только предлагает факт, вердикт выносят правила |
| SynAPS-SignPlan, SynAPS-MobiRoute, SynAPS-GridPlan | JSON Schema входа (`schemas/*-input-v1.json`) | принято в R5 | в R4 отложено (причина была в объёме раунда); в R5 сделано, см. «Дополнение R5» |
| hod-mysli-2026-alphabet | Формальные доказательства на Lean | отклонено | для правил с нормами права формальная модель — это Cedar; Lean избыточен |
| public-disclosures | Отчёты об уязвимостях | неприменимо | — |


## Дополнение R5 (15.10.2026)

- Приватных репозиториев нет. С токеном API показывает 46 репозиториев KonkovDV, все публичные (20 своих, остальные — форки). Изучать дополнительно нечего.
- Три идеи, отложенные в R4, приняты в R5:
  - JSON Schema манифеста кейса — `docs/api/case-manifest.schema.json` и тесты `tests/test_r11_manifest_schema.py`;
  - actionlint в CI — шаг в задаче test на 3.12, релиз закреплён по SHA-256;
  - точечное мутационное тестирование `rules/checks.py` — [docs/testing/MUTATION.md](../testing/MUTATION.md).
