# Engineering notes (personal)

These notes explain the **current implementation and what was actually verified on Day 4**.
They are **not** product requirements. If they conflict with `docs/`, `AGENTS.md`, or `.cursor/rules/`, the specs win.

Runtime, seeds, RAG ingest, Docker images, and the miniprogram must not read this directory.

Verification labels: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED_BY_CREDENTIALS.

---

## Transaction boundary vs `_execute`

Files: `backend/app/core/database.py` (`transaction_context`, `run_service_transaction`); service `_execute` in employee / resident / work_order / knowledge / property; `rbac_mutation._execute_authorized`.

Top-level use cases own `begin/commit`. Nested helpers join an open transaction and **do not** rollback the caller to re-begin. HTTP Auth uses a separate Session from the business Session (`get_current_principal` vs `get_db_session`). Tests: `tests/integration/test_day4_silent_rollback.py`. Status after remediation: **PASS** on disposable MySQL (pending + flushed rows preserved; outer rollback drops business+audit together; top-level HTTP still commits).

---

## Pytest vs live `APP_ENV=test` enqueue

File: `backend/app/worker/enqueue.py` (`running_under_pytest`, `enqueue_celery_task`).

- Under pytest (`PYTEST_CURRENT_TEST`): never hit RabbitMQ; optional `BackgroundTasks` in-process.
- Live process with `APP_ENV=test` and `AI_ENQUEUE_ON_CREATE=true` (Day 4 Compose): `apply_async` to `resolved_celery_queue`.

`Settings.should_enqueue_ai_on_create` honors the flag in test (`backend/app/core/config.py`). Day 4 E2E **PASS** (Fake worker). Status: PASS for that distinction; Celery eager was **not** used for E2E.

---

## Failed tasks are Redis + named publish, not AMQP DLX

Files: `backend/app/worker/failed_tasks.py`, `backend/app/cli/replay_failed_task.py`, `celery_app.py` `task_failure`.

Permanent failures / enqueue exceptions write `property_ai:{env}:failed_tasks` and publish JSON to `{queue}.dlq`. That is **not** broker DLX configuration.

Day 4 live: record `d4-live-fail-probe` listed from Redis; `property_ai.day4.dlq` messages=1. Replay CLI unit-tested; live replay of a real analyze failure **NOT_RUN**. Do not say “死信投递与恢复已验证”.

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

`deploy/Dockerfile` copies `backend/` only. `.dockerignore` excludes `learning/`, `reports/`, `miniprogram/`, `docs/`. No runtime import of `learning/`.
