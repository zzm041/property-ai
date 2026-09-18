# Day 4 — External-review findings and directed corrections

Not a release PASS. Not Day 5. Corrections after the reviewed WIP checkpoint are **uncommitted**. No push. No Release tag.

Status labels: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED / BLOCKED_BY_CREDENTIALS / BLOCKED_BY_DOMAIN_CERT.

Prior evidence in `reports/day4-verification.md` §§1–15 and `reports/day4-remediation.md` is retained. Counts there are **not** reused as this round’s result.

---

## 1. Checkpoint (recovery points only)

External review allowed saving the reviewed WIP. It did **not** approve marking Day 4 all PASS.

| Item | Value |
| --- | --- |
| Expected pre-checkpoint HEAD | `3c7b9ac90c7ec34db6a6bdbc961070b6c315791a` |
| Reviewed material | `C:\Users\lmj\AppData\Local\Temp\property-ai-day4-review\day4-remediation-diff.txt` |
| Review file SHA-256 | `86cdb5fb67f9630e44678eea4df181d230d96322d2cf4249d0614c319e2dce12` |
| Engineering checkpoint | `ee0114006d56dcdf4c2da23df09d6a1d13a824be` — `chore: checkpoint Day 4 remediation with review findings` |
| Learning checkpoint / correction baseline | `5e9e8ba5da93c700a56414f082f6011aea56c4a9` — `docs(learning): checkpoint separate remediation notes` |
| Interpreter | `d:\property-ai\.venv\Scripts\python.exe` |
| Alembic files 0001–0007 | **not modified**. Runtime DBs remain `20260917_0007` |
| Images used for live 8082 | `property-ai-api:latest` `sha256:5dde7526397449d6ab5a66fc058a9700943151c166fbfb8dfa57e605a355e3a1` ; `property-ai-worker:latest` `sha256:f4c989eda64ee99df5652ed3c930a5a420e9f4efcea2d0896b308fb3c000883c` |
| Recreated this round | only `api-day4` and `worker-day4` (and drill recreates of those two). Shared mysql/redis/rabbit/minio/milvus **not** recreated |

Staging was empty at the checkpoint. `git add .` / `-A` / `-f` were not used. `export_diff.py` and the review file were not committed.

Working tree after corrections: **dirty**. This document is not a commit.

---

## 2. Review findings → this-round change

| Finding | Correction | Result |
| --- | --- | --- |
| `transaction_context` auto-joined any `in_transaction` (autobegin vs owned outer) | Top-level `run_owned_transaction` / `begin_owned_transaction`; explicit `run_composing_transaction`; refuse unexpected open txn without commit/rollback of caller work | **PASS** pytest on disposable MySQL |
| WeChat token could return while audit was still uncommitted | Token returned only after owned txn commit | **PASS** unit/integration; live WeChat **BLOCKED_BY_CREDENTIALS** |
| AI/embed could hold txn across provider calls via nullcontext | Tx A commit → provider → Tx B; leftover txn refused | **PASS** pytest |
| Failed-task lookup scanned recent rows; args dropped unknown keys; Celery success = business success; races | Direct `hget`; per-operation arg contract; reserve attempt/task_id; CAS; distinct statuses | **PASS** unit + live this-round drill |
| E2E accepted readyz 503, ID-only notifications, APPROVED as published | Happy path requires readyz 200, exact resource_type+id, PUBLISHED only; worker rc+inspect | Live **PASS**; real-path negatives **PASS** |
| Logs treated recursive field filter as arbitrary-text safety | JSON-string / dict-repr regex plus provider `error_type` only; no disabling error logs; no claim of complete unknown-text safety | **PASS** rendered-output tests |
| Perf labeled A as server latency; dataplane 0.0.0.0 | Methods A–D labeled; YAML bind `127.0.0.1`; running bind **not** applied | Perf A **PARTIAL** vs NFR; dataplane apply **NOT_RUN** |
| CVE unread / blind Starlette 1.x | Re-read isolated pip-audit JSON; FastAPI constraint checked on PyPI; no upgrade | Proposal **not applied** |

---

## 3. Transaction boundary

Contract in `backend/app/core/database.py`:

1. Top-level use cases own begin/commit/rollback (`run_owned_transaction`).
2. Nested work uses `run_composing_transaction` / `require_owned_transaction`. Public `_execute` does **not** auto-join.
3. Unexpected open transaction is refused **before new side effects**. Caller txn is not committed or rolled back. Clean leftover SQLAlchemy `AUTOBEGIN` with no new/dirty/deleted may be closed as Session hygiene.
4. Composing errors propagate; the outer owner rolls the unit back.
5. No bulk implicit commit. No new transaction framework.

Services switched: Employee, Resident, WorkOrder, Property, RBAC mutation, WeChat Auth, Analysis, Incident summary, Announcement, Knowledge, Notification deliver/mark_read, Auth leftover autobegin close, Dev seed.

WeChat: `run_owned_transaction` then return `issued`. An outer txn cannot turn this into “commit later”.

AI / embedding / announcement LLM / incident LLM / notification provider: Tx A ends, provider runs with `in_transaction` false, then Tx B. Analysis `_call_providers` and knowledge ingest refuse if a business txn is still open.

Tests (`tests/integration/test_day4_silent_rollback.py`):

- Independent Session/connection proves persist (not `expire_all` on the same Session).
- Pending and flushed caller rows are not silently dropped on refuse.
- Composing outer failure rolls business + audit together.
- Audit/commit failure is not proven by a test-first manual rollback.
- Public create refuses an unexpected `session.begin()`.

**PASS** on disposable `property_ai_test`.

---

## 4. Failed tasks (no new table)

`backend/app/worker/failed_tasks.py` remains Redis HASH + ZSET. 0001–0007 untouched.

| Requirement | Implementation |
| --- | --- |
| Direct `record_id` lookup | `hget` + task_id index; list paging is separate |
| Operation arg contract | `validate_operation_args`; sanitize keeps positions |
| Replay race | Reserve attempt + `replay-{uuid}` task_id **before** `apply_async(task_id=…)` |
| CAS | Update requires current attempt; stale original-id callback ignored; terminal cannot become `ENQUEUED` |
| Publish / uncertain / lease | `PUBLISH_FAILED` / `UNCERTAIN` / `LEASE_EXPIRED` distinct from `ENQUEUED` |
| Complete vs expire vs permanent | `SUCCEEDED` / `IGNORED_EXPIRED` / `PERMANENT_FAILED` / `REPLAY_FAILED` |
| Celery `task_success` ≠ business success | Signal no longer marks SUCCEEDED. `FAILED`+`INVALID_OUTPUT` → `PERMANENT_FAILED` |
| Replay re-fail | Updates original via `by_task` index; attempt history kept |
| Concurrent replay | Lease plus in-flight/terminal status, not 120s lock alone |

Live drill (this round, not backlog):

- WO **164** `analyze_work_order_task` `AI.UNAVAILABLE` `RECORDED` record `9a1720cf-a185-469c-aacc-65ebc99605b1` → replay rc=0 → `PENDING_ASSIGNMENT`, `analysis_rows=1`.
- WO **165** `enqueue-9ee1e3b6cb684ecb98dd9b501c9e81c6` `ENQUEUE_FAILED` → replay rc=0 → `PENDING_ASSIGNMENT`.
- Compose/list/replay return codes checked. `finally` restore rc=0. List-first old perf `OperationalError` rows were **not** selected.

**PASS** for Fake LLM + available Redis. Dual Redis+broker remains **not** recovery PASS. See §9 (0008 unexecuted).

---

## 5. E2E / drill honesty

`scripts/ops/day4_e2e.py`:

- Happy `readyz` must be 200. Injected 503 is a separate failure test.
- Notifications: exact `resource_type` + `resource_id`. No “any notification with the same numeric id”.
- Announcement publish requires `PUBLISHED`, not `APPROVED`.
- Worker stop/start checks compose rc and `docker inspect State.Running`.
- Missing `DAY4_BASE_URL` / `TEST_DATABASE_URL` → `NOT_RUN`, not silent skip of the chain.

Live 8082 **PASS** (Fake providers): healthz/readyz 200; bind apply PENDING then APPROVED; WO 162 idempotent duplicate; worker pause left WO 163 `PENDING_ANALYSIS`; resume → `PENDING_ASSIGNMENT`; CLOSED; knowledge `document_id=4` cited; announcement 4 `PUBLISHED`; this-round notifications for bind / work order / announcement.

Real-path negatives (`tests/unit/test_day4_e2e_assertions.py`) drive `_run_flow` through HTTP/subprocess doubles: assign 409, receipt 422, wrong notification type same id, async timeout, final not CLOSED, worker stop rc. All non-zero / no overall PASS. Not `finalize(True)` or `Checks.expect(False)` as the only proof.

Drill restore failure fails the drill. **PASS** this run (`finally_*_restored=0`).

---

## 6. Logging

Recursive field filter kept. Additional `redact_text` for JSON strings (`password` / `api_key` / `session_key`) and Python dict repr. Provider `_log_failure` logs `error_type`, latency, safe URL, error code — not request bodies, auth responses, or private response text. Tracebacks keep type / location / error code after redaction.

Tests cover API / Worker / stdlib rendered output, exception chains, JSON-string and dict-repr synthetics.

**Not claimed:** regex makes arbitrary unknown text free of secrets.

Error logs were not disabled. **PASS** (synthetic).

---

## 7. Performance (methods A–D) and dataplane bind

NFR unchanged (`docs/08-NON-FUNCTIONAL.md`): work-order create P95 &lt; 500ms excluding AI. Audit / RBAC / transactions were not removed.

Fixed load this run: Day 4 Nginx `http://127.0.0.1:8082`, Fake providers, unique `Idempotency-Key` per POST, errors counted (all 0). Identity present (`house_id=1`; token redacted).

**Previous method A numbers (§15.8) retained.** New run on the corrected images:

| Label | n | c | mode | P50 ms | P95 ms | errors |
| --- | --- | --- | --- | --- | --- | --- |
| healthz A cold lifecycle (old method) | 30 | 1 | A | 524.40 | 570.82 | 0 |
| healthz A cold lifecycle (old method) | 100 | 20 | A | 1800.02 | 2148.06 | 0 |
| healthz B nokeepalive (new) | 30 | 1 | B | 4.46 | 26.82 | 0 |
| healthz C pooled (new) | 30 | 1 | C | 3.03 | 3.97 | 0 |
| healthz C pooled (new) | 100 | 20 | C | 32.50 | 42.69 | 0 |
| healthz D elapsed (new) | 30 | 1 | D | 4.37 | 5.53 | 0 |
| WO POST A cold | 30 | 1 | A | 550.84 | **593.73** | 0 |
| WO POST A cold | 20 | 5 | A | 878.34 | **1099.34** | 0 |
| WO POST B nokeepalive (new) | 30 | 1 | B | 21.12 | 43.81 | 0 |
| WO POST C pooled (new) | 30 | 1 | C | 22.36 | 47.85 | 0 |
| WO POST C pooled (new) | 20 | 5 | C | 93.37 | 109.74 | 0 |
| WO POST D elapsed (new) | 30 | 1 | D | 21.39 | 41.88 | 0 |

A is client construct + request + close. **Not** pure server latency. B/C/D must not be quoted as the old cold method. Cold WO create **misses** 500ms → **PARTIAL**.

### Dataplane bind

Nginx already `127.0.0.1:8080/8081/8082`. Compose YAML for mysql/redis/rabbit/minio/milvus host ports is now `127.0.0.1:…` in `deploy/docker-compose.yml` and `deploy/docker-compose.prodlike.yml`.

**Running containers still publish `0.0.0.0`** (and `[::]`). Applying YAML requires recreating shared data services. **Not executed.**

Minimum apply plan (needs explicit authorization; do not run automatically):

| Object | Host ports now | YAML target | Recreate risk |
| --- | --- | --- | --- |
| `property-ai-mysql-1` | 0.0.0.0:3306 | 127.0.0.1:3306 | named volume usually survives `--force-recreate`; still dump first |
| `property-ai-mysql-test-1` | 0.0.0.0:3307 | 127.0.0.1:3307 | **tmpfs — recreate wipes `property_ai_test` and `property_ai_day4`** |
| `property-ai-redis-1` | 0.0.0.0:6379 | 127.0.0.1:6379 | volume |
| `property-ai-rabbitmq-1` | 0.0.0.0:5672,15672 | 127.0.0.1 | volume |
| `property-ai-minio-1` | 0.0.0.0:9000-9001 | 127.0.0.1 | volume |
| `property-ai-milvus-1` | 0.0.0.0:19530,9091 | 127.0.0.1 | volume + etcd |
| prodlike mysql/redis/rabbit/minio/milvus | 3316/6380/5673/9010/19531 `0.0.0.0` | 127.0.0.1 | same class |

Backup / restore if authorized:

1. Dump `property_ai` from `:3306`, and `property_ai_test` + `property_ai_day4` from `:3307`, to an operator-chosen path **outside** the repo. Do not commit dumps.
2. Confirm dumps restore on a disposable schema before touching tmpfs mysql-test.
3. `docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate` the listed **data** services (not `down -v`).
4. Restore `property_ai_test` and `property_ai_day4` immediately after mysql-test comes up.
5. Recreate only API/worker/nginx if they lose links.
6. Do not treat tmpfs mysql-test as “data still there after recreate”.

Uvicorn `--host 0.0.0.0` **inside** the container remains (published only via Nginx 127.0.0.1). That is the intended Docker network listen, not a host dataplane publish.

---

## 8. Dependency scan (no upgrade applied)

Source: existing isolated `pip-audit 2.7.3` `--disable-pip --no-deps` JSON  
`C:\Users\lmj\AppData\Local\Temp\property-ai-cve-20260917\pip-audit-no-deps.json`  
(image freeze; scan dated 2026-09-17). FastAPI `Requires-Dist` re-checked on PyPI this round.

Docker Scout on `property-ai-api:latest`: **BLOCKED** (`docker login` required). Trivy: **not installed**. OS/image CVEs **NOT_RUN**.

pip-audit: **28 findings / 5 packages**. FastAPI `0.115.12` itself had **0** vulns in that scan.

Installed FastAPI constraint: `starlette<0.47.0,>=0.40.0`. **Starlette 0.49.1 and 1.x cannot be installed under the current FastAPI pin.**

| Package | Installed | IDs / aliases | Advisory source | Applicability here | Fix version(s) | Compatibility |
| --- | --- | --- | --- | --- | --- | --- |
| python-multipart | 0.0.20 | CVE-2026-24486, CVE-2026-40347, CVE-2026-53538/53539/53540, CVE-2026-42561 | PyPI/GHSA via pip-audit | FastAPI `request.form()` / uploads in scope. Default `UPLOAD_KEEP_FILENAME` path-traversal needs non-default options. Form DoS / `;` separator are relevant | **0.0.31** covers the set | Patch; keep FastAPI 0.115.12 |
| PyJWT | 2.10.1 | CVE-2026-32597, CVE-2026-48522–48526; CVE-2025-45768 disputed | PyPI/GHSA | API issues **HS256 with a local secret**; no `PyJWKClient`. `crit` header still in scope. JWKS/jku/HMAC-confusion reduced | **2.13.0** | Patch; regression: login + WeChat token issue/verify |
| orjson | 3.10.15 | CVE-2025-67221 | PyPI/GHSA | `dumps` recursion if attacker JSON is dumped | **3.11.5** | Patch; JSON responses |
| cryptography | 44.0.0 | CVE-2024-12797 (OpenSSL wheel), CVE-2026-34073, CVE-2026-26007, CVE-2026-69247/69248/69249, GHSA-537c-gmf6-5ccf | PyPI/GHSA | No PKCS7 mail gateway. TLS/JWT adjacent via wheels. Name-constraint / chain DoS if we verify untrusted certs | 44.0.1 (OpenSSL only) … **50.0.0** for PKCS7 oracle | **Major**. Review PyMySQL TLS + wheel OpenSSL |
| starlette | 0.41.3 | CVE-2025-54121, CVE-2025-62727, CVE-2026-48710, CVE-2026-48817/48818, CVE-2026-54282/54283 | PyPI/GHSA | We do **not** use `StaticFiles`/`FileResponse` (Range / Windows UNC lower). Host reconstruction and urlencoded `max_fields` still relevant if code trusts `request.url` | Range **0.49.1**; Host/form **1.3.1** | **0.49.1 needs FastAPI ≥0.121.0** (`starlette<0.50`). **1.3.1 needs FastAPI 0.135+** (`starlette>=0.46.0` with **no upper bound** — must pin). Do not “just install Starlette 1.x” |

PyPI FastAPI ↔ Starlette (this round):

| FastAPI | Starlette requirement |
| --- | --- |
| 0.115.12 / 0.115.14 | `>=0.40.0,<0.47.0` (current pin) |
| 0.116.1 | `>=0.40.0,<0.48.0` |
| 0.118.0 / 0.120.0 | `>=0.40.0,<0.49.0` |
| 0.121.0 | `>=0.40.0,<0.50.0` → can pin `starlette==0.49.1` |
| 0.128.0 | `>=0.40.0,<0.51.0` |
| 0.135.0 / 0.135.1 | `>=0.46.0` **no upper bound** |

### Minimum upgrade (not applied)

1. `python-multipart==0.0.31`
2. `PyJWT==2.13.0`
3. `orjson==3.11.5`
4. Optionally `cryptography` only after an explicit ABI/TLS review (not 44 → 50 in the same change as FastAPI).
5. Starlette Range: **separate** change: FastAPI `0.121.x` + `starlette==0.49.1` (still not 1.x).
6. Starlette 1.3.1 Host/form: **later** change: FastAPI `0.135.x` with **explicit** `starlette==1.3.1`. Do not rely on FastAPI 0.135’s open Starlette range.

### Reliability without upgrade (current freeze)

- Keep Linux containers; do not serve `StaticFiles`/`FileResponse`.
- Nginx body-size limits already in front of 8082.
- HS256 local secret; no JWKS client.
- Do not parse untrusted PKCS7.

Regression if upgrades are later approved: auth tokens, multipart uploads, JSON encoding, health, Day 4 E2E, WeChat login unit tests. **Not executed this round.**

---

## 9. Persistent task / 0008 minimum plan (**not executed**)

Question: can existing MySQL job/business state compensate when Redis and the broker are both down?

**Analyze-on-create:** work order commits first (`PENDING_ANALYSIS`), then `apply_async`. If enqueue never happens, MySQL still has the row. A sweeper of `PENDING_ANALYSIS` without a successful analysis, older than a configured age, can re-enqueue using existing analyze idempotency. That path does **not** require a new table.

**What MySQL cannot cover alone:**

- Process exit after business commit and **before** Redis `record_failed_task` **and** before broker publish: Redis has no record; broker has no message; MySQL still has `PENDING_ANALYSIS`. A sweeper handles **this** operation. Other operations (ingest, notification deliver) may lack an equivalent parked status.
- Writing `failed_jobs` / Redis **only after** the enqueue exception still loses the window if the process dies between commit and that write.
- Dual Redis+broker outage: Redis record missing. Claiming recovery PASS from Redis is false. MySQL parked status can still be the intent store **if** a sweeper exists. **No sweeper is implemented.**

Compare:

| Option | Schema | Transaction | Delivery | Execution |
| --- | --- | --- | --- | --- |
| A. `PENDING_ANALYSIS` sweeper | none | none new | periodic `apply_async` | existing analyze idempotency |
| B. Transactional outbox | **0008** outbox rows in the **same** txn as the business commit | Service-owned txn writes outbox; publisher is separate | Publisher marks sent; uncertain publish ≠ success | Worker idempotent on `(operation, resource_id)` |
| C. `failed_jobs` after exception | **0008** | Still has the commit-then-record crash window unless inserted in the business txn (then it is B) | Replay CLI | same as today |

**Recommendation for approval (not built):** implement A immediately for work-order analyze; if ingest/notification need the same guarantee, approve **0008 outbox** (B), not a post-exception `failed_jobs` table. Outbox fields (draft): `id BIGINT PK`, `app_env`, `operation`, `sanitized_args JSON`, `resource_type`, `resource_id`, `status` (`PENDING`/`PUBLISHED`/`FAILED`/`UNCERTAIN`), `task_id`, `attempt`, `available_at`, `created_at`, `updated_at`, unique intent key. Delivery and execution must both be idempotent. Replay of Redis records stays an operator tool, not the sole durability.

Do not add 0008 until approved. This does **not** block the safety fixes already in Redis/CAS.

---

## 10. Verification actually run this round

| Suite | Command / evidence | Status |
| --- | --- | --- |
| Transaction | `pytest tests/integration/test_day4_silent_rollback.py` | **PASS** |
| Replay race / permanent / expired | `pytest tests/unit/test_day4_failed_tasks.py` | **PASS** |
| Script real-path negatives | `pytest tests/unit/test_day4_e2e_assertions.py` | **PASS** |
| Log final output | `pytest tests/unit/test_day4_logging_redaction.py` | **PASS** |
| Combined four | 38 passed in 5.59s | **PASS** |
| Full regression | `pytest -q --tb=line` `APP_ENV=test` `ALLOW_DESTRUCTIVE_TEST_DATABASE=true` `TEST_DATABASE_URL` unset | **547 passed, 2 skipped**, 395.78s. 0 failed. Skips are live wrappers without `DAY4_BASE_URL` / `DAY3_PRODLIKE_BASE_URL` — not treated as PASS |
| Strict cross-process E2E | `DAY4_BASE_URL=http://127.0.0.1:8082` `TEST_DATABASE_URL=…property_ai_day4` `scripts/ops/day4_e2e.py` | **PASS** (Fake) |
| This-round fail drill | `scripts/ops/day4_failed_task_drill.py` | **PASS** (Fake; WO 164/165) |
| Labeled perf A–D | `scripts/ops/day4_perf_run.py` | Method A WO P95 **PARTIAL**; B/C/D recorded, not NFR substitutes |

No failing tests were deleted to manufacture green.

---

## 11. Still blocked / remaining

| Item | Status |
| --- | --- |
| Live Qwen / ASR / BGE-M3 / reranker | BLOCKED_BY_CREDENTIALS |
| Production WeChat `code` / TLS session_key path | BLOCKED_BY_CREDENTIALS |
| Real-device miniprogram | NOT_RUN |
| TLS / domain cert | BLOCKED_BY_DOMAIN_CERT |
| Apply dataplane `127.0.0.1` to running shared services | NOT_RUN (authorization required; mysql-test tmpfs) |
| 0008 outbox / sweeper | NOT_RUN |
| Frozen dependency upgrades | not applied |
| Container OS CVEs | BLOCKED (Scout login) / Trivy missing |
| Cold-connect WO create P95 vs 500ms | PARTIAL |
| Day 4 all PASS | **not claimed** |
| Day 5 | not entered |

---

## 12. Suggested later commit (do not run)

```
fix(day4): own transactions and tighten failed-task replay

Refuse unexpected autobegin joins, return WeChat tokens only after
audit commit, and make failed-task replay CAS-safe without a new table.
```

No git add / commit / push after these corrections.

STOP. Not entering Day 5.
