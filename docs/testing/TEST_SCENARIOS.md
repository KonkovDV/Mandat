# Сценарии тестирования

Матрица сценариев по ролям с трассировкой к правилам и автотестам. Синтетический корпус (`mandat.eval.synth`, 49 видов дефектов) прогоняется целиком в `tests/test_core.py`, `tests/test_rules.py` и `mandat eval`; мутационный фаззинг входа — `tests/test_redteam_fuzz.py`. Ниже — что покрыто и где.

## 1. Оператор банка

| # | Сценарий | Ожидание | Тест |
|---|---|---|---|
| O-1 | Корректная МЧД, руководитель по ЕГРЮЛ, операция покрыта | «на разбор» в demo (КЭП не проверена), «подтверждено» при снятом уставе | корпус: `ok`, `ok_charter_cleared` |
| O-2 | Руководитель действует без доверенности | на разбор, нужна подпись распоряжения (R-SIG-002) | корпус `ok_director`; `test_eio_direct_needs_instruction_signature_in_strict` |
| O-3 | Каждый вид вердикта | confirmed / needs_review / refuted / insufficient_data | таблица 5 ниже |
| O-4 | HTML-карточка: следующий шаг, источники, статус draft, доступность | WCAG 2.2 AA контраст, `scope`, `caption`, `lang` | `tests/test_r2_card.py` |
| O-5 | Повтор кейса с тем же `case_id` | 409; `replace=true` — перезапись | `tests/test_r2_races.py::test_live_claim_blocks_and_expired_claim_does_not` |

## 2. Представитель клиента

| # | Сценарий | Ожидание | Тест |
|---|---|---|---|
| C-1 | МЧД истекла | опровергнуто (R-TERM-002) | корпус `expired`, `no_term_expired` |
| C-2 | МЧД отозвана в реестре | опровергнуто (R-REG-001) | корпус `revoked`; `test_r_reg_001_revoked_refutes` |
| C-3 | Передоверие: корректное, шире, дольше, запрещено, без родителя, отозван родитель, передоверитель утратил полномочия | на разбор / опровергнуто / нет данных по R-SUB-001…010 | корпус `ok_subdelegation`, `child_wider`, `child_longer`, `subdelegation_forbidden`, `missing_parent`, `parent_revoked`, `lost_on_subdelegation`, `long_chain*`, `sub_*` |
| C-4 | Смена руководителя после выдачи МЧД | сигнал, на разбор (R-EIO-002) | корпус `director_changed`, `director_changed_long_ago`, `fresh_director_admin` |
| C-5 | Ликвидация, реорганизация, прекращение юрлица, банкротство | опровергнуто / на разбор (R-EIO-004, R-BNK-001) | корпус `liquidated`; `tests/test_r2_legal.py` (реорганизация, прекращение) |
| C-6 | Устаревший снимок ЕГРЮЛ, нет статуса, реестр недоступен | недостаточно данных (R-EIO-006, R-REG-001) | корпус `egrul_stale`, `egrul_no_status`, `registry_unavailable` |
| C-7 | Операция не покрыта полномочием, текстовые полномочия, совместные | опровергнуто / на разбор | корпус `not_covered`, `text_authority`, `joint_powers` |

## 3. Нарушитель

| # | Сценарий | Ожидание | Тест |
|---|---|---|---|
| A-1 | Подложная МЧД: чужой ОГРН, неверная контрольная сумма, чужой представитель, подпись не ЕИО | опровергнуто | корпус `ogrn_of_other_company`, `bad_checksum`, `wrong_representative`, `signer_not_eio`, `snils_of_other_person` |
| A-2 | XML-бомба, битый XML, сверхглубокий JSON, превышение размера | отказ без падения | корпус `xml_bomb`, `malformed_xml`; `test_hardening.py`; `test_redteam_2026_10_14.py::test_rt06_*` |
| A-3 | Подмена файла кейса ссылкой после проверки пути, рост файла после fstat, FIFO | отказ (S-4) | `tests/test_r2_races.py` |
| A-4 | Двойник-документ, повтор ЕГРЮЛ, номер-двойник | недостаточно данных | корпус `duplicate_egrul`, `same_number_twin`, `leaf_tie` |
| A-5 | Подбор токена, отсутствие токена, чужой кейс | 401 / 404, лимит 429 | `test_api_users.py`, нагрузочный `--overload` |
| A-6 | Подделка журнала между записями | следующая запись отвергается | `tests/test_r2_races.py::test_journal_tampering_between_appends_is_still_caught` |
| A-7 | Повтор удостоверения, подложенный старый список статусов, удостоверение старым ключом после ротации | отказ | `test_attestation_validation.py`, `tests/test_signing.py::test_rotation_*` |
| A-8 | Гонка одновременных создания и перезаписи | один выигрывает, остальные 409 | `tests/test_r2_races.py::test_many_concurrent_creates_one_wins`, `test_external_audit_2026_10.py` |

## 4. Администратор

| # | Сценарий | Ожидание | Тест |
|---|---|---|---|
| D-1 | Запуск pilot/prod без ключей, с коротким токеном, с открытым PEM | сервис не стартует, код 2 | `test_serve_guard.py`, `test_redteam_2026_10_14.py`, `test_signing.py::test_cli_demo_refuses_plain_key_in_pilot` |
| D-2 | Ротация ключа подписи, отзыв ключа | старые удостоверения проверяются, отозванный ключ — нет | `tests/test_signing.py` |
| D-3 | HSM / Vault / KMS | подпись сверяется с открытым ключом модуля | `tests/test_signing.py` |
| D-4 | Нагрузка, перегрузка, выдержка | без ошибок данных, журнал цел, 429 при лимите | `tools/load/run_load.py`, CI `--smoke` |

## 5. Вердикты по корпусу

| Вердикт | Видов | Дефекты |
|---|---:|---|
| confirmed | 1 | `ok_charter_cleared` |
| insufficient_data | 9 | `duplicate_egrul`, `egrul_no_status`, `egrul_stale`, `leaf_tie`, `malformed_xml`, `missing_parent`, `registry_unavailable`, `same_number_twin`, `xml_bomb` |
| needs_review | 21 | `charter_limit`, `charter_limit_and_more`, `charter_limit_tys`, `charter_second_signature`, `code_revoked`, `director_changed`, `director_changed_long_ago`, `fresh_director_admin`, `joint_powers`, `long_chain`, `long_chain_unregistered`, `missing_1001`, `name_mismatch`, `no_term`, `not_in_registry`, `ok`, `ok_director`, `ok_subdelegation`, `paper_poa`, `snils_of_other_person`, `text_authority` |
| refuted | 18 | `bad_checksum`, `child_longer`, `child_wider`, `expired`, `guid_case_transfer`, `liquidated`, `lost_on_subdelegation`, `no_term_expired`, `not_covered`, `ogrn_of_other_company`, `org_mismatch`, `parent_revoked`, `revoked`, `signer_not_eio`, `sub_child_before_parent`, `sub_signer_stranger`, `subdelegation_forbidden`, `wrong_representative` |

## 6. Правило → тест

| Правило | Что проверяет | Тесты |
|---|---|---|
| R-FMT-001 | Документы разобраны, обязательные поля МЧД на месте | `test_r_fmt_001_bad_xml_is_insufficient`, `test_r_fmt_001_xml_bomb_rejected`, `test_values_outside_the_schema_lists_are_format_gaps`, `test_document_in_a_foreign_namespace_is_rejected` |
| R-FMT-002 | МЧД в едином формате 003 | `test_r_fmt_002_foreign_format_needs_review` |
| R-FMT-003 | Реквизиты МЧД по единым требованиям в ред. Приказа № 1001 | `test_r_fmt_003_order_1001_requisites` |
| R-FMT-004 | Признаки безотзывной доверенности согласованы с признаком передоверия | `test_r_fmt_004_irrevocable_mismatch_needs_review` |
| R-FMT-005 | Документы и поля не повторяются | `test_r_fmt_005_duplicate_egrul_is_not_resolved` |
| R-ID-001 | Контрольные числа ИНН, ОГРН и СНИЛС сходятся | `test_r_id_001_bad_checksum_refutes` |
| R-ID-002 | Доверитель в МЧД — организация клиента | `test_r_id_002_org_mismatch_refutes` |
| R-ID-005 | ИНН и СНИЛС заявителя указывают на одно лицо | `test_r_id_005_inn_and_snils_of_different_people` |
| R-ID-003 | Представитель в МЧД — тот, кто просит права | `test_r_id_003_wrong_representative_refutes` |
| R-ID-004 | Наименование доверителя совпадает с ЕГРЮЛ | `test_r_id_004_name_mismatch_needs_review` |
| R-REG-001 | Каждая МЧД цепочки действует в реестре | `test_r_reg_001_revoked_refutes`, `test_r_reg_001_unavailable_is_insufficient`, `test_registry_revoked_after_historical_operation_goes_to_review`, `test_registry_active_snapshot_older_than_operation_is_stale` |
| R-TERM-001 | Срок действия указан или определяется по закону | `test_r_term_001_no_term_defaults_to_one_year` |
| R-TERM-002 | Дата операции попадает в срок каждой МЧД цепочки | `test_r_term_002_expired_refutes`, `test_r_term_002_no_term_after_a_year_refutes` |
| R-EIO-001 | Корневую МЧД подписал руководитель по ЕГРЮЛ на дату выдачи | `test_r_eio_001_signer_not_eio_refutes` |
| R-EIO-002 | Руководитель сменился после выдачи МЧД: сигнал, а не прекращение | `test_r_eio_002_fresh_change_needs_review`, `test_r_eio_002_old_change_is_signal_and_confirms` |
| R-EIO-003 | Нет отметки о недостоверности сведений о руководителе | `test_r_eio_003_unreliable_needs_review` |
| R-EIO-004 | Организация действует: не ликвидирована, доверитель и представитель не в банкротстве | `test_r_eio_004_liquidated_refutes`, `test_r_eio_004_bankruptcy_terminating_refutes`, `test_r_eio_004_observation_needs_review`, `test_r_eio_004_bankruptcy_mapping` |
| R-BNK-001 | Сведения о банкротстве доверителя и заявителя получены и распознаны | `test_bankruptcy_status_outside_dictionary`, `test_r_bnk_001_missing_bankruptcy_blocks_strict_confirmation` |
| R-EIO-006 | Выписка ЕГРЮЛ на дату операции | `test_r_eio_006_stale_egrul_is_not_confirmed` |
| R-EIO-005 | Заявитель — действующий руководитель (без доверенности) | `test_r_eio_005_director_acts_without_poa` |
| R-COV-001 | Полномочие покрывает запрошенную операцию | `test_r_cov_001_not_covered_refutes`, `test_r_cov_001_text_only_needs_review` |
| R-COV-002 | Код полномочия действует в классификаторе | `test_r_cov_002_revoked_code_needs_review` |
| R-JNT-001 | Полномочия совместные; нет признака — не «индивидуально» | `test_r_jnt_001_joint_powers_needs_review`, `test_missing_joint_flag_on_one_representative_is_not_confirmed` |
| R-SUB-001 | Родительская МЧД разрешает передоверие | `test_r_sub_001_forbidden_refutes` |
| R-SUB-002 | Передоверенные полномочия не шире исходных | `test_r_sub_002_wider_refutes` |
| R-SUB-003 | Срок передоверия не дольше срока исходной МЧД | `test_r_sub_003_longer_term_refutes` |
| R-SUB-004 | Цепочка передоверия предоставлена целиком | `test_r_sub_004_missing_parent_is_insufficient` |
| R-SUB-005 | Во всей цепочке один доверитель | `test_r_sub_005_principal_mismatch_refutes` |
| R-SUB-006 | Передоверие прекращается вместе с основной доверенностью | `test_r_sub_006_parent_revoked_cascades`, `test_later_parent_revocation_without_event_date_does_not_cascade` |
| R-SUB-007 | Передоверитель не утратил полномочия | `test_r_sub_007_lost_on_subdelegation_refutes`, `test_r_sub_007_foreign_child_needs_review` |
| R-SUB-008 | Длинная цепочка передоверия подтверждена реестром | `test_r_sub_008_long_chain_needs_review`, `test_r_sub_008_long_chain_in_registry_is_info` |
| R-SUB-009 | Каждое ребро передоверия подтверждено на дату выдачи | `test_r_sub_009_child_signer_must_match_parent_representative`, `test_same_day_sibling_transfers_need_review` |
| R-SUB-010 | Передоверие нотариально удостоверено или подпадает под исключение | `test_subdelegation_without_form_evidence_goes_to_review`, `test_notarized_mark_is_accepted_only_in_demo`, `test_subdelegator_kind_is_not_taken_from_the_client`, `test_subdelegator_kind_comes_from_the_parent_poa`, `test_notarization_is_read_from_the_child_poa`, `test_notarization_without_details_is_not_accepted`, `test_subdelegator_kind_conflicting_with_the_parent_is_unknown` |
| R-CHR-001 | Устав ограничивает полномочия руководителя | `test_r_chr_001_limit_needs_review` |
| R-CHR-002 | Ограничения устава шаблонами не найдены или не разобраны | `test_r_chr_002_clean_charter_is_info`, `test_missing_charter_blocks_confirmation`, `test_partial_required_charter_blocks_confirmation` |
| R-TXT-001 | В МЧД есть текстовые полномочия | `test_r_cov_001_text_only_needs_review` |
| R-PAP-001 | Предоставлена бумажная доверенность | `test_r_pap_001_paper_poa_needs_review` |
| R-SIG-001 | Квалифицированная подпись МЧД проверена | `test_r_sig_001_strict_profile_is_insufficient` |
| R-SIG-002 | Подпись распоряжения руководителя, действующего без доверенности | `test_eio_direct_needs_instruction_signature_in_strict` |
