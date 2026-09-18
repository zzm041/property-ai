# Interview Q&A (personal)

Answers cite **this repo’s files** and Day 4 verification status.
Unverified items are marked. This file is **not** a requirement source and must not override `docs/`.

---

### Q. How does RBAC avoid “permission without scope”?

`RoleGrant.permissions` are stored on a grant that already has `OrganizationScope`. `Principal.has_permission(permission, target)` only succeeds when a grant both lists the permission **and** `grant.scope.allows(target)` (`backend/app/domain/iam.py`). Tests: `test_property_scope_allows_only_exact_property` (property 11 cannot read 12), `test_tenant_scope_allows_its_properties_but_not_cross_tenant`. Day 4 regression **PASS**.

---

### Q. Why can a brand-new WeChat user create a bind request without `resident_binding:create`?

Because they are an ACTIVE self-applicant, not because `current.grants` is empty. `ResidentService._create_bind_request` uses `_require_active_actor` (applicant policy) and does not require `resident_binding:create` on the target property (`backend/app/service/resident.py`). A role on property A does not block applying to B. Apply stays PENDING and does not create a house relation or grant. Approve is atomic (request + relation + PROPERTY RESIDENT + audit). Pytest apply **PASS**; live 8082 apply+approve+work order **PASS**. Production WeChat login **BLOCKED_BY_CREDENTIALS**.

---

### Q. If the Worker is down, is the work order lost?

No. Create commits in MySQL first, then enqueues `analyze_work_order_task`. Day 4 E2E: worker stop left WO **163** in `PENDING_ANALYSIS`; after start it reached `PENDING_ASSIGNMENT`. **PASS** on Fake providers. If enqueue throws and Redis is up, `record_failed_task` runs. This-round drill: WO **164** `AI.UNAVAILABLE` and WO **165** `ENQUEUE_FAILED` replayed by **this-round** `record_id` → `PENDING_ASSIGNMENT` **PASS** (Fake). Dual Redis+broker down is **not** recovery PASS (no outbox/sweeper). Celery success ≠ business success.

---

### Q. Is a `.dlq` queue name enough to claim dead-letter recovery?

No. Day 4 publishes JSON to `property_ai.day4.dlq` after recording in Redis. Live: that queue had 1 message after a probe. There is **no** AMQP DLX binding verified. Operator replay is `python -m app.cli.replay_failed_task`. Status **PARTIAL**.

---

### Q. Does `APP_ENV=test` always skip Celery?

No. Pytest sets `PYTEST_CURRENT_TEST` and `enqueue_celery_task` skips the broker. A **live** API with `APP_ENV=test` and `AI_ENQUEUE_ON_CREATE=true` (Day 4 Compose) does `apply_async`. Confirmed by worker logs on vhost `property_ai_day4`. **PASS**.

---

### Q. How is knowledge rebuilt after Milvus loss?

MySQL `KnowledgeDocument` + current version is authoritative (`rebuild_knowledge_index.py`). Day 3 rebuild of 0 documents is **not** data-bearing. Day 4 rebuilt synthetic A/B/staff/archived docs on a disposable collection and re-checked citations/ACL. **PASS**. Live BGE-M3 **BLOCKED_BY_CREDENTIALS**. Development `knowledge_chunks_v1` was not dropped.

---

### Q. Does restoring MySQL restore uploaded photos?

No. Day 4 put unique bytes in MinIO bucket `property-ai-day4-restore`; mysqldump did not contain the marker; SHA-256 after SQL restore still matched `get_object`. **PASS**. DB restore ≠ media restore (`docs/08` data section).

---

### Q. How do you keep a Day 4 Worker from eating development tasks?

Separate RabbitMQ **user + vhost** (`day4` / `property_ai_day4`), queue `property_ai.day4`, Redis DB `/5`, Milvus `knowledge_chunks_day4`, MinIO `property-ai-day4`, MySQL schema `property_ai_day4`. `list_user_permissions day4` showed only that vhost. Queue name / `-Q` alone is not isolation. **PASS**.

---

### Q. Why is readyz `not_checked` instead of `up` for LLM?

`check_readiness` uses `NOT_CHECKED` for unpaid live vendor probes (`backend/app/core/readiness.py`). Day 4 8082: llm/vlm/asr/embedding/reranker/storage = `not_checked`. Calling that `up` would be a false vendor claim. **PASS**. Live Qwen **BLOCKED_BY_CREDENTIALS**.

---

### Q. Work-order create P95 &lt; 500ms — did Day 4 prove 千人并发?

No. Method **A** (client construct + request + close) WO create P95 **593.73ms** (c=1 n=30) and **1099.34ms** (c=5 n=20) on 8082 Fake — **PARTIAL** vs 500ms NFR. A is **not** pure server latency. Methods B (nokeepalive), C (pooled), D (`response.elapsed`) are different experiments and must not replace A. No registered-user-scale ramp. Fake assistant latency is **not** live RAG.

---

### Q. What happens if two open jobs are assigned to the same staff?

Assign uses eligibility including capacity. Second assign raises `WO.EMPLOYEE_NOT_ELIGIBLE` 409 (`test_second_assignment_to_full_employee_is_rejected`) **PASS**. Domain filter reason `CAPACITY_FULL` exists in `app/domain/ai.py`; LLM is not allowed to name the employee.

---

### Q. Is `/opsz` a public health URL?

No. `/opsz` requires SYSTEM-scoped `audit:read` (`RbacService.require_permission` + `OrganizationScope(SYSTEM)`). 8082 unauthenticated **401**, property manager **403**, SYSTEM admin **200**. 8081 prodlike unauthenticated **401** after rebuild. Staging operator 200 is **NOT_RUN** without a real ops login; DevAuth stays off on staging.

---

### Q. Can `learning/` be imported into the property RAG?

Must not. Rebuild reads MySQL documents only. `.dockerignore` excludes `learning/`. No `backend/app` import of `learning/`. Specs remain in `docs/`.

---

### Overclaims to drop from older chat reports

- Day 3 Milvus rebuild of 0 docs ≠ data-bearing recovery.
- TestClient / eager Celery / in-process Service ≠ Day 4 cross-process E2E.
- Fake Development providers ≠ Qwen / ASR / BGE-M3 / reranker / 微信正式登录.
- A `.dlq` string in config ≠ AMQP DLX delivery+replay verified.
- healthz throughput on 8082 ≠ 千人并发达标.
