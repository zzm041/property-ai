# Independent read-only review: SLA-ACCEPT-RESPOND-REASSIGN-004

Review this focused slice only. Do not edit files, run tests, start/stop processes,
operate pages, send HTTP/business requests, inspect secrets/private context, or create
another reviewer.

## Fixed context

- Overall goal: frozen enterprise property-management Production V1, not a demo.
- Task: `SLA-ACCEPT-RESPOND-REASSIGN-004`.
- Approved scope: implement 0009-compatible accept/respond SLA phase handling and
  idempotent accept-timeout automatic reassignment. New migrations, dependency changes,
  ACCOUNT-MERGE-001 semantics, shared-service maintenance, production release, and port
  8082 are outside this slice.
- Base: `b7dd955169a08fb1e6bcbb74ce072ea48a9b2468`.
- Product commits: `d649d7dbc99febecd6b92c0eac933ae8a8a467d8` and
  `b952016c6d38b1aa8b84757b058d9b7d91e1674b`.
- Evidence/review HEAD: `c90914f` (resolve full SHA).
- `miniprogram/project.config.json`, `backend/app/model/ai.py`, old report changes, and
  raw Codex call logs are pre-existing/out of scope and were not included.
- Migration 0010 remains unapproved, uncreated, and unexecuted.
- Main-session final tests: focused 46 passed; full backend unit 426 passed. Ruff was not
  installed and was not run; no dependency was installed. Treat these as saved evidence,
  not reviewer-rerun tests.

## Intended behavior and implementation

1. Every manual or AUTO assignment restarts `accept_due_at` from the assignment time
   using the existing active SLA policy's `respond_minutes`. A successful staff accept
   restarts `respond_due_at` from the actual accept time. This uses the existing 0009
   clock and policy; no new schema is introduced.
2. A periodic scheduler locks due work orders with `FOR UPDATE SKIP LOCKED`. If an
   `ASSIGNED` order is past `accept_due_at`, it locks the active assignment, marks it
   `REASSIGNED` with `timeout_at`, releases one unit of current load, transitions the
   work order to `PENDING_ASSIGNMENT`, writes deterministic WorkOrderLog and AuditLog,
   notifies scoped recipients in the same owned transaction, and enqueues analysis only
   after commit. Existing completed analysis is reused by the Worker to invoke the rule
   dispatch path; the timed-out employee is excluded for the existing configurable
   cooldown. A repeated sweep must not repeat release, event, notification, or enqueue.
3. If an `ACCEPTED` order is past `respond_due_at`, it stays assigned and accepted. The
   scheduler writes one `SLA_RESPONSE_TIMEOUT` event and notifies scoped managers plus
   assignee for supervisor review. It does not automatically seize an accepted order,
   because the frozen state machine only grants `ACCEPTED -> PENDING_ASSIGNMENT` to a
   supervisor reassign. Idempotency is scoped to the current assignment ID so a later
   assignment cycle may produce its own legitimate timeout event.
4. The existing beat task invokes both completion-deadline and assignment-phase sweeps.
   Beat was intentionally not started against the historical synthetic database.
5. Runtime order 13 was created through resident SMS login and POST `/work-orders` on
   8083/0009. Two already claimed/on-duty synthetic employees had capacity increased
   through PATCH `/admin/employees/{id}`. Development/Fake analysis AUTO-assigned
   assignment 15 to employee 7. A controlled scheduler clock scoped only to order 13
   timed it out; duplicate sweep was empty; the isolated Worker reused stored analysis
   and created AUTO assignment 16 for employee 10. Employee 10 accepted with their own
   official session. The response sweep produced one assignment-scoped timeout event;
   its duplicate produced no event/notification. Final order remained ACCEPTED with
   assignment 16 active. Eight scoped development-notification deliveries succeeded.
6. An earlier pre-fix run on synthetic order 12 exposed clock-scoped response idempotency;
   it was not accepted as final evidence. The key was corrected to assignment scope,
   unit suites reran, services restarted, and final order 13 evidence was captured on
   product commit b952016.

## Required inspection

Read the exact diff from base through review HEAD and direct context:

- `backend/app/domain/work_order.py`
- `backend/app/model/work_order.py` work order, assignment, log, SLA clock definitions
- `backend/app/repository/work_order.py`
- `backend/app/repository/employee.py` daily-status lock
- `backend/app/service/sla.py`
- assignment, accept, reject, load adjustment in `backend/app/service/work_order.py`
- candidate cooldown and assignment commit in `backend/app/service/auto_dispatch.py`
- existing-analysis redispatch in `backend/app/service/analysis.py`
- notification after-commit scheduling in `backend/app/service/notification.py`
- `backend/app/worker/tasks.py` and `backend/app/worker/celery_app.py`
- `backend/tests/unit/test_sla_notification_scheduler.py`
- `backend/tests/unit/test_auto_reassign_exception.py`
- `reports/sla-accept-respond-reassign-004/test-evidence.json`
- `reports/sla-accept-respond-reassign-004/runtime-evidence.json`
- `reports/sla-accept-respond-reassign-004/run-runtime.py`
- frozen state/SLA/dispatch requirements in docs 01, 03, 04, 05, 08, 10, 11

Check specifically:

- work-order/clock/assignment/daily lock order and concurrency/idempotency;
- load release exactly once and whether immediate reselection is prevented;
- post-commit enqueue behavior and reuse of stored completed analysis;
- deadline restart semantics for initial assignment, reassignment, and acceptance;
- response-timeout behavior over multiple assignment cycles;
- recipient tenant/property scope and privacy;
- whether state and audit logs truthfully describe automatic timeout reassign;
- whether the combined periodic task leaves any compatibility or infinite-repeat risk;
- evidence consistency, including controlled future clocks and the disclosed superseded
  order 12 run;
- whether the focused result overclaims wall-clock Beat, real providers, live concurrent
  schedulers, production operation, broader SLA policy administration, or V1 completion.

## Decision rules

- `PASS` only if no current-task blocker exists and the saved evidence supports the
  scoped local result.
- Every finding needs an ID and one of `CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or
  `NON_BLOCKING`, with exact location, trigger, impact, and evidence.
- Broader policy UI, production providers, live Beat/concurrency, real Qwen/RAG, account
  merge and release validation remain separate release gaps; do not invalidate a correct
  existing-schema slice solely because those are still open.
- State that the reviewer remained read-only and did not rerun tests or operations.
- Direct `next_action` to the main Codex session, never Cursor.
- Approval requests must contain only genuine user decisions.
