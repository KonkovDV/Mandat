# Передача работы (Mandat)

> Исторический срез 15.10.2026, tip `d483e8b`. Очередь ниже закрыта последующими PR и на текущем `main` не исполняется. Актуальное состояние — [README](../README.md) и [сверка 16.10](audit/AUDIT-2026-10-16-R7.md).

**Для:** другой аккаунт / следующий исполнитель  
**Дата среза:** 2026-10-15 (МСК)  
**main tip (на момент среза):** `d483e8b` — `fix(cli): «подпись верна» после проверок + LIMITS require_seen (#25)`  
**Factcheck post-merge #25:** GO (evidence 1017, verify-source ok, G-A/G-D в main).

## Уже в main (не переделывать)

| PR | Что |
|---|---|
| #24 | M-13 status freshness by risk + G1 `min(global, risk)` |
| #25 | CLI print-order «подпись верна» **после** status checks; LIMITS `require_seen=False`; G-A (protected JSON override **только ужесточает**); G-D RESPONSE M-13→FIXED; evidence **1017** |

## Закрыто в PR #28 (G-B + demo print; после merge — в main)

- **G-B:** в pilot/prod `operation_risk` не указан → строжайшее окно (breaking для интеграторов, см. LIMITS/CHANGELOG).
- **Demo print bug:** `attest-verify` проверяет статус до «подпись верна»; demo + `--status-dir` + revoked → exit 3 без success-вывода.

## Открытый follow-up (не вошёл в #25)

- Нет открытых пунктов по G-B и demo print (закрыты в #28).

## NEXT чанки (порядок)

1. **M-03** — pilot compose | OPEN | ветка `fix/m03-pilot-compose` (PR ещё не открыт)  
   - `deploy/compose.pilot.yml`: volume на `/app/var` (не tmpfs); `MANDAT_API_JOURNAL`; `MANDAT_API_STORE=sqlite:////app/var/store.db`; `MANDAT_ENV=pilot`.  
   - `/readyz` (journal+store+keys) → 200/503; `/health` liveness.  
   - Overlay healthcheck → `/readyz`; CI: compose config + up --wait; persist across restart.  
   - README/LIMITS: `MANDAT_ENV=pilot` alone ≠ pilot.

2. **G-B + demo print** | DONE в PR #28 (ожидает merge) | `fix/gb-none-risk-strictest`  
   - Protected: `operation_risk is None`/blank → **strictest** (`min(mapping)`), не default 7d.  
   - Demo может оставить default.  
   - В том же PR: убрать success-print на reject-path для demo+revoked.

3. **M-10 / M-12** | M-12 DONE в PR #29 (ожидает merge); M-10 в PR #30  
   - **M-10** journal/store atomicity: crash-safe outbox; 200 только после обоих; Idempotency-Key; fencing; reconcile. Не exactly-once от `first_seen`.  
   - **M-12** token lifecycle: `expires_at`/`revoked`; Directory отвергает expired/revoked в любом режиме; обязательный `expires_at` и потолок 366 дн. — только в protected; docs rotation. **DONE (PR #29).**

4. **M-09** — review routes / reviewer≠operator | OPEN | `fix/m09-review-routes` (после M-12)  
   - Единые роли review↔API; `reviewer` decide; `operator` не accept/reject; запрет self-approve.  
   - Маршруты appeal / queue / open / accept|reject|request_docs + `expected_version` (409).  
   - Protected: journal обязателен; state в store; SLA + docs/REVIEW.md.  
   - Не пересекать с M-10 правками `app.py` без согласования порядка.

G-A/G-D — в #25; G-C docs mostly done.

## Не делать

- `--crypto-only` на сцене: печатает «подпись верна (только подпись)» — **не** полный статус; нельзя показывать жюри как полный verify.  
- Overclaim exactly-once / pilot из одного `MANDAT_ENV`.  
- Заявлять M-09 «закрыт» наличием только appeal/state_machine без API-маршрутов и единых ролей.
