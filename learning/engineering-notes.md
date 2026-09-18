# Engineering notes (personal)

These notes explain the **current implementation and what was actually verified on Day 4**.
They are **not** product requirements. If they conflict with `docs/`, `AGENTS.md`, or `.cursor/rules/`, the specs win.

Runtime, seeds, RAG ingest, Docker images, and the miniprogram must not read this directory.

Verification labels: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED_BY_CREDENTIALS.

---

## Transaction boundary vs `_execute`

Files: `backend/app/core/database.py` (`begin_owned_transaction`, `run_owned_transaction`, `run_composing_transaction`). `transaction_context` / `run_service_transaction` are **owned aliases** and do **not** auto-join.

Top-level use cases own begin/commit/rollback. Nested work uses an explicit composing entry that requires an **active** owned transaction object, not just a count in `session.info`. Any already-open transaction — including a leftover SQLAlchemy `AUTOBEGIN` from a SELECT, and including a Session whose `new`/`dirty`/`deleted` collections are empty — is **refused** before new side effects. The helper does **not** rollback, commit, or join that leftover work. “Collections are empty, so rollback is safe” is **incorrect** and was removed (`_is_clean_autobegin` / `_close_unowned_read_transaction`). HTTP Auth uses a separate Session closed by the dependency. Reloading a principal inside an owned business transaction reads in that same transaction. WeChat / development tokens return only after the owned audit txn commits. AI/embed/push: Tx A ends, provider runs, Tx B starts. Tests: `tests/integration/test_day4_silent_rollback.py` (independent Session persist; flushed INSERT/UPDATE/DELETE/Core execute; query autobegin not discarded). Status: **PASS** on disposable MySQL. Auto-join is **not** an accepted pattern.

---

## Pytest vs live `APP_ENV=test` enqueue

File: `backend/app/worker/enqueue.py` (`running_under_pytest`, `enqueue_celery_task`).

- Under pytest (`PYTEST_CURRENT_TEST`): never hit RabbitMQ; optional `BackgroundTasks` in-process.
- Live process with `APP_ENV=test` and `AI_ENQUEUE_ON_CREATE=true` (Day 4 Compose): `apply_async` to `resolved_celery_queue`.

`Settings.should_enqueue_ai_on_create` honors the flag in test (`backend/app/core/config.py`). Day 4 E2E **PASS** (Fake worker). Status: PASS for that distinction; Celery eager was **not** used for E2E.

---

## Failed tasks are Redis + named publish, not AMQP DLX

Files: `backend/app/worker/failed_tasks.py`, `backend/app/cli/replay_failed_task.py`, `celery_app.py` `task_failure`.

Lookup is direct `hget` by `record_id` (list paging is separate). Replay validates per-operation args, reserves attempt/`task_id` before enqueue, and CAS-updates by attempt. Celery `task_success` is **not** business `SUCCEEDED`. `FAILED`+`INVALID_OUTPUT` is `PERMANENT_FAILED`. Named JSON publish to `{queue}.dlq` is **not** AMQP DLX.

Focused-fix live drill **PASS** (Fake): WO **488** `AI.UNAVAILABLE` record `5c0a106e-17e0-4de1-b7e8-df3cdae1ac83` replay → `PENDING_ASSIGNMENT`; WO **489** `ENQUEUE_FAILED` `enqueue-82cc66444856428689acf39cef0a811e` replay → `PENDING_ASSIGNMENT`. Matching is structured fields, not `str(work_order_id) in line`. Dual Redis+broker outage is **not** recovery PASS; no 0008 outbox/sweeper yet. Notification/knowledge claim on 0007 columns is **PARTIAL** (no dedicated owner token; Development push is at-least-once). Do not say incomplete task recovery is accepted.

---

## `/readyz` honesty and `/opsz` boundary

Files: `backend/app/core/readiness.py` (`NOT_CHECKED`), `backend/app/api/v1/health.py`.

Unprobed LLM/VLM/ASR/embedding/reranker/storage → `not_checked` + `live_vendor_not_probed`, never fake `up`. MySQL is critical.

`/opsz` requires `RbacService.require_permission(principal, "audit:read", OrganizationScope(SYSTEM))`. Flat `Principal.permissions` is not enough. PROPERTY/TENANT managers with `audit:read` on a property still **403**. Unauthenticated **401**. Isolated Day 4 SYSTEM admin **200** without secrets. Prodlike `:8081` unauthenticated **401** after image rebuild. Operator 200 on staging is **NOT_RUN** (no staging login; DevAuth not enabled).

---

## Queue isolation is vhost + user

Compose: `deploy/docker-compose.day4.yml`. User `day4` permissions **only** on vhost `property_ai_day4`. Worker `-Q property_ai.day4` is necessary but not sufficient. Test: `tests/integration/test_day4_queue_vhost.py` **PASS**.

---

## First-time WeChat user vs self bind-apply

File: `backend/app/service/resident.py` (`_create_bind_request`, `_ensure_resident_role`).

Self-apply is an applicant/ownership policy: an ACTIVE user may submit a PENDING bind request for themselves. Having a role on property A does not block applying to property B. Apply does **not** create `ResidentHouseRelation` or grants. Approve is atomic: request APPROVED + relation + PROPERTY RESIDENT grant (reuse if present) + audit. Missing RESIDENT role is `IAM.ROLE_NOT_FOUND` and rolls back; the resident service does not create admin/catalog rows.

Verified: pytest new-user / A→B / staff self-apply / missing-role rollback / concurrent grant uniqueness; live 8082 apply+approve+WO **PASS**. Production WeChat **BLOCKED_BY_CREDENTIALS**. This is not “any user may access any property”.

---

## Cross-day load is counted from ACTIVE assignments

Files: `backend/app/service/employee.py` check-in; `WorkOrderRepository.count_active_assignments_for_employee`.

A new `EmployeeDailyStatus` row must not start `current_load` at 0 if ACTIVE assignments exist. Test `test_cross_day_checkin_preserves_active_assignment_load` **PASS**. Capacity second-assign **PASS** (`WO.EMPLOYEE_NOT_ELIGIBLE`).

---

## Milvus rebuild source of truth is MySQL

File: `backend/app/cli/rebuild_knowledge_index.py` (`rebuild=True` ingest from ACTIVE `KnowledgeDocument`).

Day 3 rebuild of 0 docs is not data-bearing recovery. Day 4 used synthetic A/B/staff/archived docs, unique collection, citation + ACL after rebuild **PASS**. Development `knowledge_chunks_v1` was not dropped.

---

## Object storage vs MySQL dump

MySQL restore does not move JPEG bytes. Day 4 restore test put a unique object in bucket `property-ai-day4-restore`, asserted the marker absent from dump, SHA-256 unchanged after SQL restore **PASS**.

---

## Learning / image copy scope

`deploy/Dockerfile` copies `backend/` only. `.dockerignore` excludes `learning/`, `reports/`, `miniprogram/`, `docs/`. No runtime import of `learning/`. Interview Q&A in `learning/interview-qa.md` is not engineering acceptance and is not a RAG source.
