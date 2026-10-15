# Ответ на внешний аудит Mandat (2026-10-15)

**Срез кода:** `dc00239` (main post-#19); M-06/M-13 → PR #20 (`main`, post-merge [#18](https://github.com/KonkovDV/Mandat/pull/18))  
**Аудит-пакет:** `/workspace/mandat-audit/` (`AUDIT.md`, briefs `BRIEF_P2.md`, `BRIEF_M01_M02.md`, `BRIEF_M11_M14.md`)  
**Evidence:** 993 passed, coverage 95,8 % (`evidence.json`)  
**Правило ответа:** без завышений; «FIXED» только при коде + тестах в `main`.

| ID | Pri | Статус на main+#20 | Что сделано / что осталось | Доказательство |
|---|---|---|---|---|
| **M-01** | P1 | **FIXED** (residual → M-04) | Fail-closed: в pilot/prod статус обязателен; revoked/suspended/missing/corrupt/stale → отказ; CLI `--crypto-only` не говорит «принимается»; `authorize_operation` требует `verdict=confirmed`. Residual: нет bind amount/request_id (M-04). | PR #18; `tests/test_m01_status_required.py` |
| **M-02** | P1 | **FIXED** (residual: **POSIX/Linux only**) | `fcntl.flock` + CAS sequence; OR только revocation; suspension/конфликт idx → `StaleRegistryError`; snapshots + `CURRENT`. **Residual:** `import fcntl` на уровне модуля `status_list.py` → на native Windows `ImportError` при импорте: сломаны status registry и `attest-verify` целиком (не «flock не покрыт тестами»). Целевой контур: Linux / Docker / WSL. CI — только `ubuntu-latest`, Windows matrix нет. | PR #18; `tests/test_m02_*`; `LIMITS.md` |
| **M-03** | P2 | **STILL_OPEN** | Compose = стенд (`tmpfs` `/app/var`); нет durable journal/store overlay. | `BRIEF_P2.md`; `docker-compose.yml` |
| **M-04** | P2 | **PARTIAL** | Разделены crypto receipt vs `authorize_operation`; полного bind (amount, representative, nonce, audience) нет. | #18 + brief M-04 |
| **M-05** | P2 | **STILL_OPEN** | Pseudonym ≠ de-ID; DOB/address / LLM minimization не закрыты. | `BRIEF_P2.md` |
| **M-06** | P2 | **IN_REVIEW** ([#20](https://github.com/KonkovDV/Mandat/pull/20)) | HTTPS в protected; loopback http только с флагом (`::1`/`127.0.0.1`/`localhost`); SECURITY/.env.example. | BRIEF_M06_M13_ACCEPTANCE; `test_m06_llm_tls.py` |
| **M-07** | P2 | **STILL_OPEN** (legal) | Приказ №74: реквизиты ≠ применимость к банку; нужен caveat + counsel. | `BRIEF_P2.md` |
| **M-08** | P2 | **STILL_OPEN** (eval) | Synth stats ≠ bank risk; нужны честные bounds / holdout. | `BRIEF_P2.md` |
| **M-09** | P2 | **STILL_OPEN** | HITL library ≠ client API (appeal/queue/decide). | `BRIEF_P2.md` |
| **M-10** | P2 | **STILL_OPEN** | Journal-first без полной атомарности store (outbox). | `BRIEF_P2.md` |
| **M-11** | P3 | **PARTIAL** | R6 закрыл часть дрейфа; живые gates/числа ещё местами устарели (см. BRIEF_M11). Post-merge docs: `SUBMISSION_DRIFT` → 993, колода 15.10 (#19). | #19 |
| **M-12** | P2 | **STILL_OPEN** | Token hash ≠ IAM lifecycle (expiry/revoke/provisioning). | `BRIEF_P2.md` |
| **M-13** | P2 | **IN_REVIEW** ([#20](https://github.com/KonkovDV/Mandat/pull/20)) | `status_max_age_for(risk)`; unknown→strictest; authorize requires `seen`; CLI `--operation-risk`; outage в LIMITS/ARCHITECTURE. | BRIEF_M06_M13_ACCEPTANCE; `test_m13_*` |
| **M-14** | P3 | **PARTIAL** | MUTATION.md честнее; targeted mutmut на attestation/status_list после P1 — ещё нет. | `BRIEF_M11_M14.md` |

## Что можно говорить жюри / банку сейчас

- Удостоверение в pilot/prod **не принимается** без актуального статуса; concurrent revoke не теряется на Linux flock/CAS (#18). Status registry — **только POSIX/Linux** (Docker/WSL); native Windows не поддерживается.
- **Нельзя** утверждать: полный IAM, durable pilot compose, TLS/egress к модели, атомарность journal↔store, применимость приказа №74 к банку, bank-risk KPI со синтеза, сквозной HITL API.

## Очередь после этого ответа

1. Re-review/merge [#20](https://github.com/KonkovDV/Mandat/pull/20) (M-06/M-13) после green CI.  
2. M-10 + M-12 → M-09 → M-03/M-04 → M-05 + M-07/M-08 docs.  
3. M-11 sync gates; M-14 kill-tests на attestation/status_list.

Исторические снимки аудита (`AUDIT-2026-10-15-R*.md`, pack `updated-documentation/`) **не переписываем**.
