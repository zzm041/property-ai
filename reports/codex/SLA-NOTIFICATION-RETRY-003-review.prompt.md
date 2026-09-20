# Independent read-only review: SLA-NOTIFICATION-RETRY-003

Review this focused slice only. Do not edit files, run tests, start processes, operate
pages, send HTTP/business requests, inspect secrets/private context, or create another
reviewer.

## Fixed context

- Overall goal: frozen enterprise property-management Production V1, not a demo.
- Task: `SLA-NOTIFICATION-RETRY-003`.
- Approved scope: implement the SLA scheduling and notification finite-retry/idempotency
  work possible in the existing 0009 schema; repeated scheduling must not create duplicate
  business events or duplicate provider delivery. New migration, dependency change,
  ACCOUNT-MERGE-001 semantics, shared-service rebuild, production release, and 8082 are
  outside this slice.
- Base: `aaf8a94e21c2e27bd3caa1f5cea5c014854ba9a9`.
- Product implementation: `2c2872fa0d7ccdeb8cc980802da14b212d31c8ea`.
- Evidence/current review HEAD: `a5433ff` (resolve full SHA).
- `miniprogram/project.config.json`, `backend/app/model/ai.py`, old report changes, and raw
  Codex call logs are pre-existing/out of scope and were not included.
- Migration 0010 remains unapproved, uncreated, and unexecuted.
- Main-session tests: focused 18 passed; full backend unit 423 passed; three Compose config
  expansions exited 0. Treat these as reported evidence, not reviewer-rerun tests.

## Intended behavior and implementation

1. A periodic SLA task locks due clocks with `FOR UPDATE SKIP LOCKED`. `OPEN` moves once
   to `WARNING`; `WARNING` (or a delayed `OPEN`) moves once to `OVERDUE`. Each transition
   writes a deterministic system WorkOrderLog and in-app notifications in the same owned
   transaction. Current assignee plus active tenant/property managers with
   `work_order:decide` are deduplicated recipients; SYSTEM-only scope is not used as a
   business recipient.
2. Resolved receipt transition to `WAIT_CONFIRM` now stops SLA, and the sweep also stops
   legacy due clocks whose order is `WAIT_CONFIRM` or terminal.
3. Notification delivery claims remain atomic. Explicit timeout/unavailable/rate-limit
   failures stay `PENDING` with bounded backoff for attempts 1-3 and become `FAILED` on
   attempt 4. Uncertain failures keep the existing 120-second lease and also terminate on
   attempt 4. A periodic compensation task republishes only due `PENDING` deliveries.
   Duplicate tasks still pass through the delivery-row claim, so successful delivery is
   not called again.
4. Celery beat schedules SLA and notification compensation tasks onto the environment's
   isolated queue. Default, prodlike, Day4, and AUTH001 launch definitions were added;
   AUTH001 beat was intentionally not started against a database containing historical
   synthetic clocks.
5. Runtime order 11 was created through resident SMS login and the real POST endpoint on
   8083/0009, then auto-assigned by DEVELOPMENT/Fake AI. A controlled scheduler clock was
   scoped to that single new order. One WARNING and one OVERDUE log persisted; repeated
   sweeps produced zero new rows. Three scoped management recipients received two
   notifications each, all SUCCEEDED. The first evidence attempt did not apply the
   isolated broker environment inside the evidence process, so those six rows initially
   remained PENDING; the continued run republished exactly those six existing delivery
   IDs without recreating the work order or SLA events. This is an evidence-harness issue,
   not hidden: evaluate whether the final evidence supports the code claim.
6. The runtime retry probes produced RETRY/RETRY/RETRY/FAILED and
   RETRY/SUCCEEDED/SUCCEEDED. The final duplicate call left attempt=2.

## Required inspection

Read the exact diff from base through review HEAD and inspect direct context:

- `backend/app/core/config.py`
- `backend/app/domain/work_order.py`
- `backend/app/model/work_order.py` SLA/assignment/log definitions
- `backend/app/repository/knowledge.py`
- `backend/app/repository/work_order.py`
- `backend/app/service/notification.py`
- `backend/app/service/sla.py`
- the resolved-receipt branch and `_stop_sla` in `backend/app/service/work_order.py`
- `backend/app/worker/enqueue.py`
- `backend/app/worker/tasks.py`
- `backend/app/worker/celery_app.py`
- the three modified Compose files and `scripts/ops/serve_auth001_isolated_beat.py`
- `backend/tests/unit/test_sla_notification_scheduler.py`
- related changed unit/integration tests
- `reports/sla-notification-retry-003/test-evidence.json`
- `reports/sla-notification-retry-003/runtime-evidence.json`
- `reports/sla-notification-retry-003/run-runtime.py`
- frozen SLA/notification requirements in docs 01, 03, 08, 09, 10, and 11

Check specifically:

- transaction ownership and post-commit scheduling;
- row-lock/idempotency behavior under repeated or concurrent beat tasks;
- whether retry attempt counting, backoff, terminal failure, uncertain outcomes, and
  Celery max_retries agree without an infinite PENDING row or premature terminal state;
- whether compensation scheduling can itself call the provider or duplicate delivery;
- recipient tenant/property scope and privacy;
- task queue isolation and whether standard deployment actually starts beat;
- whether the focused implementation silently overclaims accept/respond/complete SLA,
  escalation, timeout reassign, policy administration, production provider, or wall-clock
  runtime behavior. Classify genuine remaining Production V1 gaps separately from a
  blocker in the implemented completion-deadline/notification slice.
- evidence consistency, including the disclosed first-run enqueue problem and continued
  exact-ID compensation.

## Decision rules

- `PASS` only if no current-task blocker exists in this focused implementation and the
  saved evidence supports the scoped local claim.
- Every finding needs an ID and one of `CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or
  `NON_BLOCKING`, with exact location, trigger, impact, and evidence.
- Do not turn unimplemented accept/respond timeout reassign, SLA policy UI, production
  notification provider, or wall-clock production operation into a false PASS; also do
  not use those wider release gaps to invalidate a correct bounded existing-schema slice
  unless the current code makes an unsafe/false claim.
- State that the reviewer remained read-only and did not rerun tests or operations.
- Direct `next_action` to the main Codex session, never Cursor. If PASS, require updating
  the existing matrix/handoff and proceeding to the next approved independent slice.
- Approval requests must contain only genuine user decisions.
