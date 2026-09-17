# Day 4 — Known Defect Remediation

Not a release PASS. Not Day 5. No git add / commit / push.

## Baseline

| Item | Value |
| --- | --- |
| Engineering checkpoint | `f87f7b6fde396f115ffd52c932e7aec74b3728f8` |
| Starting HEAD | `3c7b9ac90c7ec34db6a6bdbc961070b6c315791a` (`docs(learning): add separate engineering notes and interview Q&A`) |
| Working tree at STOP | dirty (this remediation uncommitted) |
| Interpreter | `d:\property-ai\.venv\Scripts\python.exe` |
| Alembic | `20260917_0007` on development / test / day4 / staging; files 0001–0007 **not modified** |

## Modified files

### Production / ops

- `backend/app/core/database.py` — `transaction_context` / `run_service_transaction`; never rollback caller txn
- `backend/app/service/{employee,resident,work_order,knowledge,property,analysis,announcement,incident,wechat_auth}.py`, `rbac_mutation.py` — join vs begin
- `backend/app/service/resident.py` — ACTIVE self-apply policy; approve atomic; missing RESIDENT → `ROLE_NOT_FOUND`
- `backend/app/service/work_order.py` — capacity lock via `get_daily_status_for_update`; `register_file` persist inside `_execute`
- `backend/app/api/v1/health.py` — `/opsz` requires SYSTEM-scoped `audit:read`
- `backend/app/core/logging.py`, `exception_handlers.py`, `provider/http.py` — recursive redaction + exception chains; safe URLs
- `backend/app/worker/failed_tasks.py`, `celery_app.py`, `enqueue.py`, `tasks.py` — HASH+ZSET records, independent DLQ, lease, `REPLAY_ENQUEUED`, retryable analysis re-raise
- `deploy/docker-compose.yml`, `docker-compose.day4.yml`, `docker-compose.prodlike.yml` — Nginx `127.0.0.1`; Day 4 LLM/broker drill env interpolation
- `docs/11-OPERATIONS.md`, `scripts/README.md`, `scripts/ops/day4_e2e.py`, `perf_probe.py`
- New: `scripts/ops/day4_failed_task_drill.py`, `scripts/ops/day4_perf_run.py`

### Tests / fixtures (call-sites only; acceptance not lowered)

- `backend/tests/integration/conftest.py` — `commit_open_transaction`
- identity `_principal` helpers commit autobegin after `load_principal`
- `test_day3_prodlike_e2e.py`, `test_day4_readiness_opsz.py` — manager/dispatcher 403; SYSTEM 200 (security tighten)
- New: `test_day4_silent_rollback.py`, `test_day4_e2e_assertions.py`, `test_day4_logging_redaction.py`
- Rewritten/extended: `test_day4_failed_tasks.py`, `test_day4_capacity_load.py`, `test_resident_binding_runtime.py`, `test_celery_ai_task.py`

### Learning (separate)

- `learning/engineering-notes.md`, `learning/interview-qa.md` — corrected empty-grants / opsz / transaction notes to match current code. Not a product spec.

## Defect → fix → result

| Defect | How reproduced | Fix | After |
| --- | --- | --- | --- |
| E2E printed then PASS | isolated doubles + live 8082 | per-step HTTP+postcondition; no seed fallback; this-round RAG/notifications; try/finally worker | Live **PASS**; negatives non-zero / no overall PASS |
| Silent `_execute` rollback | MySQL tests with pending + flushed rows | join open txn; refuse-by-not-rollback | **PASS** |
| Log leak in nested/exc | rendered logger output with synthetic markers | recursive redact + traceback | **PASS** |
| Bind blocked by property A grants; approve missing role silent | pytest A→B / missing RESIDENT | applicant policy; atomic grant | **PASS** |
| Failed tasks drop/skip/mark success | live unavailable LLM + bad broker | HASH+ZSET, record all `task_failure`, replay lease, analyze retry re-raise | Live drill **PASS** |
| `/opsz` too wide / 8081 public | curl + pytest | SYSTEM `audit:read`; rebuild API/Nginx | 8082 401/403/200; 8081 unauth 401. Staging operator 200 **NOT_RUN** |
| Perf claimed without method label | cold vs pooled probes | labeled `perf_probe --mode` | cold WO P95 misses NFR **PARTIAL**; pooled is a **new** method |
| CVE unread | scout login fail; pip-audit freeze | isolated scan, no upgrade | 28 findings / 5 pkgs; Scout **BLOCKED** |

## Commands actually run

```text
docker compose -f deploy/docker-compose.yml --profile app build api worker
docker compose -f deploy/docker-compose.day4.yml up -d --no-deps --force-recreate api-day4 worker-day4 nginx-day4
docker compose -f deploy/docker-compose.yml --profile app up -d --no-deps --force-recreate api worker nginx
docker compose -f deploy/docker-compose.prodlike.yml up -d --no-deps --force-recreate --build api worker nginx

DAY4_BASE_URL=http://127.0.0.1:8082 TEST_DATABASE_URL=<property_ai_day4> python scripts/ops/day4_e2e.py
# → DAY4_CROSS_PROCESS_E2E=PASS (Fake providers; not live vendor)

python scripts/ops/day4_failed_task_drill.py
# → DAY4_FAILED_TASK_DRILL=PASS (WO 60 AI.UNAVAILABLE replay → PENDING_ASSIGNMENT;
#    WO 61 ENQUEUE_FAILED replay → PENDING_ASSIGNMENT)

python scripts/ops/day4_perf_run.py
# cold vs pooled tables in reports/day4-verification.md §15.8

cd backend && python -m pytest -q --tb=line
# TEST_DATABASE_URL unset (must stay property_ai_test)
# → 531 passed, 2 skipped, 61 warnings in 408.56s

pip-audit -r <image freeze minus local package> --disable-pip --no-deps
# isolated venv under %TEMP%\property-ai-cve-20260917
```

Live wrappers skipped inside pytest on purpose. Independent results are above, not the skip.

## Dual Redis+broker (paused)

Without a MySQL `failed_jobs` table there is no durable compensation evidence if both Redis and the broker are down after WO commit. **Do not report recovery PASS** for that case.

Minimum 0008 (not created):

```text
failed_jobs (
  id BIGINT PK,
  app_env, operation, sanitized_args JSON,
  error_code, status, work_order_id NULL,
  task_id, replay_task_id,
  lease_until, created_at, updated_at
)
```

Wait for operator approval before adding a migration.

## CVE minimum upgrade (not applied)

Frozen stack. Propose only if approved later:

| Package | Now | Propose | Why not applied |
| --- | --- | --- | --- |
| python-multipart | 0.0.20 | 0.0.31 | form DoS / separator issues; FastAPI uploads |
| PyJWT | 2.10.1 | 2.13.0 | several verifier bugs; HS256-only usage reduces exposure |
| orjson | 3.10.15 | 3.11.5 | dumps recursion |
| cryptography | 44.0.0 | needs review vs 46/49/50 | wheel OpenSSL + cert bugs; may be a large bump |
| starlette | 0.41.3 | 0.49.1 first, 1.3.1 later | Range/Host/form; 1.x is a major |

No batch upgrade. Base image OS CVEs **NOT_RUN** (Scout login blocked).

## Skips / blocks unchanged

- Live vendors / production WeChat: **BLOCKED_BY_CREDENTIALS**
- TLS domain cert: **BLOCKED_BY_DOMAIN_CERT**
- Real-device miniprogram: **NOT_RUN**
- AMQP DLX: not implemented
- Staging DevAuth: remains disabled

## Remaining publish blockers

1. Uncommitted remediation (this tree).
2. Cold-connect WO create P95 > 500ms on this host.
3. Frozen dependency CVEs not upgraded.
4. No `failed_jobs` table for dual-outage compensate.
5. Live model/WeChat/TLS/device still blocked.
6. Infra ports other than 8080/8081/8082 still `0.0.0.0`.

## Suggested commit (do not run)

```
fix(day4): close known verification defects without entering Day 5

Stop silent service rollback, require SYSTEM audit:read for /opsz,
record and replay failed Celery work, and make Day 4 E2E fail closed.
```

STOP. Not entering Day 5.
