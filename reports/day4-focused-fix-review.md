# Day 4 — Focused-fix review

Not a release PASS. Not production-ready. Not Day 5. No push.

Status labels: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED / BLOCKED_BY_CREDENTIALS.

Historical pytest **547 passed** (prior correction review) is **not** this round’s result.

---

## 1. Start confirmation

| Item | Actual |
| --- | --- |
| Repository | `D:\property-ai` (main worktree). Detached recovery tree `D:\property-ai-recovery\20260918-142233-al39yg7p\` was **not** used |
| Start HEAD | `68c16b5254eedf79dfe0ebeed99c405688654269` — `docs(learning): recover matching correction notes` |
| Recovery commits (pre-fix, not restaged) | `7a9a938902e67a1e7d4c3ad535834ce5a31c2596`, `68c16b5254eedf79dfe0ebeed99c405688654269` |
| Interpreter | `d:\property-ai\.venv\Scripts\python.exe` — Python **3.12.10** |
| Test DB | `mysql+pymysql://…@127.0.0.1:3307/property_ai_test` revision **`20260917_0007`** |
| Day 4 E2E DB | `property_ai_day4` on the same mysql-test instance, revision **`20260917_0007`** |
| `test_dev_token_audit.py` | present at `backend/tests/integration/test_dev_token_audit.py`; retained in full pytest |
| Alembic files 0001–0007 | **not modified**. Migration **0008 not created** |
| Shared data plane | mysql / mysql-test / Redis / RabbitMQ / MinIO / Milvus **not** recreated or flushed |

Working tree at the start of this focused-fix round was clean on `68c16b5`. Cursor checkpoints were not restored.

Live Day 4 images used for E2E / drill / perf:

| Object | Value |
| --- | --- |
| `property-ai-api:latest` / `property-ai-worker:latest` | `sha256:a33cc8b3179e25ac43733d9d8752fbeb24ddc8318eadcef6548d4c1def1be350` (built 2026-09-18T07:17:44Z) |
| Recreated | `api-day4`, `worker-day4`; later `nginx-day4` only (config bind-mount). Development API image was not swapped |

The rebuild-CLI session close landed after that image build and was verified by local pytest, not by rebuilding the image again.

---

## 2. Problems and directed fixes

### 2.1 Transaction ownership (closed)

`begin_owned_transaction` refuses **any** `session.in_transaction()` before new side effects. It does not rollback, commit, or join. `_is_clean_autobegin` and `_close_unowned_read_transaction` are gone. Empty `new`/`dirty`/`deleted` is **not** a license to roll back.

`require_owned_transaction` checks an active transaction object, owned count ≥ 1, and `session.info[OWNED_TXN_OBJ_KEY] is session.get_transaction()`.

`AuthService.load_principal` is read-only. HTTP Auth already uses a separate Session closed by the dependency. Reloading inside an owned business transaction participates in that transaction.

Owned `session.begin()` still rolls back on the owner’s exception. Tokens (`issue_development_token`) return only after the owned audit commit; audit/commit failure does not return a token (`test_dev_token_audit.py` kept).

AI / embedding / notification provider: Tx A ends, `in_transaction` is refused if still open, then the provider, then Tx B.

Knowledge rebuild CLI was holding the listing autobegin across `ingest_version`. Snapshot `(id, version)`, commit if in transaction, then ingest.

### 2.2 Notification / knowledge claim (PARTIAL — schema)

Notification claim: atomic UPDATE while `PENDING` and lease expired/null; bump `attempt`; store `NTF.CLAIM:{hex}` in `last_error_code`; `next_retry_at` is the lease. Write-back matches attempt + claim code. Outcomes: `SUCCEEDED` / `FAILED` (`ProviderUnavailableError`) / `UNCERTAIN` (other Exception, stay PENDING) / `SKIPPED`. Two workers: only one CAS claim; pytest concurrent provider call count is 1.

Limitations (0007 CHECK `PENDING|SUCCEEDED|FAILED`): no `CLAIMING` status, no dedicated owner/token columns, claim token overloads `last_error_code`. `DevelopmentNotificationProvider` has **no** vendor idempotency key. Crash after the provider accepted a push and before local write-back is **at-least-once**, not exactly-once.

Knowledge ingest: `SELECT … FOR UPDATE`, PROCESSING + `started_at` 120s hold, snapshot includes attempt/version. Success/failure write-back requires the same attempt and PROCESSING. `_activate_version` still refuses `ARCHIVED` and a newer `current_version`. Stale failure cannot overwrite SUCCEEDED. Archive during embed does not reactivate. No claim-owner column: hold is time-based; **PARTIAL**.

### 2.3 Redis replay (closed for the lease/CAS contract)

Lua compare-and-delete / compare-and-expire (no `DEL` without owner, no GET-then-DEL). CAS retries WatchError three times. `TypeError`/`ValueError` → `PUBLISH_FAILED` and release; other `apply_async` exceptions → `PUBLISH_UNCERTAIN` and keep lease. Fast complete vs enqueue write-back: expected `REPLAY_RESERVED` cannot revert `SUCCEEDED`. Analyze / ingest / deliver record **business** outcomes; Celery success is not business success.

Tests use a dedicated Redis key namespace (`property_ai:test:day4-lease-…`). No `FLUSHALL`, no shared DB wipe.

MemoryRedis remains for unit tests; its no-op WATCH / ignored TTL is **not** the concurrency evidence.

### 2.4 Drill / perf honesty

Drill matching uses structured `operation` + resource id + original task/request id + `record_id`. Reverse test: UUID/timestamp digits must not match a work-order id. Rebuild/restore/replay command failure fails the drill.

Perf: `x-process-time-ms` and W3C `Server-Timing` `dur` are milliseconds; the probe does not guess units from magnitude. `response.elapsed` is labeled transfer time, not exclusive server processing. Nginx `add_header X-Request-Id $request_id always` overwrote the API echo; that header was removed on `nginx-day4.conf` so the API `X-Request-Id` passes through.

---

## 3. Verification this round

Commands used `d:\property-ai\.venv\Scripts\python.exe`. Pytest: `APP_ENV=test`, `ALLOW_DESTRUCTIVE_TEST_DATABASE=true`, `TEST_DATABASE_URL` **unset** (fixture default `property_ai_test`). Live scripts set `DAY4_BASE_URL=http://127.0.0.1:8082` and `TEST_DATABASE_URL=…/property_ai_day4` only for those processes.

| Suite | Evidence | Status |
| --- | --- | --- |
| Transaction / token / provider-txn | `tests/integration/test_day4_silent_rollback.py` + `test_dev_token_audit.py` inside full pytest | **PASS** |
| Async claim | `tests/integration/test_day4_async_claim.py` | **PASS** (MySQL; concurrent Sessions + Fake providers) |
| Real Redis lease / CAS | `tests/integration/test_day4_redis_lease.py` | **PASS** |
| Drill match negatives | `tests/unit/test_day4_drill_match.py` | **PASS** |
| Perf probe units | `tests/unit/test_day4_perf_probe.py` | **PASS** |
| Full pytest | `pytest -q --tb=line` in `backend/` | **577 passed, 2 skipped**, 476.20s, 0 failed |
| Pytest skips (not PASS) | `DAY4_BASE_URL is not set`; `DAY3_PRODLIKE_BASE_URL is not set` | skipped wrappers |
| Strict E2E | `scripts/ops/day4_e2e.py` against 8082 / `property_ai_day4` | **PASS** (Fake providers; not live vendor) |
| Failed-task drill | `scripts/ops/day4_failed_task_drill.py` | **PASS** (Fake). WO **488** `AI.UNAVAILABLE` record `5c0a106e-17e0-4de1-b7e8-df3cdae1ac83` replay → `PENDING_ASSIGNMENT`; WO **489** `enqueue-82cc66444856428689acf39cef0a811e` `ENQUEUE_FAILED` replay → `PENDING_ASSIGNMENT`. Compose restore rc=0 |
| Request-id echo | `GET /healthz` `X-Request-Id: echo-check-1` → response `echo-check-1` | **PASS** after nginx-day4 recreate |
| Labeled perf A–D | `scripts/ops/day4_perf_run.py` | see §4. Cold WO c=5 still **PARTIAL** vs NFR |

No tests were deleted or relaxed to go green. `test_dev_token_audit.py` was not weakened.

---

## 4. Performance (NFR unchanged)

`docs/08-NON-FUNCTIONAL.md` work-order create P95 &lt; 500ms excluding AI. Audit, RBAC, and transactions were not removed. Unique `Idempotency-Key` per POST. All jobs `errors=0`. `server_process_unit=milliseconds`. `request_id_echo_mismatches=0` on this run. `server_process_missing=0`.

**Prior method A–D client numbers** (correction-review images; retained, not replaced):

| Label | n | c | client P50 ms | client P95 ms |
| --- | --- | --- | --- | --- |
| WO POST A cold | 30 | 1 | 550.84 | **593.73** |
| WO POST A cold | 20 | 5 | 878.34 | **1099.34** |
| WO POST C pooled | 30 | 1 | 22.36 | 47.85 |
| WO POST C pooled | 20 | 5 | 93.37 | 109.74 |

**This-round** (image `a33cc8b3`, nginx without `$request_id` overwrite):

| Label | n | c | client P50 / P95 ms | server P50 / P95 ms | httpx elapsed P95 ms |
| --- | --- | --- | --- | --- | --- |
| healthz A cold | 30 | 1 | 292.28 / 326.38 | 1.19 / 1.52 | 26.61 |
| healthz A cold | 100 | 20 | 2186.61 / 2560.66 | 0.95 / 1.13 | 27.30 |
| healthz B nokeepalive | 30 | 1 | 15.35 / 29.92 | 1.13 / 1.54 | 29.76 |
| healthz C pooled | 30 | 1 | 4.63 / 5.71 | 1.17 / 1.41 | 5.60 |
| healthz C pooled | 100 | 20 | 32.79 / 47.98 | 16.21 / 27.56 | 46.68 |
| healthz D elapsed | 30 | 1 | 3.62 / 4.37 | 1.03 / 1.40 | 4.37 |
| WO POST A cold | 30 | 1 | 324.77 / **449.80** | 21.66 / 35.54 | 60.88 |
| WO POST A cold | 20 | 5 | 726.30 / **793.18** | 31.38 / 104.69 | 122.43 |
| WO POST B nokeepalive | 30 | 1 | 22.62 / 47.49 | 18.07 / 24.64 | 47.30 |
| WO POST C pooled | 30 | 1 | 22.03 / 27.97 | 19.01 / 23.86 | 27.85 |
| WO POST C pooled | 20 | 5 | 89.08 / 103.78 | 79.49 / 100.72 | 103.62 |
| WO POST D elapsed | 30 | 1 | 20.50 / 23.21 | 17.38 / 20.28 | 23.21 |

A is client construct + request + close, **not** server time. D is `response.elapsed`, **not** exclusive server processing. Server samples are middleware `x-process-time-ms` / `Server-Timing` at response start (milliseconds).

Cold WO create **c=5 client P95 793.18ms** still misses 500ms → **PARTIAL**. This-round c=1 A (449.80ms) is host-run variance versus the retained 593.73ms; it is **not** an NFR close. Per-statement SQL / InnoDB lock-wait traces were **not** captured; the remaining A cost is connection setup, not proven as a single SQL hotspot → unlocated item stays **PARTIAL**.

---

## 5. Closed vs still needing approval

### Closed in this focused-fix (engineering)

- Explicit transaction ownership; no clean-autobegin rollback
- Token only after audit commit; leftover Auth read txn not implicitly rolled back
- Provider calls outside the business transaction
- Notification CAS claim + attempt/claim-code write-back (within 0007 columns)
- Knowledge attempt/version write-back and `_activate_version` archive/newer-version guards
- Redis Lua owner-checked lease release/renew; CAS; publish failed vs uncertain; business replay outcomes
- Structured drill match; reverse UUID-digit test
- Server-ms units; request-id echo through nginx-day4
- Rebuild CLI no longer holds listing autobegin

### PARTIAL / not executed (need Schema, maintenance, or credentials)

| Item | Why |
| --- | --- |
| Notification exactly-once | No vendor idempotency; claim token lives in `last_error_code`; CHECK blocks `CLAIMING` |
| Knowledge durable owner | `started_at` lease only; no `claim_token` / executor column |
| Dual Redis+broker outage | No outbox/sweeper. Redis records are not a durable intent store |
| Migration 0008 | **Not created.** Draft below merges claim columns with the paused outbox plan |
| Dataplane `127.0.0.1` bind | YAML exists; **running** mysql/redis/rabbit/minio/milvus still `0.0.0.0`. **NOT_RUN** (mysql-test tmpfs would wipe `property_ai_test` / `property_ai_day4`) |
| Frozen dependency CVE patches | Not applied (python-multipart / PyJWT / orjson / cryptography / Starlette vs FastAPI pin) |
| Live Qwen / WeChat / BGE-M3 | **BLOCKED_BY_CREDENTIALS** |
| Staging `/opsz` operator 200 | **NOT_RUN**; DevAuth stays off on staging |

### Minimum 0008 (draft only — do not apply)

Merge with the paused reliability plan:

1. **Outbox** (same txn as business commit): `id BIGINT PK`, `app_env`, `operation`, `sanitized_args JSON`, `resource_type`, `resource_id`, `status` (`PENDING`/`PUBLISHED`/`FAILED`/`UNCERTAIN`), `task_id`, `attempt`, `available_at`, timestamps, unique intent key.
2. **Notification claim columns** (stop overloading `last_error_code`): `claim_token CHAR(32) NULL`, `lease_until DATETIME(6) NULL`. Keep status CHECK `PENDING|SUCCEEDED|FAILED` unless a later review accepts `CLAIMING`.
3. **Knowledge job owner**: `claim_token CHAR(32) NULL` on `knowledge_ingestion_jobs` (keep PROCESSING + `started_at`).
4. Sweeper for `PENDING_ANALYSIS` without a successful analysis (can start without 0008 if approved separately).

Do not use Redis locks as durable recovery.

---

## 6. Commits this round

| Hash | Message | Scope |
| --- | --- | --- |
| `265b3fba6734bae31ea659578b05861a72579917` | `fix(day4): enforce explicit transaction ownership` | database.py, auth.py, silent-rollback tests, caller cleanup |
| `afc90c46f92c14f3b07b47d4c8f13b427622008f` | `fix(day4): checkpoint async and replay corrections` | notification/knowledge/failed_tasks/tasks/replay CLI, async+redis tests, drill/perf |
| *(this document’s engineering follow-up)* | rebuild CLI session close, nginx-day4 request-id, this report | WIP; not production-ready |
| *(learning, separate)* | `docs(learning): update verified focused-fix notes` | `learning/` only |

Hashes after the follow-up commits are recorded in the off-repo manifest. This file is not rewritten to include its own blob hash.

---

## 7. STOP

Day 5 was not entered. Shared volumes were not deleted. History migrations were not edited. Review export is off-repo from `68c16b5` through the final commits of this round, including intermediate WIP.
