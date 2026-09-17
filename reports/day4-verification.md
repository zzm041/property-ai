# Day 4 — Release Verification / Security / Recovery / Documentation Separation

Engineering evidence only. Interview Q&A is in `learning/`, not here.
No git add / commit / push. STOP. Not entering Day 5.

Status labels used below: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED_BY_CREDENTIALS / BLOCKED_BY_DOMAIN_CERT.

---

## 1. Current commit and database baseline

| Item | Actual value |
| --- | --- |
| Git HEAD | `090c41fd157ab8e4ebcf6eb7614e1b6082028d2d` |
| HEAD message | `feat(day2): connect production providers and harden integrations` |
| Tags | `day1-production-baseline`, `day2-integration-baseline` |
| Working tree | Day 3 + Day 4 **uncommitted** in this clone |
| Migrations 0001–0007 | **untouched** |
| Development MySQL `:3306` `property_ai` | `20260917_0007` + `fk_work_orders_incident_scope_incidents` present |
| Test MySQL `:3307` `property_ai_test` | `20260917_0007` after repair + full pytest (still `0007` after the cycle tests) |
| Day 4 DB `:3307` `property_ai_day4` | `20260917_0007` |
| Prodlike MySQL `:3316` `property_ai_staging` | `20260917_0007` |

This clone’s HEAD is still the Day 2 tag. The operator statement “Day 3 修改已提交” is **not** visible as a commit here. Day 3 and Day 4 files remain in the working tree.

Day 3 historical PASS counts are not reused as this day’s regression.

Disposable-only destructive work: `property_ai_test`, `property_ai_restore_drill`, `property_ai_day4`, MinIO bucket `property-ai-day4` / `property-ai-day4-restore`, Milvus `knowledge_chunks_day4` and `d4_rebuild_*`. Development `knowledge_chunks_v1` was listed after rebuild tests and was **not** dropped. No `docker compose down -v` on development. No global prune.

Test-DB repair (not a migration edit): an interrupted Alembic cycle had left `property_ai_test` at `20260917_0006` without `fk_work_orders_incident_scope_incidents` and without 0007 tables. Repair was `ALTER TABLE ... ADD CONSTRAINT fk_work_orders_incident_scope_incidents ...` then `alembic upgrade head`. Frozen files under `backend/alembic/versions/` were not modified.

---

## 2. Modified files (Day 3 leftover + Day 4 in this working tree)

### Tracked modifications

- `.cursor/rules/project.mdc`
- `.env.example`
- `AGENTS.md`
- `backend/app/api/v1/health.py`
- `backend/app/core/config.py`
- `backend/app/core/database.py`
- `backend/app/core/exception_handlers.py`
- `backend/app/core/logging.py`
- `backend/app/core/readiness.py`
- `backend/app/core/request_context.py`
- `backend/app/main.py`
- `backend/app/provider/asr/__init__.py`
- `backend/app/provider/embedding/__init__.py`
- `backend/app/provider/http.py`
- `backend/app/provider/llm/__init__.py`
- `backend/app/provider/reranker/__init__.py`
- `backend/app/provider/storage/__init__.py`
- `backend/app/provider/vision/__init__.py`
- `backend/app/provider/wechat/__init__.py`
- `backend/app/repository/work_order.py`
- `backend/app/service/employee.py`
- `backend/app/service/resident.py`
- `backend/app/worker/celery_app.py`
- `backend/app/worker/enqueue.py`
- `backend/tests/unit/test_ai_policy.py`
- `backend/tests/unit/test_day2_config_enqueue.py`
- `deploy/README.md`
- `deploy/docker-compose.yml`
- `deploy/nginx.conf`
- `docs/11-OPERATIONS.md`
- `docs/DIRECTORY.md`
- `scripts/README.md`

### Untracked

- `.dockerignore`
- `backend/app/cli/replay_failed_task.py`
- `backend/app/worker/failed_tasks.py`
- Day 3/4 tests under `backend/tests/unit/test_day3_*.py`, `test_day4_*.py`, `backend/tests/integration/test_day3_*.py`, `test_day4_*.py`
- `deploy/docker-compose.day4.yml`
- `deploy/docker-compose.prodlike.yml`
- `deploy/nginx-day4.conf`
- `deploy/nginx-tls.conf.example`
- `scripts/ops/mysql_backup.py`
- `scripts/ops/mysql_restore.py`
- `scripts/ops/day4_prepare.py`
- `scripts/ops/day4_e2e.py`
- `scripts/ops/perf_probe.py`
- `reports/day4-verification.md` (this file)
- `learning/engineering-notes.md`
- `learning/interview-qa.md`

---

## 3. Engineering / learning material separation

| Surface | Finding | Status |
| --- | --- | --- |
| `docs/` | Specs, architecture, security, operations remain here | PASS |
| Runtime `backend/app/` | No `learning/` imports | PASS |
| Seed / RAG import | Rebuild CLI reads MySQL `KnowledgeDocument` only (`app/cli/rebuild_knowledge_index.py`) | PASS |
| Business prompts | Unchanged; no interview Q&A injected | PASS |
| `miniprogram/` | No 面试/知识点 copy | PASS |
| `AGENTS.md` §8 | `learning/` cannot override `docs/` / rules; evidence in `reports/` | PASS |
| `.cursor/rules/project.mdc` | Same learning rule; Day 5 still requires explicit approval | PASS |
| `docs/DIRECTORY.md` | `reports/` vs `learning/` documented | PASS |
| `deploy/Dockerfile` | `COPY backend/ /app/` only | PASS |
| `.dockerignore` | excludes `learning/`, `reports/`, `miniprogram/`, `docs/`, `*.md` | PASS |
| Chinese comments | Not bulk-deleted | PASS |

`learning/` is not a product requirement source and is not in the production image or miniprogram package.

---

## 4. Data-bearing MySQL restore

**PASS** on disposable `property_ai_test` → `property_ai_restore_drill` (`tests/integration/test_day4_mysql_restore_relations.py`).

Verified after restore, not only `users` count:

- `alembic_version` matches source (`20260917_0007`)
- table counts: `users`, `houses`, `resident_house_relations`, `work_orders`, `work_order_assignments`, `work_order_logs`, `audit_logs`, `employees`
- JOIN: work order + assignment + log + house + reporter’s `resident_house_relations`
- audit rows for the restore drill request ids

Object checksum (isolated bucket `property-ai-day4-restore`):

- put unique payload, SHA-256 of `get_object` matches
- unique marker **absent** from mysqldump
- after MySQL restore, MinIO object still returns the same digest

**Database restore is not media-byte restore.** Files live in object storage.

Related: `tests/integration/test_day3_minio_live.py` SHA-256 put/get on Compose MinIO **PASS** in this regression.

Development MySQL was not dumped/restored.

---

## 5. Data-bearing Milvus rebuild and ACL

Day 3 rebuild of **0 documents is not accepted** as data-bearing recovery.

**PASS** `tests/integration/test_day4_milvus_rebuild_acl.py` against a unique collection `d4_rebuild_*` (not `knowledge_chunks_v1`):

1. Ingest four MySQL-backed documents (Fake embedding dim=8): Property A resident RULE, Property B resident RULE, Property A STAFF_ONLY, Property A resident FAQ then archive.
2. Query + citation **before** rebuild: resident cites Property A document/version.
3. Drop only the test collection; `rebuild_knowledge_index.main()` from MySQL ACTIVE rows.
4. After rebuild:
   - resident citation still the Property A document and version
   - Property A resident query does not cite Property B
   - resident does not cite STAFF_ONLY
   - staff does cite STAFF_ONLY
   - archived document remains invisible
5. Drop test collection. Live list after tests still included `knowledge_chunks_v1`.

Live Day 4 HTTP ingest (Fake): `document_id=2 version=1`; assistant `answered` with citation `document_id=1 version=1` (prior Day 4 doc in `knowledge_chunks_day4`). Fake embedding/LLM — **not** live BGE-M3 / Qwen.

---

## 6. Real HTTP / Nginx / Worker E2E

Isolated Compose project `property-ai-day4` (Nginx **8082**). Path:

`HTTP/Nginx → api-day4 → RabbitMQ vhost property_ai_day4 → worker-day4 -Q property_ai.day4 → MySQL property_ai_day4 / Milvus knowledge_chunks_day4 / MinIO property-ai-day4`

`APP_ENV=test`, `AI_ENQUEUE_ON_CREATE=true`, Development/Fake providers **labeled**. Not TestClient, not Service-in-process, not Celery eager, not BackgroundTasks for this path. DevAuth used only because `APP_ENV=test`. Staging/production DevAuth guard remains (`test_non_development_environments_reject_dev_auth`).

Script: `scripts/ops/day4_e2e.py`. Result: `DAY4_CROSS_PROCESS_E2E=PASS (Fake providers; not live vendor)`.

| Step | Result |
| --- | --- |
| `healthz` | 200, `request_id=d4-healthz` |
| `readyz` | 200 `ready`; llm/vlm/asr/embedding/reranker = `not_checked` |
| public `/opsz` | **401** |
| manager `/opsz` | 200, `celery_queue=property_ai.day4` |
| WeChat new user | 200 `request_id=d4-wx-orphan` |
| bind apply + approve | 200 / 200 |
| create WO | 200 `request_id=d4-wo-create` id=5 |
| duplicate Idempotency-Key | 200 **id=5** (no second mutation) |
| worker stopped | create id=6 stayed `PENDING_ANALYSIS` |
| worker started | `PENDING_ASSIGNMENT` |
| assign / receipt / confirm | 200; DB `work_orders.status=CLOSED` |
| knowledge ingest | 200 `document_id=2 version=1` |
| assistant query | 200 `answered` + citation |
| announcement publish | 200 |
| in-app notifications | 200, count=3 |
| check-in on rerun | 409 (already checked in; not a create failure) |

Worker task_ids from `property-ai-day4-worker-day4-1` (this rerun):

- `analyze_work_order_task` `b3c5edb7-d095-4862-bef2-9af35632759b`, `6761c517-2f82-425f-a5d9-b84f82dd9c5f`, `6413f0ea-35e6-4159-9b1a-89b33c43cfff`
- `ingest_knowledge_task` `0f8630ca-0e69-4903-be5f-147b74329ae6`
- `deliver_notification_task` `a6332e32-e608-439d-9e5b-3fb38e1817ad`, `bae69de4-9629-4939-a5df-f88c62976dc4`, `cc900d6f-7f37-4f52-a056-2f28cb03abe2`

Worker transport: `amqp://day4:**@rabbitmq:5672/property_ai_day4`. Queue list: only `property_ai.day4`.

Pytest wrapper `test_day4_cross_process_e2e` was **skipped** in the full suite because `DAY4_BASE_URL` was unset to avoid mixing pytest with the live worker. Live script result above is the evidence.

---

## 7. Queue isolation and failed-task recovery

### Isolation — PASS (vhost/user, not queue name alone)

`rabbitmqctl list_user_permissions day4`:

| vhost | configure | write | read |
| --- | --- | --- | --- |
| `property_ai_day4` | `.*` | `.*` | `.*` |

`property_ai` (development vhost) is **absent**. Worker `-Q property_ai.day4` is additional, not sufficient by itself.

`tests/integration/test_day4_queue_vhost.py` **PASS** in regression (prefers container `property-ai-rabbitmq-1`).

### Failed tasks — PARTIAL

Implemented without a new migration:

- Redis list `property_ai:{env}:failed_tasks` (`app/worker/failed_tasks.py`)
- named DLQ publish to `{queue}.dlq` via Kombu (not AMQP DLX / broker policy)
- `python -m app.cli.replay_failed_task`

Live probe: `record_failed_task` wrote `d4-live-fail-probe`; Redis list returned it; `property_ai.day4.dlq` **messages=1**.

Unit tests for record/replay mapping **PASS**.

**Not claimed:** AMQP dead-letter-exchange topology, live `replay_failed_task` of a real failed analyze (probe args were synthetic), or “DLQ recovery verified end-to-end”.

Enqueue failure now calls `record_failed_task` (`app/worker/enqueue.py`). Pytest still skips the broker via `running_under_pytest()`.

---

## 8. Observability honesty and `/opsz`

| Probe | critical | this Day 4 8082 readyz |
| --- | --- | --- |
| mysql | critical | **up** (checked_at set) |
| redis | non-critical | **up** |
| rabbitmq | non-critical | **up** |
| milvus | non-critical | **up** (connectivity, Fake collection) |
| llm / vlm / asr / embedding / reranker / storage | non-critical | **not_checked** (`live_vendor_not_probed`) |

Unprobed vendors were **not** labeled `up`.

`/opsz`: unauthenticated 401 on 8082; resident 403; operator 200 without secrets (`tests/unit/test_day4_readiness_opsz.py`, live E2E).

**PARTIAL leftover:** prodlike Nginx `:8081` still returns **200** for unauthenticated `/opsz` because that stack was **not** rebuilt with Day 4 images. Do not treat 8081 as the Day 4 contract.

---

## 9. Security and concurrency coverage

Requirement → code/API → how verified → status.

| Requirement | Code / API / page | Verification | Status |
| --- | --- | --- | --- |
| First WeChat user without house can apply to bind | `ResidentService` empty-`grants` skip of `resident_binding:create`; existing grants still checked on that PROPERTY scope | pytest `test_day4_wechat_bind`; live 8082 apply+approve | PASS (Fake WeChat). Production `code` login **BLOCKED_BY_CREDENTIALS** |
| Approve grants PROPERTY `RESIDENT` | `_ensure_resident_role` | live E2E: approved orphan created a WO | PASS |
| Permission bound to same Scope | `Principal.has_permission` / `RbacService.require_permission` | `test_property_scope_allows_only_exact_property`, `test_tenant_scope_allows_its_properties_but_not_cross_tenant`, `test_multiple_roles_form_permission_union_without_losing_scope` | PASS |
| Cross tenant/property IDOR | work order / resident / RBAC / files | existing Day 1/2 integration tests in this regression | PASS |
| File upload size/type | `/api/v1/files` | `test_day4_upload_limits` | PASS |
| File access | register/put ownership | `test_cross_tenant_file_content_is_not_found` 404; same-tenant non-uploader 403 | PASS |
| Capacity: two WOs to one staff | assign + `WO.EMPLOYEE_NOT_ELIGIBLE` | `test_second_assignment_to_full_employee_is_rejected` | PASS |
| Cross-day checkout / re-check-in vs load | `EmployeeDailyStatus.current_load` from `WorkOrderRepository.count_active_assignments_for_employee` | `test_cross_day_checkin_preserves_active_assignment_load` (`current_load == 1`) | PASS |
| Service must not silent-rollback caller uncommitted work | `EmployeeService._execute` (and other services) still `if session.in_transaction(): rollback()` | Inspected; **not** mass-refactored (frozen architecture) | **FAIL** (known defect; no schema/rule change attempted) |
| AI/notify enqueue failure recoverable | `enqueue_celery_task` → `record_failed_task` | unit + live Redis/DLQ publish | PARTIAL (replay of a real failed business task **NOT_RUN**) |
| Sensitive logs | `redact_log_event` | `test_day3_logging_privacy` | PASS |
| DevAuth not in staging/production | Settings validator | `test_non_development_environments_reject_dev_auth` | PASS |
| WeChat `session_key` not in API JSON | wechat login | Day 3/4 E2E assertions | PASS (Development provider) |
| Frozen dependency CVE / image rebuild | `pyproject.toml` + `python:3.12.10-slim-bookworm` | No pip-audit / distro CVE run this day | **NOT_RUN** — do not batch-upgrade without an approved minimum set |

Miniprogram pages were not exercised on a real device: **NOT_RUN**.

---

## 10. Performance (disposable 8082, Fake providers)

NFR from `docs/08-NON-FUNCTIONAL.md`: work-order create P95 **< 500ms** (no AI in the HTTP path); knowledge Q&A P95 **< 5s**. No “千人并发” claim.

| Probe | n | concurrency | errors | rps | P50 ms | P95 ms | P99 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| WO create (authenticated, Fake, async AI) | 30 | 1 | 0 | 68.0 | 13.97 | **20.63** | 21.15 |
| WO create | 20 | 5 | 0 | 7.87 | 616.14 | **658.21** | 659.64 |
| assistant/query Fake | 8 | 1 | 0 | — | 24.13 | 26.81 | 26.81 |
| `/healthz` | 30 | 1 | 0 | 2.3 | 484.7 | 505.73 | 560.25 |
| `/healthz` | 100 | 20 | 0 | 9.54 | 2070.16 | 2667.87 | 2706.23 |
| `/readyz` | 40 | 10 | 0 | 7.82 | 985.72 | 2736.57 | 2944.54 |

Interpretation:

- Sequential WO create **meets** the 500ms NFR in this disposable env (n=30, not a registered-user-scale test).
- Concurrent c=5 WO create **misses** 500ms P95 on this host (WSL2 + shared mysql-test + enqueue). Status **PARTIAL** vs NFR. Not a thousand-user result.
- Assistant P95 27ms is **Fake** retrieval/generation, **not** live BGE-M3/Qwen.

Resource snapshot after probes (`docker stats --no-stream`), not a saturation test:

- `api-day4` ~0.25% CPU, 156 MiB
- `worker-day4` ~0.10% CPU, **1.48 GiB** (Celery prefork concurrency 24)
- `mysql-test` ~0.33% CPU, 919 MiB
- `rabbitmq` ~0.26% CPU, 216 MiB
- `redis` ~0.22% CPU, 16 MiB
- `milvus` ~3% CPU, 244 MiB

**NOT_RUN:** MySQL thread wait / InnoDB row-lock time series; queue backlog under a designed load ramp; registered-user vs active-user vs simultaneous-request scale-out. After E2E, `property_ai.day4` depth was 0 (earlier 3 messages were drained).

---

## 11. Regression and skips

**This day’s full pytest (Day 4 env vars unset):**

`502 passed, 2 skipped` in 386s. **0 failed.**

Skipped (explicit reasons):

1. `test_live_nginx_health_ready_ops` — `DAY3_PRODLIKE_BASE_URL` not set. 8081 still serves public `/opsz` 200 on old images; the variable was left unset so a stale stack would not be reported as Day 4 PASS.
2. `test_cross_process_http_nginx_worker_e2e` — `DAY4_BASE_URL` not set in pytest. Live script against 8082 **PASS** (section 6).

Not skipped in this run: MinIO live, MySQL restore, Milvus rebuild ACL, queue vhost, WeChat bind, capacity, upload limits, readiness/opsz unit tests, Alembic drift + cycle.

---

## 12. Unverified / credential / cert blocks

| Item | Status |
| --- | --- |
| Live Qwen LLM / VLM | BLOCKED_BY_CREDENTIALS |
| Live ASR | BLOCKED_BY_CREDENTIALS |
| Live BGE-M3 embedding | BLOCKED_BY_CREDENTIALS |
| Live reranker | BLOCKED_BY_CREDENTIALS |
| Production WeChat `code` login | BLOCKED_BY_CREDENTIALS |
| Real-device miniprogram | NOT_RUN |
| TLS / domain cert on Nginx | BLOCKED_BY_DOMAIN_CERT (`deploy/nginx-tls.conf.example` only) |
| pip-audit / base-image CVE | NOT_RUN |
| AMQP DLX topology | NOT_RUN (not implemented; named publish only) |
| Live `replay_failed_task` of a real failed WO | NOT_RUN |
| Staging/production with DevAuth | must stay disabled; not used |

Fake results are **not** live vendor sign-off.

---

## 13. Remaining delivery blockers

1. **Git:** Day 3+4 still uncommitted on `090c41f` in this clone.
2. **Silent transaction rollback** in Service `_execute` (`employee.py` and siblings): caller uncommitted work can be discarded. Frozen-architecture defect; needs an approved Service-transaction design before a wide fix.
3. **Prodlike `:8081`** still on pre-Day-4 API images (`/opsz` public 200). Rebuild only that disposable stack if it will be reused; do not treat it as verified.
4. **Concurrent WO create P95** 658ms vs NFR 500ms on this host at c=5.
5. **Live vendor + WeChat + real device + TLS** still blocked.
6. **Failed-task replay** of a genuine worker failure is not live-verified.
7. **Dependency/image CVE review** not done; no unapproved version bump.

---

## 14. Recommended Commit Message

```
feat(day4): verify recovery, cross-process E2E, and separate learning docs

Harden opsz/readiness honesty, failed-task records, WeChat bind-onboarding,
and data-bearing MySQL/Milvus restore without changing migrations 0001-0007.
```

Do not commit until the operator requests it. This working tree still contains Day 3 files that were never committed in this clone.
