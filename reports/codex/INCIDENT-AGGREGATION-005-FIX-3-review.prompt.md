# Independent read-only final review: INCIDENT-AGGREGATION-005 FIX-3

Review only IA005-F2-C3, its direct regressions, and the new saved evidence. This is the
third and final automatic fix round for this task. Do not broaden into a full-repository
audit. Do not edit files, run tests, start/stop processes, operate pages, send HTTP or
business requests, inspect secrets/private context, or create another reviewer.

## Fixed context

- Task ID: `INCIDENT-AGGREGATION-005`, FIX-3 review round 3 of 3.
- Original base: `86257dc2c1fc5e0c3b81eca48c500fa741b6ebea`.
- FIX-2 head: `52f8018614ae91612324db3237f339386dcb16e9`.
- FIX-3 head: `e58829a4d52cd94ff8bf62027e2081da7a8ca1bf`.
- Read the prior focused result:
  `reports/codex/INCIDENT-AGGREGATION-005-FIX-2-review.json`.
  All older current-task findings were closed there; do not reopen without new evidence.
- Migration 0010 remains unapproved/uncreated/unexecuted. Runtime is Development Fake
  on 8083 -> `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`.
- Known unrelated dirty paths include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, and old report artifacts. The config SHA-256 remains
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.

## Required focused inspection

1. Diff `52f8018614ae91612324db3237f339386dcb16e9..e58829a4d52cd94ff8bf62027e2081da7a8ca1bf`.
2. Verify `WorkOrderService._execute` maps only MySQL 1205/1213 OperationalError to a
   stable `WO.CONFLICT` HTTP 409 with explicit `retryable=true`, after the owned
   transaction has rolled back. Other OperationalError must remain internal 500 and
   IntegrityError behavior must remain unchanged.
3. Inspect the added focused tests in
   `backend/tests/unit/test_incident_aggregation.py`.
4. Inspect saved runtime evidence and its script:
   - `reports/incident-aggregation-005/fix3-setup-evidence.json`
   - `reports/incident-aggregation-005/fix3-opposing-closures-evidence.json`
   - `reports/incident-aggregation-005/run-fix3-opposing-closures.py`

## Evidence boundary

- Main session reports focused 30 passed and full unit 455 passed. Treat as reported
  main-session evidence; do not rerun.
- Setup created work orders 33-38 through authenticated API and the real isolated Worker;
  all six received completed Development Fake analysis, one incident, and AUTO assignment.
- The final evidence used an external transaction only to hold and roll back incident row
  lock 4; it made no direct business-state mutation. Primary confirmation 33 queued first,
  follower confirmation 38 queued second, and both were blocked before lock release. The
  forced opposing lock cycle produced exactly one HTTP 200 and one HTTP 409 with
  `WO.CONFLICT` and `retryable=true`. Retrying the conflicted authenticated confirmation
  succeeded. Remaining orders closed through authenticated APIs.
- Final state: all six CLOSED, no active assignment, daily load 5 -> 1 matching four
  initial charges, incident primary cleared, exactly one CLOSED log and one confirmation
  row per work order. This is API/DB evidence, not page evidence.
- Development Fake/local runtime do not prove real Qwen, production providers, wall-clock
  Beat, full UI, or full V1 acceptance.
- The accidental task `replay-875fd38782864137a8a77cbdf95370a2` remains unresolved in
  shared vhost `/property_ai`, queue `property_ai.auth001`; last observation was 7 ready,
  0 consumers. No shared message was consumed/deleted. Its targeted reconciliation still
  needs explicit approval and remains a separate release blocker.

## Decision rules

- PASS if this scoped incident aggregation/shared-capacity task has no current-task
  blocker. A clearly separated release blocker does not force NEEDS_CHANGES for local
  product code.
- Every finding must be CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or NON_BLOCKING with ID,
  exact location, trigger, impact, and evidence/rationale.
- State that the reviewer stayed read-only and did not rerun tests/runtime.
- Direct the next action to main Codex, never Cursor.
