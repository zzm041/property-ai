# Independent read-only re-review: SLA-ACCEPT-RESPOND-REASSIGN-004 FIX-1

Re-review only the previously reported blocker and direct regressions. Do not broaden
into a new full-repository audit. Do not edit files, run tests, start/stop processes,
operate pages, send HTTP/business requests, inspect secrets/private context, or create
another reviewer.

## Fixed context

- Task remains `SLA-ACCEPT-RESPOND-REASSIGN-004`; this is automatic fix round 1 of 3.
- Overall goal remains frozen enterprise Production V1.
- Original base: `b7dd955169a08fb1e6bcbb74ce072ea48a9b2468`.
- Previously reviewed HEAD: `c90914ffecf4110e6fbabb4091b44c79af5dfc3e`.
- Read the first result at
  `reports/codex/SLA-ACCEPT-RESPOND-REASSIGN-004-review.json`.
- Fix product commit: `9ab106b29b9761504fa229cc1bd97227daa6e6c9`.
- Current evidence/review HEAD: `55fe0a9` (resolve full SHA).
- Product behavior outside the blocker is unchanged. Migration 0010 remains unapproved,
  uncreated, and unexecuted. Port 8082 and shared services remain untouched.

## Prior blocker

`SLA-004-B1 CURRENT_TASK_BLOCKER`: handled `ACCEPTED` orders remained due and could
repeatedly consume the fixed first-100 query batch, starving later phase timeouts.

## Fix and evidence

1. The due-order query now joins only the current active Assignment and excludes rows
   with `assignment.timeout_at IS NOT NULL` before ordering and `LIMIT`.
2. For legacy rows produced before that marker existed, the ACCEPTED branch also uses a
   correlated assignment-scoped `NOT EXISTS` on deterministic response-timeout log
   request ID before `LIMIT`. Therefore already handled legacy cycles do not need a
   cleanup/backfill pass before later due rows become visible.
3. A new response timeout sets the active Assignment's `timeout_at` in the same owned
   transaction as its WorkOrderLog and notifications. The assignment stays ACTIVE and
   the order stays ACCEPTED so supervisor review/reassign remains possible.
4. Ordering is by the relevant phase deadline and work-order ID.
5. Unit coverage compiles the real repository statement with the MySQL dialect and
   asserts current-assignment join, active/unended/unhandled predicates, both accept
   branches, correlated NOT EXISTS, and `LIMIT 100 FOR UPDATE SKIP LOCKED`.
6. Final main-session results: focused 47 passed; full unit 427 passed. Treat as reported
   saved evidence, not reviewer-rerun tests.
7. Services were restarted on 9ab106b. New synthetic runtime order 14 on 8083/0009 again
   proved accept timeout, load release, AUTO reassignment to a different employee,
   official staff acceptance, and one response timeout. Unlike the pre-fix evidence,
   both duplicate accept and duplicate response sweeps now report `scanned=0`. Active
   assignment 18 remains ACTIVE/ACCEPTED and has response `timeout_at`. Eight scoped
   development notifications succeeded. Beat and production providers were not tested.

## Required inspection

- Diff `c90914ffecf4110e6fbabb4091b44c79af5dfc3e..55fe0a9`.
- Direct context in:
  - `backend/app/repository/work_order.py`
  - `backend/app/service/sla.py`
  - `backend/tests/unit/test_sla_notification_scheduler.py`
  - `reports/sla-accept-respond-reassign-004/test-evidence.json`
  - `reports/sla-accept-respond-reassign-004/runtime-evidence.json`
  - first review JSON
- Confirm the SQL predicate is correlated to the current assignment cycle and is applied
  before `LIMIT`; verify no malformed/missing assignment can be timed out.
- Confirm setting `timeout_at` on an ACTIVE accepted assignment does not break current
  FSM, manual supervisor reassignment, load accounting, auto-dispatch cooldown, or active
  assignment uniqueness.
- Confirm duplicate scheduling and legacy handled rows no longer consume the batch.
- Check only direct regressions introduced by FIX-1. Keep real concurrency, wall-clock
  Beat, real providers, policy administration, account merge, real AI/RAG and release
  validation classified as broader evidence/release gaps rather than false current-task
  blockers unless this fix makes them unsafe.

## Decision rules

- `PASS` only if `SLA-004-B1` is closed and no direct current-task regression exists.
- Every finding needs ID, classification, exact location, trigger, impact, and evidence.
- State that the reviewer stayed read-only and did not rerun tests or operations.
- Direct next action to main Codex, never Cursor.
