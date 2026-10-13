# Покрытие файлов аудитом

Отправная точка — состояние `main` на 13.10.2026. Число строк в таблице — по этой версии; файлы, изменённые в ходе аудита, помечены, новые файлы — в конце таблицы.

Глубина: «полностью», «выборочно» (с указанием фрагмента), «проверен прогоном» или «не читался». Непрочитанных файлов кода, скриптов и тестов нет. Часть из них прочитана выборочно, фрагмент указан в строке; сгенерированные примеры (`examples/`) проверялись прогоном, а не чтением.

Дополнительно прочитаны: `pyproject.toml` (полностью), `.github/workflows/ci.yml` (команды `run:`), `Makefile` (цели).

| Файл | Строк | Глубина |
|---|---:|---|
| `examples/cases/01_ok/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/01_ok/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/01_ok/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/01_ok/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/02_director_without_poa/case.json` | 35 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/02_director_without_poa/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/02_director_without_poa/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/03_subdelegation_ok/case.json` | 46 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/03_subdelegation_ok/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/03_subdelegation_ok/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/03_subdelegation_ok/poa-child.xml` | 1 | пересобран генератором (`mandat examples`), построчно не читался |
| `examples/cases/03_subdelegation_ok/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/04_subdelegation_forbidden/case.json` | 46 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/04_subdelegation_forbidden/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/04_subdelegation_forbidden/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/04_subdelegation_forbidden/poa-child.xml` | 1 | пересобран генератором (`mandat examples`), построчно не читался |
| `examples/cases/04_subdelegation_forbidden/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/05_director_changed/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/05_director_changed/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/05_director_changed/egrul.json` | 29 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/05_director_changed/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/06_text_authority/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/06_text_authority/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/06_text_authority/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/06_text_authority/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/07_revoked/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/07_revoked/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/07_revoked/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/07_revoked/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/08_registry_unavailable/case.json` | 38 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/08_registry_unavailable/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/08_registry_unavailable/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/08_registry_unavailable/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/09_charter_limit/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/09_charter_limit/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/09_charter_limit/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/09_charter_limit/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/10_old_director_poa/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/10_old_director_poa/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/10_old_director_poa/egrul.json` | 29 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/10_old_director_poa/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/11_parent_revoked/case.json` | 46 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/11_parent_revoked/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/11_parent_revoked/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/11_parent_revoked/poa-child.xml` | 1 | пересобран генератором (`mandat examples`), построчно не читался |
| `examples/cases/11_parent_revoked/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/12_no_term/case.json` | 40 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/12_no_term/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/12_no_term/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/12_no_term/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/13_bankruptcy/case.json` | 43 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/13_bankruptcy/charter.txt` | 0 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/13_bankruptcy/egrul.json` | 20 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/cases/13_bankruptcy/poa-root.xml` | 1 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/events/poa_revoked.json` | 26 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `examples/events/takeover.json` | 50 | проверен прогоном: `mandat examples` воспроизводит файл без расхождений |
| `policies/cedar/README.md` | 12 | полностью: число 384 совпадает с tests/test_properties.py |
| `policies/cedar/mandat.cedar` | 46 | полностью: сгенерирован, совпадение с экспортом держит test_cedar_export_is_up_to_date |
| `policies/cedar/mandat.cedarschema` | 16 | полностью: сгенерирован |
| `scripts/bench.py` | 52 | полностью: дефектов нет |
| `scripts/build_evidence.py` | 101 | полностью: число цепочек 384 записано константой, его держит tests/test_properties.py |
| `scripts/doctor.py` | 58 | полностью: дефектов нет |
| `scripts/effect_model.py` | 113 | полностью: отрицательные t_auto и rate не отклоняются (замечание) |
| `scripts/export_cedar.py` | 27 | полностью: дефектов нет |
| `scripts/export_demo_cards.py` | 61 | полностью: число карточек в описании расходилось со списком CARDS; теперь описание числа не называет, а скрипт печатает количество, изменён |
| `scripts/gen_rules_doc.py` | 136 | полностью: дефектов нет |
| `scripts/import_classifier.py` | 34 | полностью: повтор кода теперь отклоняется в import_esnsi |
| `scripts/lint_claims.py` | 54 | выборочно (изменена: сверка «N правил» с пакетом) |
| `src/mandat.egg-info/PKG-INFO` | 276 | не читался: артефакт сборки, в git не хранится (`.gitignore`) |
| `src/mandat.egg-info/SOURCES.txt` | 82 | не читался: артефакт сборки, в git не хранится (`.gitignore`) |
| `src/mandat.egg-info/dependency_links.txt` | 1 | не читался: артефакт сборки, в git не хранится (`.gitignore`) |
| `src/mandat.egg-info/entry_points.txt` | 2 | не читался: артефакт сборки, в git не хранится (`.gitignore`) |
| `src/mandat.egg-info/requires.txt` | 21 | не читался: артефакт сборки, в git не хранится (`.gitignore`) |
| `src/mandat.egg-info/top_level.txt` | 1 | не читался: артефакт сборки, в git не хранится (`.gitignore`) |
| `src/mandat/__init__.py` | 8 | полностью: только описание модуля |
| `src/mandat/advisory/__init__.py` | 5 | полностью: только описание модуля |
| `src/mandat/advisory/explainer.py` | 139 | полностью: фразы «уполномочен», «имеет полномочия», «наделён полномочиями», «правомочен» проходили фильтр при неподтверждённом вердикте; исправлено, тест `test_explainer_drops_authority_wording_when_verdict_is_not_confirmed`, изменён |
| `src/mandat/advisory/gate.py` | 74 | выборочно (ground_candidates; изменён) |
| `src/mandat/advisory/llm.py` | 97 | полностью (изменён) |
| `src/mandat/api/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/api/app.py` | 226 | выборочно (Guard, create_app, маршруты, create_case с журналом; изменён) |
| `src/mandat/api/principals.py` | 125 | выборочно (таблица ролей) |
| `src/mandat/api/storage.py` | 240 | полностью: окно лимита во всех трёх хранилищах одинаково включает границу 60 с; запись кейса не условная (FIX_PLAN, п. 1); Redis в `first_seen` не учитывает переданное `now` и округляет срок вниз до целых секунд, не меньше 1; очистка окон в памяти удаляет только устаревшие окна, поэтому при одновременной активности более 10 000 адресов словарь растёт. Замечания, не дефекты |
| `src/mandat/audit/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/audit/attestation.py` | 152 | полностью (переписаны подпись, проверка и токен представителя) |
| `src/mandat/audit/journal.py` | 246 | выборочно (изменены: append, блокировка, проверка истории перед записью; прочитан verify_journal) |
| `src/mandat/audit/merkle.py` | 81 | полностью: корень и доказательство включения по RFC 6962, проверка по RFC 9162, разд. 2.1.3.2, расхождений не найдено |
| `src/mandat/audit/status_list.py` | 270 | полностью: `mark` выставлял бит до проверки индекса, а отрицательный индекс адресовал конец строки; исправлено, тест `test_mark_of_unissued_index_changes_nothing` и мутационный контроль, изменён. Замечания: `check_status` строит путь к файлу списка из `statusPurpose` удостоверения (подмену сдерживает проверка `id` и подписи); подписанный список без `sequence` при `seen=None` не сверяется с состоянием издателя |
| `src/mandat/cli.py` | 427 | выборочно (список команд, demo, attest-verify; изменён) |
| `src/mandat/data/__init__.py` | 24 | выборочно (изменённые строки) |
| `src/mandat/data/authority_classifier.json` | 69 | полностью: 6 синтетических кодов, повторов нет |
| `src/mandat/data/effect_scenarios.json` | 34 | полностью: доли и минуты — гипотезы; число клиентов взято из пересказа, первичный источник не открыт (так и указано в файле) |
| `src/mandat/data/emchd_003_mapping.json` | 126 | полностью (сверен с XSD EMCHD_1: пути, перечни, ветвь передоверия, реквизиты нотариального удостоверения) |
| `src/mandat/data/operations.json` | 78 | полностью: коды синтетические; SYN-BANK-PAY-SIGN-OLD намеренно отсутствует в классификаторе |
| `src/mandat/data/rules.json` | 51 | выборочно (записи изменённых и новых правил) |
| `src/mandat/domain/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/domain/graph.py` | 155 | выборочно (applicable_checks, _represents, начало build_chain; изменён) |
| `src/mandat/domain/identifiers.py` | 124 | выборочно (_digits, normalize_snils, comparable_inn/snils; изменён) |
| `src/mandat/domain/model.py` | 280 | выборочно (Person, PowerOfAttorney, Case, TextDocument, IntakeError; изменены) |
| `src/mandat/domain/verdict.py` | 207 | выборочно (CHECK_LABEL_RU, профили, profile_gap, fold; изменён) |
| `src/mandat/domain/verdict_oracle.py` | 79 | полностью: оракул не полностью независим, импортирует effective_effect и profile_gap из verdict; дефектом не считается |
| `src/mandat/eval/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/eval/metrics.py` | 42 | полностью: границы Уилсона верны (верхняя граница 1,32 % при 288 отрицательных случаях); каппа при ожидаемом согласии 1 возвращает 1,0 |
| `src/mandat/eval/run.py` | 66 | полностью: покрытие — доля вердиктов «подтверждено» и «опровергнуто»; дефектов нет |
| `src/mandat/eval/synth.py` | 525 | выборочно (World.make, DEFECTS, DERIVED, _derive, сборка цепочек; изменён) |
| `src/mandat/examples.py` | 155 | полностью: дефектов нет |
| `src/mandat/intake/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/intake/case_loader.py` | 185 | выборочно (разбор манифеста, документы, реестр, отметки; изменён) |
| `src/mandat/intake/charter.py` | 160 | полностью: эвристика только отправляет кейс на разбор, строгий профиль без отметки устава не подтверждает; дефектов нет |
| `src/mandat/intake/egrul.py` | 93 | полностью: тип `grn` не проверяется; строка "false" читается как истина (безопасное направление); порядок valid_from и valid_to не проверяется. Замечания, не дефекты |
| `src/mandat/intake/emchd.py` | 370 | полностью (пространство имён, ветвь передоверия, перечни значений, нотариальное удостоверение; писатель дополнен обязательными кодами) |
| `src/mandat/intake/formats.py` | 63 | полностью: дефектов нет |
| `src/mandat/intake/xml_safety.py` | 59 | полностью: defusedxml без DTD и сущностей, лимиты размера, числа элементов, глубины и длины текста |
| `src/mandat/intake/xpath.py` | 80 | выборочно (изменены: _walk, read) |
| `src/mandat/ports.py` | 142 | выборочно (BankruptcyStatus, порты банкротства, RegistryObservation, FixtureRegistry; изменены) |
| `src/mandat/privacy/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/privacy/pii.py` | 76 | выборочно (назначение и ключ псевдонимизации) |
| `src/mandat/report/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/report/html.py` | 132 | полностью: все значения экранируются; дефектов нет |
| `src/mandat/review/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/review/state_machine.py` | 129 | выборочно (изменены: ReviewCase.apply и запись решения в журнал; таблица переходов прочитана) |
| `src/mandat/rules/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/rules/cedar_export.py` | 106 | полностью: экспорт берёт `date.today()`, поэтому результат зависит от дня запуска (замечание) |
| `src/mandat/rules/checks.py` | 1429 | выборочно (emit, RULES, R-ID-001/002/005, R-EIO-002/004/005, банкротство, R-CHR-002, R-FMT-001, R-REG-001, R-SUB-006/009/010, R-SIG-002, run_rules; изменены) |
| `src/mandat/rules/classifier.py` | 124 | полностью: повтор кода в from_payload и import_esnsi молча оставлял последнюю запись; исправлено, тест `test_repeated_classifier_code_is_rejected`, изменён. Открыт вопрос юристу: дата REVOKED включается в срок действия кода или нет |
| `src/mandat/rules/pack.py` | 171 | выборочно (CHECKS, validate_pack; изменён) |
| `src/mandat/services/__init__.py` | 1 | полностью: только описание модуля |
| `src/mandat/services/monitoring.py` | 232 | выборочно (сигнатуры функций) |
| `src/mandat/services/next_steps.py` | 75 | выборочно (strongest_objection, build_next_steps) |
| `src/mandat/services/rights.py` | 39 | полностью: valid_until берётся только из последнего звена; для подтверждённых цепочек это держит R-SUB-003 (замечание) |
| `src/mandat/services/verify.py` | 262 | выборочно (verify_case, выбор профиля; изменён) |
| `src/mandat/services/webhooks.py` | 126 | полностью (изменён) |
| `tests/conftest.py` | 107 | выборочно (изменённые тесты) |
| `tests/test_api_users.py` | 112 | выборочно (изменённые тесты) |
| `tests/test_audit_fixes.py` | 106 | выборочно (изменённые тесты) |
| `tests/test_regressions.py` (прежнее имя `test_audit_regressions.py`) | 778 | полностью: тесты названы по смыслу, удалён запасной импорт несуществующего модуля, изменён. Замечания: `test_amount_with_kopecks_is_not_truncated` и `test_revocation_without_number_is_not_silently_dropped` допускают и отказ, и обработку, поэтому проходят при любом из двух исправлений |
| `tests/test_audit_status.py` | 198 | полностью (добавлен тест на повтор кода классификатора) |
| `tests/test_cedar.py` | 57 | выборочно (изменённые тесты) |
| `tests/test_core.py` | 213 | выборочно (изменённые тесты) |
| `tests/test_interfaces.py` | 187 | выборочно (изменённые тесты) |
| `tests/test_properties.py` | 34 | полностью: полный перебор 384 цепочек; строгий профиль проверяется на каждой седьмой |
| `tests/test_reaudit.py` | 162 | выборочно (изменённые тесты) |
| `tests/test_rules.py` | 532 | выборочно (изменённые тесты) |
| `tests/test_serve_guard.py` | 37 | полностью: дефектов нет |
| `tests/test_services.py` | 338 | полностью: дефектов нет; добавлены тесты на формулировки полномочий |
| `tests/test_status_privacy.py` | 44 | полностью: проверки случайности вероятностные, вероятность ложного падения пренебрежимо мала; добавлены тест на невыданный индекс и мутационный контроль |
| `tests/test_storage.py` | 132 | выборочно (изменённые тесты) |
| `tests/test_webhooks.py` | 115 | выборочно (изменённые тесты) |

Новые файлы:

| Файл | Глубина |
|---|---|
| `src/mandat/audit/jcs.py` | полностью (новый) |
| `src/mandat/services/env_policy.py` | полностью (новый) |
| `tools/redteam/common.py` | полностью |
| `tools/redteam/fuzz_mutations.py` | выборочно (мутации, давшие расхождения, и разбор XML в них) |
| `tests/test_attestation_validation.py` | полностью (новый) |
| `tests/test_chains_and_signatures.py` | полностью (новый) |
| `tests/test_false_confirmed_paths.py` | полностью (новый) |
| `tests/test_hardening.py` | полностью (новый) |
| `tests/test_jcs.py` | полностью (новый) |
| `tests/test_journal_transaction.py` | полностью (новый) |
| `tests/test_redteam_fuzz.py` | полностью (новый) |
| `tests/test_emchd_format.py` | полностью (новый) |
| `tests/data/emchd/direct.xml`, `tests/data/emchd/transfer.xml` | полностью (новые, проверены по XSD EMCHD_1) |
| `docs/audit/LEGAL_TRACEABILITY.md`, `THREAT_AND_TRUST_BOUNDARIES.md`, `REGRESSION_MATRIX.md`, `FIX_PLAN.md`, `READINESS_GATES.md`, `SOURCE_LEDGER.md` | записи аудита |
