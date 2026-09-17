# Engineering notes (personal)

These notes explain the **current implementation and what was actually verified on Day 4**.
They are **not** product requirements. If they conflict with `docs/`, `AGENTS.md`, or `.cursor/rules/`, the specs win.

Runtime, seeds, RAG ingest, Docker images, and the miniprogram must not read this directory.

Verification labels: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED_BY_CREDENTIALS.

---

## Transaction boundary vs `_execute` rollback

Files: `backend/app/service/employee.py` (`_execute` ~1146), same pattern in `work_order.py`, `knowledge.py`, `resident.py`.

`Service` owns the transaction (`AGENTS.md`). Several services still do:

```python
if self._session.in_transaction():
    self._session.rollback()
```

before opening their own `begin()`. That discards **caller** uncommitted work on a shared Session. Day 4 **did not** mass-refactor this (frozen architecture). Status: **FAIL** as a known defect, documented in `reports/day4-verification.md`. Capacity tests create work orders **before** check-in so the rollback does not wipe the graph.

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

`/opsz` requires `get_current_principal` plus one of `role:read` / `audit:read` / `work_order:dispatch` / `knowledge:manage` / `employee:manage`. Public 401 on Day 4 Nginx 8082 **PASS**. Prodlike 8081 still 200 on old images — **PARTIAL** leftover.

---

## Queue isolation is vhost + user

Compose: `deploy/docker-compose.day4.yml`. User `day4` permissions **only** on vhost `property_ai_day4`. Worker `-Q property_ai.day4` is necessary but not sufficient. Test: `tests/integration/test_day4_queue_vhost.py` **PASS**.

---

## First-time WeChat user vs `resident_binding:create`

File: `backend/app/service/resident.py` (`_create_bind_request`, `_ensure_resident_role`).

PRD 2.2: a newly authenticated WeChat user has JWT and **no** PROPERTY grants, so bind-create cannot require `resident_binding:create` until they belong to a property. If `current.grants` is non-empty, the permission is still required **on that house’s PROPERTY scope**. Approve inserts PROPERTY `RESIDENT` so the user can create work orders.

Verified: pytest apply-only **PASS**; live 8082 apply+approve+WO **PASS**. Production WeChat **BLOCKED_BY_CREDENTIALS**.

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
