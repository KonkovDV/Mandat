# Матрица регрессий

Матрица составлена при прогоне `python -m pytest -p no:warnings -o addopts="" -q`: 571 passed, 0 skipped, 0 failed (Python 3.13.5, pytest 9.1.1). Число тестов последнего полного прогона — в `evidence.json` (поле `tests.passed`). Ниже — семейства сценариев, почему ожидается именно такой исход и какой тест это держит. Номера находок AUD-xx — из `AUDIT.md`.

## Сценарии по семействам

| Семейство | Сценарий | Ожидание и почему | Тест |
|---|---|---|---|
| Идентичность и организация | ИНН и СНИЛС представителя от разных людей | разбор: по ст. 185 ГК РФ представитель — лицо, указанное в доверенности, сверка по всем идентификаторам | `test_r_id_005_inn_and_snils_of_different_people`, `test_foreign_inn_with_representative_snils` |
| | ОГРН из выписки другой компании | опровергнуто | `test_r_id_002_ogrn_of_another_company_refutes`, `test_ogrn_of_other_company` |
| | СНИЛС из нецифровых символов | конфликт, не совпадение | `test_identity_*` |
| | Руководитель сам, СНИЛС не с чем сверить | разбор | `test_eio_direct_with_snils_of_other_person`, `test_eio_direct_*` |
| Срок и история | Срок не указан | год со дня совершения (ст. 186 ГК РФ), разбор | `test_r_term_001_no_term_defaults_to_one_year` |
| | Срок истёк, в том числе год по умолчанию | опровергнуто | `test_r_term_002_*` |
| | Новый руководитель назначен 3 дня назад, запись о прежнем открыта | разбор | `test_new_director_while_old_record_open` |
| | МЧД подписана прежним руководителем после назначения нового | разбор | `test_poa_signed_by_old_director_after_new_appointment` |
| | Отмена в реестре после даты операции | разбор, не опровержение | `test_registry_revoked_after_historical_operation_goes_to_review` |
| | Снимок «действует» старше операции на срок свежести | «не проверено» | `test_registry_active_snapshot_older_than_operation_is_stale` |
| Граф передоверия | Передоверие запрещено, шире, дольше, второе при однократном | опровергнуто (ст. 187 ГК РФ) | `test_r_sub_001_*`, `test_r_sub_002_*`, `test_r_sub_003_*`, `test_once_subdelegation_cannot_be_used_twice` |
| | Отмена основной МЧД | передоверие теряет силу (п. 3 ст. 188) | `test_r_sub_006_parent_revoked_cascades` |
| | Подписант передоверия не представитель родительской МЧД | опровергнуто | `test_r_sub_009_child_signer_must_match_parent_representative` |
| | Нет нотариата, передоверитель — физлицо | разбор (п. 3 ст. 187) | `test_subdelegation_without_form_evidence_goes_to_review` |
| | Пустой `СвНотУд` или без реквизитов | разбор и пробел формата | `test_notarization_without_details_is_not_accepted` |
| | Вид передоверителя в МЧД расходится с родительской | вид не определяется | `test_subdelegator_kind_conflicting_with_the_parent_is_unknown` |
| | Несколько МЧД без выбора | разбор | `test_several_poas_*` |
| | Сужение объёма на всех 384 двухзвенных цепочках, сверка с Cedar | «подтверждено» только при разрешённом и не более широком передоверии | `test_subdelegation_never_widens_authority_exhaustive`, `test_cedar_and_engine_agree_on_every_chain` |
| Документ, схема, криптография | XML-бомба, битый XML | «недостаточно данных» | `test_r_fmt_001_xml_bomb_rejected`, `test_r_fmt_001_bad_xml_is_insufficient` |
| | Повтор одиночного элемента МЧД | документ не читается | `test_repeated_single_element_in_poa_is_not_read_silently` |
| | Документ в пространстве имён EMCHD_1, ветвь передоверия, чужое пространство имён, значения вне перечня | разбор по XSD | `tests/test_emchd_format.py` |
| | Каноническая запись RFC 8785 | совпадение с примерами RFC | `tests/test_jcs.py` |
| Нормы и операции | Операция не покрыта полномочиями | опровергнуто | `test_r_cov_001_not_covered_refutes` |
| | Код полномочия исключён из классификатора или без даты начала | разбор | `test_r_cov_002_revoked_code_needs_review`, `test_code_without_start_date_is_not_in_force` |
| | Текстовые полномочия, совместные полномочия, нет признака совместности, бумажная доверенность | разбор | `test_r_cov_001_text_only_needs_review`, `test_r_jnt_001_joint_powers_needs_review`, `test_missing_joint_flag_on_one_representative_is_not_confirmed`, `test_r_pap_001_paper_poa_needs_review` |
| | Обязательный устав с найденным фрагментом; разные передоверия в один день; поздний снимок отмены без даты | разбор | `test_partial_required_charter_blocks_confirmation`, `test_same_day_sibling_transfers_need_review`, `test_later_revocation_snapshot_without_event_date_goes_to_review` |
| | Устав не размечен или есть лимит | разбор | `test_missing_charter_blocks_confirmation`, `test_r_chr_001_limit_needs_review` |
| | Строгий профиль на полном переборе | ни одного «подтверждено» без проверенной подписи | `test_no_false_confirmed_in_strict_profile_exhaustive`, `test_r_sig_001_strict_profile_is_insufficient` |
| Банкротство и прекращение | Ликвидация, конкурс | опровергнуто | `test_r_eio_004_liquidated_refutes`, `test_r_eio_004_bankruptcy_terminating_refutes` |
| | Наблюдение, статус вне словаря | разбор | `test_r_eio_004_observation_needs_review`, `test_r_eio_004_unknown_bankruptcy_status_goes_to_review` |
| | Нет сведений о банкротстве в strict | не подтверждено | `test_r_bnk_001_missing_bankruptcy_blocks_strict_confirmation` |
| API, хранилище, разбор, журнал | Профиль demo через API вне demo/dev/test | 403 | `test_profile_*` |
| | Глубокий JSON, большое тело | 400 или 413 | `test_api_body_*` |
| | Два решения по одной версии одновременно | одна запись | `test_two_concurrent_decisions_on_one_version_write_one_record` |
| | Сбой журнала или хранилища | без журнала отчёт не сохраняется | `test_api_journal_failure_stores_nothing`, `test_api_store_failure_after_journal_leaves_audit_trail` |
| | Чужой кейс | не виден | `test_owner_sees_own_case_others_do_not` |
| Удостоверения, статус, вебхуки | Чужой издатель, просроченное, demo, отозванное | не принимается | `tests/test_attestation_validation.py` |
| | Удостоверение прежнего формата | подпись верна, не принимается | `test_attestation_signed_before_rfc8785_is_not_accepted_without_validity_end` |
| | Ключ HMAC короче 32 байт, права файла ключа | отказ, 0600 | `test_short_hmac_key_is_rejected`, `test_new_key_files_are_created_private_and_never_overwritten` |
| | Повтор или подделка вебхука | отказ | `tests/test_webhooks.py` |
| Приватность и модель | Индексы статуса | случайные и уникальные | `tests/test_status_privacy.py` |
| | Числа от модели, хост модели, маркер конца данных в документе | отказ | `test_llm_and_webhook_*` |
| | Правило упало с исключением | «не проверено», не «подтверждено» | `test_rule_crash_becomes_not_verified` |

## Мутационный фазз

`tests/test_redteam_fuzz.py`: 8814 мутаций в двух вариантах базы. В первом варианте ложных «подтверждено» нет. Во втором ровно 24 нейтральных случая, закреплённых поимённо; 18 случаев открытого вопроса о должности подписанта никогда не подтверждаются (`test_open_questions_are_never_confirmed`).

## Синтетический корпус

`mandat eval --per-defect 6`: 294 кейса, 49 видов дефектов, профиль demo. Ложных «подтверждено» 0; верхняя граница 95 % по Уилсону считается по 288 кейсам, где верный ответ не «подтверждено», и равна 1,32 %; доля решений без человека 38,8 %. В строгом профиле покрытие 0. Это проверка логики правил на синтетике, а не точность на документах банка: экспертной разметки, отложенной выборки и сравнения с базовым процессом нет (`READINESS_GATES.md`).

## Пропуски

В этом окружении пропусков нет. Условные пропуски в коде: `test_regressions.py` — если в репозитории нет презентации; `test_hardening.py` — FIFO вне POSIX и недоступные символические ссылки.

## Контроль мутацией

Способ: перед сбором тестов правило заменялось на функцию, которая ничего не находит (`checks.RULES[id] = lambda ctx: []`), по одному правилу за раз. Прогонялись `tests/test_false_confirmed_paths.py` и `tests/test_hardening.py`. Ниже правила, отключение которых роняет тест.

| Тест | Правило, отключение которого роняет тест |
|---|---|
| `test_new_director_while_old_record_open` | R-EIO-002 |
| `test_foreign_inn_with_representative_snils` | R-ID-005 |
| `test_ogrn_of_other_company` | R-ID-002 |
| `test_missing_poa_field[ОГРН]` | R-ID-002 |
| `test_missing_poa_field[СрокДейст]` | R-TERM-001 |
| `test_bankruptcy_status_outside_dictionary[*]` (5 вариантов) | R-EIO-004 |
| `test_r_bnk_001_missing_bankruptcy_blocks_strict_confirmation` | R-BNK-001 |
| `test_charter_mark_client_charter_mark_ignored_in_protected_mode` | R-CHR-002 |
| `test_base_cases_are_confirmed[ok_charter_cleared]` | R-REG-001, R-EIO-001 (группа без SUPPORTS становится `missing`) |
| `test_base_cases_are_confirmed[ok_director]` | R-FMT-001, R-EIO-005, R-COV-001 |

Не убиваются отключением одного правила: `test_missing_poa_field` для `НомДовер`, `ДатаВыдДовер`, `ИННЮЛ`. Отсутствие этих полей ловят несколько правил или свёртка (группа без подтверждения). Проверка парами правил не выполнялась.

Тесты идентификаторов, профиля, тела запроса, приёма документов, модели и вебхуков (AUD-01, AUD-02, AUD-09, AUD-10, AUD-15) проверяют функции и границы API, а не правила пакета, поэтому к этому способу мутации не относятся.

Мутационный прогон остального набора тестов не выполнялся.
