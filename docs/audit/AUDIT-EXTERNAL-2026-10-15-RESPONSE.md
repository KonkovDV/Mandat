# Ответ на внешний аудит Mandat (2026-10-15)

**Срез кода:** `be0d786` (`main`, post-merge [#24](https://github.com/KonkovDV/Mandat/pull/24); leftovers [#25](https://github.com/KonkovDV/Mandat/pull/25))  
**Аудит-пакет:** внешний, в репозиторий не входит; ниже — только находки и ответ.  
**Evidence:** 1017 passed, coverage 95,7 % (`evidence.json`)  
**Правило ответа:** без завышений; «FIXED» только при коде + тестах в `main`.

| ID | Pri | Статус на `51f247d` | Что сделано / что осталось | Доказательство |
|---|---|---|---|---|
| **M-01** | P1 | **FIXED** (residual → M-04) | Fail-closed: в pilot/prod статус обязателен; revoked/suspended/missing/corrupt/stale → отказ; CLI `--crypto-only` не говорит «принимается»; `authorize_operation` требует `verdict=confirmed`. Residual: нет bind amount/request_id (M-04). | PR #18; `tests/test_m01_status_required.py` |
| **M-02** | P1 | **FIXED** (residual: **POSIX/Linux only**) | `fcntl.flock` + CAS sequence; OR только revocation; suspension/конфликт idx → `StaleRegistryError`; snapshots + `CURRENT`. **Residual:** `import fcntl` на уровне модуля `status_list.py` → на native Windows `ImportError` при импорте: сломаны status registry и `attest-verify` целиком (не «flock не покрыт тестами»). Целевой контур: Linux / Docker / WSL. CI — только `ubuntu-latest`, Windows matrix нет. | PR #18; `tests/test_m02_*`; `LIMITS.md` |
| **M-03** | P2 | **STILL_OPEN** | Compose = стенд (`tmpfs` `/app/var`); нет durable journal/store overlay. | `docker-compose.yml` |
| **M-04** | P2 | **PARTIAL** | Разделены crypto receipt vs `authorize_operation`; полного bind (amount, representative, nonce, audience) нет. | #18 |
| **M-05** | P2 | **STILL_OPEN** | Pseudonym ≠ de-ID; DOB/address / LLM minimization не закрыты. | — |
| **M-06** | P2 | **FIXED** | В pilot/prod LLM только HTTPS; loopback HTTP лишь при `MANDAT_LLM_ALLOW_INSECURE_LOOPBACK=1`. | PR #21; `tests/test_m06_llm_tls.py` |
| **M-07** | P2 | **STILL_OPEN** (legal) | Приказ №74: реквизиты ≠ применимость к банку; нужен caveat + counsel. | — |
| **M-08** | P2 | **STILL_OPEN** (eval) | Synth stats ≠ bank risk; нужны честные bounds / holdout. | — |
| **M-09** | P2 | **STILL_OPEN** | HITL library ≠ client API (appeal/queue/decide). | — |
| **M-10** | P2 | **STILL_OPEN** | Journal-first без полной атомарности store (outbox). | — |
| **M-11** | P3 | **PARTIAL** | R6 закрыл часть дрейфа; живые gates/числа ещё местами устарели. Post-merge docs: `SUBMISSION_DRIFT` → 993. | этот PR |
| **M-12** | P2 | **STILL_OPEN** | Token hash ≠ IAM lifecycle (expiry/revoke/provisioning). | — |
| **M-13** | P2 | **FIXED** | `status_max_age_for(risk)`; unknown → strictest; `min(global, risk)` (G1); в protected JSON risk-map только ужесточает defaults (G-A); `authorize_operation` fail-closed без `seen`. Residual: нет — см. LIMITS POSIX. | PR #24/#25; `tests/test_m13_*` |
| **M-14** | P3 | **PARTIAL** | MUTATION.md честнее; targeted mutmut на attestation/status_list после P1 — ещё нет. | — |

## Что можно говорить жюри / банку сейчас

- Удостоверение в pilot/prod **не принимается** без актуального статуса; concurrent revoke не теряется на Linux flock/CAS (#18). Status registry — **только POSIX/Linux** (Docker/WSL); native Windows не поддерживается.
- **Нельзя** утверждать: полный IAM, durable pilot compose, атомарность journal↔store, применимость приказа №74 к банку, bank-risk KPI со синтеза, сквозной HITL API. TLS к модели в protected закрыт (#21); residual — IAM lifecycle (M-12).

## Очередь после этого ответа

1. M-06 + M-13 влиты (#21/#24/#25). Дальше: M-03 pilot compose.  
2. M-10 + M-12 → M-09 → M-04 → M-05 + M-07/M-08 docs.  
3. M-11 sync gates; M-14 kill-tests на attestation/status_list.

Исторические снимки аудита (`AUDIT-2026-10-15-R*.md`, pack `updated-documentation/`) **не переписываем**.
