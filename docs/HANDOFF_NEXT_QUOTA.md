# HANDOFF — следующая квота (Mandat)

**Для:** другой аккаунт / следующий исполнитель  
**Дата среза:** 2026-10-15 (МСК)  
**main tip (на момент среза):** `d483e8b` — `fix(cli): «подпись верна» после проверок + LIMITS require_seen (#25)`  
**Factcheck post-merge #25:** GO (evidence 1017, verify-source ok, G-A/G-D в main).

## Уже в main (не переделывать)

| PR | Что |
|---|---|
| #24 | M-13 status freshness by risk + G1 `min(global, risk)` |
| #25 | CLI print-order «подпись верна» **после** status checks; LIMITS `require_seen=False`; G-A (protected JSON override **только ужесточает**); G-D RESPONSE M-13→FIXED; evidence **1017** |

## Открытый follow-up (не вошёл в #25)

- **Demo print bug:** `demo + --status-dir + revoked` на main всё ещё может печатать success, потом exit 3 (`cli.py` ~368–370). Отдельный мелкий PR (удобно вместе с G-B).

## NEXT чанки (порядок)

1. **M-03** — pilot compose | OPEN | ветка `fix/m03-pilot-compose` (PR ещё не открыт)  
   - `deploy/compose.pilot.yml`: volume на `/app/var` (не tmpfs); `MANDAT_API_JOURNAL`; `MANDAT_API_STORE=sqlite:////app/var/store.db`; `MANDAT_ENV=pilot`.  
   - `/readyz` (journal+store+keys) → 200/503; `/health` liveness.  
   - Overlay healthcheck → `/readyz`; CI: compose config + up --wait; persist across restart.  
   - README/LIMITS: `MANDAT_ENV=pilot` alone ≠ pilot.

2. **G-B + demo print** | OPEN | `fix/gb-none-risk-strictest` (+ demo revoked print)  
   - Protected: `operation_risk is None`/blank → **strictest** (`min(mapping)`), не default 7d.  
   - Demo может оставить default.  
   - В том же PR: убрать success-print на reject-path для demo+revoked.

3. **M-10 / M-12** | OPEN  
   - **M-10** journal/store atomicity: crash-safe outbox; 200 только после обоих; Idempotency-Key; fencing; reconcile. Не exactly-once от `first_seen`.  
   - **M-12** token lifecycle: `expires_at`/`revoked`; Directory отвергает expired/revoked в protected; docs rotation.

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
