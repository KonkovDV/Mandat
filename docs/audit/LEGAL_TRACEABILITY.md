# Прослеживаемость норм

Дата правовой оценки — 15.10.2026 (доп. R6 docs). Для исторической операции нужна редакция нормы на дату операции; пакет правил хранит одну редакцию и даты вступления только там, где они заданы параметром (R-FMT-003, `effective_from`).

Все 39 правил пакета `mandat-rules-ru` имеют статус `draft`. Ни одно не согласовано юристом банка. В профиле `strict` эффект `supports` у черновика не даёт «подтверждено» (становится пробелом / «не проверено»), а опровержения и «на разбор» по-прежнему видны сотруднику — см. `docs/RULES.md` и FAQ README. Синтетический прогон `strict` в `evidence.json` (`synthetic_corpus_strict`: 294 кейса) даёт 0 % решений без человека и 0 ложных «подтверждено». Это не дефект, а честная граница до утверждения правил.



## Источники норм и глубина их проверки

| Источник | Редакция и сила | Как проверен | Где используется |
|---|---|---|---|
| ГК РФ, ст. 187 | действующая; текст в КонсультантПлюс | текст прочитан при подготовке аудита; ссылки на пункты 1, 3, 4, 6, 7 в `rules.json` сверены с ним | R-SUB-001…005, R-SUB-007…010 (R-SUB-006 — ст. 188 п. 3) |
| ГК РФ, ст. 188 | ред. от 10.06.2026 по КонсультантПлюс | прочитан при подготовке аудита: перечень оснований прекращения и п. 3 о передоверии | R-TERM-002, R-EIO-002, R-EIO-004, R-BNK-001, R-SUB-006, R-REG-001 |
| ГК РФ, ст. 189 | текст в Гаранте | по записи в `docs/REGULATORY.md`, в этом аудите не перечитывалась; знание об отмене через реестр в коде не моделируется | R-REG-001 (частично) |
| ГК РФ, ст. 53, 174, 182, 185, 185.1, 186, 188.1 | действующие | номера и смысл сверены с `rules.json`; полные тексты в этом аудите не перечитывались | R-EIO-*, R-CHR-*, R-ID-*, R-TERM-001, R-FMT-004, R-PAP-001 |
| 63-ФЗ, ст. 11 | ред. 31.07.2025 по КонсультантПлюс, консолидированную редакцию на 13.10.2026 перепроверить | условия признания КЭП прочитаны при подготовке аудита; проверка подписи в стенде не подключена | R-SIG-001 |
| 63-ФЗ, ст. 17.2–17.5 | в ред. 476-ФЗ | по записи в `docs/REGULATORY.md`; в этом аудите не перечитывались | R-FMT-001, R-REG-001 |
| Приказ Минцифры № 857 в ред. № 1001 | № 1001: размещение/СПС 21.01.2026, RG 22.01.2026, в силе с 01.02.2026 | реквизиты публикации подтверждены; **приложение к приказу полностью не прочитано** | R-FMT-002, R-FMT-003, R-ID-003, R-ID-004 |
| Приказ Минцифры № 856 (классификатор полномочий) | действующий | не прочитан; коды в стенде синтетические (`SYN-…`) | R-COV-001, R-COV-002 |
| XSD EMCHD_1 (`ON_EMCHD_1_928_00_01_01_01`) | открытые копии, sha256 в `SOURCE_LEDGER.md` | прочитана целиком машинно (пути, обязательность, перечни) | R-FMT-001, R-SUB-010, файл соответствия |
| 127-ФЗ (банкротство) | действующий | процедуры перечислены по `docs/REGULATORY.md`; текст закона в этом аудите не читался | R-EIO-004, R-BNK-001 |
| 129-ФЗ (ЕГРЮЛ) | действующий | не читался | R-EIO-003, R-EIO-006 |
| 14-ФЗ, ст. 46 | действующий | не читался; порог суммы в стенде не доказывает крупную сделку | R-CHR-001 |
| 315-ФЗ от 04.08.2026 | вступает в силу 01.09.2027 | ст. 2 прочитана на kremlin.ru; в правила как действующая норма не включён | — |
| 243-ФЗ от 26.07.2026 (ИИ) | в силе с 01.09.2026, часть норм с 01.03.2027 | реквизиты подтверждены на kremlin.ru и publication.pravo.gov.ru | советник-модель, не правила |

Ничего из 152-ФЗ, 395-1, 115-ФЗ, 161-ФЗ, положений Банка России и ГОСТ Р 57580 правила пакета не реализуют. Это требования к банку и его процессу; что из них ложится на «Мандат», а что на инфраструктуру банка — в `THREAT_AND_TRUST_BOUNDARIES.md`.

## Правило → код → тест

Таблица собрана из `src/mandat/data/rules.json` и декораторов `@rule` в `src/mandat/rules/checks.py`. Что каждое правило ссылается на существующую функцию и тест, проверяют тесты пакета правил при каждом прогоне.

| Правило | Норма в пакете | Функция | Тесты | Согласование |
|---|---|---|---|---|
| R-FMT-001 | 63-ФЗ, ст. 17.2–17.5 в ред. 476-ФЗ (с 01.09.2024 представитель юрлица подписывает своей КЭП и МЧД); формат МЧД 003 | `rules/checks.py::fmt_parsed` | `test_r_fmt_001_bad_xml_is_insufficient`, `test_r_fmt_001_xml_bomb_rejected`, `test_values_outside_the_schema_lists_are_format_gaps`, `test_document_in_a_foreign_namespace_is_rejected` | черновик, юристом не согласовано |
| R-FMT-002 | Приказ Минцифры № 857 от 18.08.2021 (единые требования к формам МЧД); формат 003. Формат ФНС 5.0x — для налоговых отношений | `rules/checks.py::fmt_version` | `test_r_fmt_002_foreign_format_needs_review` | черновик, юристом не согласовано |
| R-FMT-003 | Приказ Минцифры № 857 от 18.08.2021 в ред. Приказа № 1001 от 05.11.2025 (в силу с 01.02.2026): КПП доверителя, СНИЛС руководителя доверителя, наименование полномочия рядом с кодом | `rules/checks.py::fmt_order_1001` | `test_r_fmt_003_order_1001_requisites` | черновик, юристом не согласовано |
| R-FMT-004 | ГК РФ, ст. 188.1 (безотзывная доверенность); описание схемы МЧД: Безотзыв/@ПрПерБезДов и @ПрПередов | `rules/checks.py::fmt_irrevocable` | `test_r_fmt_004_irrevocable_mismatch_needs_review` | черновик, юристом не согласовано |
| R-FMT-005 | Требование к полноте пакета: сведения ЕГРЮЛ и устав в одном экземпляре; повтор с разным содержимым не разрешается молча | `rules/checks.py::fmt_unique` | `test_r_fmt_005_duplicate_egrul_is_not_resolved` | черновик, юристом не согласовано |
| R-ID-001 | Алгоритмы контрольных чисел ИНН и ОГРН (ФНС), СНИЛС (СФР) | `rules/checks.py::id_checksums` | `test_r_id_001_bad_checksum_refutes` | черновик, юристом не согласовано |
| R-ID-002 | ГК РФ, ст. 182, 185 | `rules/checks.py::id_principal` | `test_r_id_002_org_mismatch_refutes` | черновик, юристом не согласовано |
| R-ID-005 | ГК РФ, ст. 185: представитель — лицо, указанное в доверенности; сверка по всем указанным идентификаторам | `rules/checks.py::id_conflict` | `test_r_id_005_inn_and_snils_of_different_people` | черновик, юристом не согласовано |
| R-ID-003 | ГК РФ, ст. 182, 185; Приказ Минцифры № 857 в ред. № 1001 (с 01.02.2026): представитель указывается с СНИЛС и ИНН, паспорт не обязателен; идентификация (115-ФЗ, ст. 7) — каналами банка | `rules/checks.py::id_representative` | `test_r_id_003_wrong_representative_refutes` | черновик, юристом не согласовано |
| R-ID-004 | Приказ Минцифры № 857 в ред. Приказа № 1001: наименование доверителя по ЕГРЮЛ | `rules/checks.py::id_principal_name` | `test_r_id_004_name_mismatch_needs_review` | черновик, юристом не согласовано |
| R-REG-001 | ГК РФ, ст. 188, 189; 63-ФЗ в ред. 476-ФЗ (сведения о МЧД и её отмене в информационной системе — распределённый реестр ФНС) | `rules/checks.py::registry_status` | `test_r_reg_001_revoked_refutes`, `test_r_reg_001_unavailable_is_insufficient`, `test_registry_revoked_after_historical_operation_goes_to_review`, `test_registry_active_snapshot_older_than_operation_is_stale`, `test_later_revocation_snapshot_without_event_date_goes_to_review` | черновик, юристом не согласовано |
| R-TERM-001 | ГК РФ, ст. 186 п. 1: если срок не указан, доверенность действует год со дня совершения; в описании схемы 003 срок (СрокДейст) — обязательный реквизит | `rules/checks.py::term_present` | `test_r_term_001_no_term_defaults_to_one_year` | черновик, юристом не согласовано |
| R-TERM-002 | ГК РФ, ст. 186 (в том числе срок по умолчанию — год); ст. 188 п. 1 (истечение срока) | `rules/checks.py::term_covers_date` | `test_r_term_002_expired_refutes`, `test_r_term_002_no_term_after_a_year_refutes` | черновик, юристом не согласовано |
| R-EIO-001 | ГК РФ, ст. 53, 185.1 | `rules/checks.py::eio_signed_root` | `test_r_eio_001_signer_not_eio_refutes` | черновик, юристом не согласовано |
| R-EIO-002 | ГК РФ, ст. 188 п. 1 — смена руководителя не входит в исчерпывающий перечень оснований прекращения доверенности; разъяснения ФНС: ранее выданные МЧД действуют, пока их не отменили | `rules/checks.py::eio_changed` | `test_r_eio_002_fresh_change_needs_review`, `test_r_eio_002_old_change_is_signal_and_confirms` | черновик, юристом не согласовано |
| R-EIO-003 | 129-ФЗ (отметка о недостоверности сведений в ЕГРЮЛ) | `rules/checks.py::eio_unreliable` | `test_r_eio_003_unreliable_needs_review` | черновик, юристом не согласовано |
| R-EIO-004 | ГК РФ, ст. 188 п. 1 подп. 4 и 7 (прекращение юрлица; процедура банкротства, при которой лицо утрачивает право выдавать доверенности); 127-ФЗ, ст. 64, 82, 94, 126, 159, 213.11, 213.25 | `rules/checks.py::org_status` | `test_r_eio_004_liquidated_refutes`, `test_r_eio_004_bankruptcy_terminating_refutes`, `test_r_eio_004_observation_needs_review`, `test_r_eio_004_bankruptcy_mapping` | черновик, юристом не согласовано |
| R-BNK-001 | ГК РФ, ст. 188 п. 1 подп. 7; 127-ФЗ. Отсутствие ответа ЕФРСБ не означает отсутствия процедуры | `rules/checks.py::bankruptcy_checked` | `test_bankruptcy_status_outside_dictionary`, `test_r_bnk_001_missing_bankruptcy_blocks_strict_confirmation` | черновик, юристом не согласовано |
| R-EIO-006 | 129-ФЗ, ст. 6 (сведения ЕГРЮЛ на дату запроса); порог свежести — параметр банка | `rules/checks.py::egrul_fresh` | `test_r_eio_006_stale_egrul_is_not_confirmed` | черновик, юристом не согласовано |
| R-EIO-005 | ГК РФ, ст. 53 | `rules/checks.py::eio_direct` | `test_r_eio_005_director_acts_without_poa` | черновик, юристом не согласовано |
| R-COV-001 | ГК РФ, ст. 182, 185; единый классификатор полномочий (63-ФЗ в ред. 476-ФЗ; Приказ Минцифры № 856 от 18.08.2021) | `rules/checks.py::coverage` | `test_r_cov_001_not_covered_refutes`, `test_r_cov_001_text_only_needs_review` | черновик, юристом не согласовано |
| R-COV-002 | Единый классификатор полномочий (Приказ Минцифры № 856; справочник в ЕСНСИ с датами начала и исключения кода). Нет даты начала — код не считается действующим | `rules/checks.py::coverage_classifier` | `test_r_cov_002_revoked_code_needs_review`, `test_code_without_start_date_is_not_in_force` | черновик, юристом не согласовано |
| R-JNT-001 | ГК РФ, ст. 185; описание схемы МЧД: ПрСовмПолн = 2 — полномочия осуществляются совместно. Нет признака — не «индивидуально» | `rules/checks.py::joint_powers` | `test_r_jnt_001_joint_powers_needs_review`, `test_missing_joint_flag_on_one_representative_is_not_confirmed` | черновик, юристом не согласовано |
| R-SUB-001 | ГК РФ, ст. 187 п. 1 и 7; признак ПрПередов: 1 — без права передоверия, 2 — однократное, 3 — с правом последующего передоверия | `rules/checks.py::sub_allowed` | `test_r_sub_001_forbidden_refutes` | черновик, юристом не согласовано |
| R-SUB-002 | ГК РФ, ст. 187 (передоверие — только в пределах полномочий по основной доверенности) | `rules/checks.py::sub_narrower` | `test_r_sub_002_wider_refutes` | черновик, юристом не согласовано |
| R-SUB-003 | ГК РФ, ст. 187 п. 4 (срок передоверия не может превышать срок основной доверенности) | `rules/checks.py::sub_term` | `test_r_sub_003_longer_term_refutes` | черновик, юристом не согласовано |
| R-SUB-004 | ГК РФ, ст. 187 | `rules/checks.py::sub_complete` | `test_r_sub_004_missing_parent_is_insufficient` | черновик, юристом не согласовано |
| R-SUB-005 | ГК РФ, ст. 187 | `rules/checks.py::sub_same_principal` | `test_r_sub_005_principal_mismatch_refutes` | черновик, юристом не согласовано |
| R-SUB-006 | ГК РФ, ст. 188 п. 3: с прекращением доверенности теряет силу передоверие | `rules/checks.py::sub_cascade` | `test_r_sub_006_parent_revoked_cascades`, `test_later_parent_revocation_without_event_date_does_not_cascade` | черновик, юристом не согласовано |
| R-SUB-007 | ГК РФ, ст. 187 п. 6 (по общему правилу передоверитель полномочия не теряет); описание схемы МЧД: ПрУтрПолн = 2 — передоверитель утрачивает полномочия при передоверии | `rules/checks.py::sub_lost_authority` | `test_r_sub_007_lost_on_subdelegation_refutes` | черновик, юристом не согласовано |
| R-SUB-008 | ГК РФ, ст. 187 число передоверий не ограничивает; по справке оператора ЭДО (2026) реестр ФНС принимает ограниченную длину цепочки — техническое ограничение, не норма | `rules/checks.py::sub_long_chain` | `test_r_sub_008_long_chain_needs_review`, `test_r_sub_008_long_chain_in_registry_is_info` | черновик, юристом не согласовано |
| R-SUB-009 | ГК РФ, ст. 187: передоверить может тот, кому полномочие предоставлено, и только пока оно действует | `rules/checks.py::sub_edge` | `test_r_sub_009_child_signer_must_match_parent_representative`, `test_same_day_sibling_transfers_need_review` | черновик, юристом не согласовано |
| R-SUB-010 | ГК РФ, ст. 187 п. 3: доверенность, выдаваемая в порядке передоверия, должна быть нотариально удостоверена, кроме передоверия юридическими лицами, руководителями филиалов и представительств | `rules/checks.py::sub_form` | `test_subdelegation_without_form_evidence_goes_to_review`, `test_notarized_mark_is_accepted_only_in_demo`, `test_subdelegator_kind_is_not_taken_from_the_client`, `test_subdelegator_kind_comes_from_the_parent_poa`, `test_notarization_is_read_from_the_child_poa`, `test_notarization_without_details_is_not_accepted`, `test_subdelegator_kind_conflicting_with_the_parent_is_unknown` | черновик, юристом не согласовано |
| R-CHR-001 | ГК РФ, ст. 174; 14-ФЗ, ст. 46 (крупные сделки ООО) | `rules/checks.py::charter_limits` | `test_r_chr_001_limit_needs_review` | черновик, юристом не согласовано |
| R-CHR-002 | ГК РФ, ст. 174 | `rules/checks.py::charter_info` | `test_r_chr_002_clean_charter_is_info`, `test_missing_charter_blocks_confirmation`, `test_partial_required_charter_blocks_confirmation` | черновик, юристом не согласовано |
| R-TXT-001 | Описание схемы МЧД: ТипПолн = 0 — текстовые полномочия; их оценивает сотрудник, вердикт не выше «на разбор» | `rules/checks.py::text_authorities` | `test_r_cov_001_text_only_needs_review` | черновик, юристом не согласовано |
| R-PAP-001 | ГК РФ, ст. 185, 185.1 | `rules/checks.py::paper_poa` | `test_r_pap_001_paper_poa_needs_review` | черновик, юристом не согласовано |
| R-SIG-001 | 63-ФЗ, ст. 11; проверка КЭП — сертифицированными СКЗИ банка (в стенде не подключена) | `rules/checks.py::signature` | `test_r_sig_001_strict_profile_is_insufficient` | черновик, юристом не согласовано |
| R-SIG-002 | ГК РФ, ст. 53 (ЕИО действует без доверенности); 63-ФЗ, ст. 6 (электронная подпись). Статус ЕИО не доказывает авторство распоряжения | `rules/checks.py::eio_instruction_signature` | `test_eio_direct_needs_instruction_signature_in_strict` | черновик, юристом не согласовано |

## Что требует человека

- Юрист: согласовать или отклонить каждое правило; до согласования статус `draft` сохраняется. Конкретные вопросы с вариантами — в `AUDIT.md`, раздел «Вопросы юристу и службе безопасности».
- Прочитать полностью приложение к Приказу № 1001 и Приказы № 857 и № 856 и сверить с R-FMT-003, R-ID-003, R-ID-004 и R-COV-*.
- Для R-CHR-001 и R-BNK-001 прочитать 14-ФЗ, 208-ФЗ и 127-ФЗ с учётом вида лица; сейчас правила дают только разбор или «не проверено».
