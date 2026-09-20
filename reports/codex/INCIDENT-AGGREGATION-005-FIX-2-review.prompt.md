# Independent read-only re-review: INCIDENT-AGGREGATION-005 FIX-2

Review only the two current-task blockers from FIX-1 review, their direct regressions,
and the new saved evidence. Do not broaden into a full-repository audit. Do not edit
files, run tests, start/stop processes, operate pages, send HTTP/business requests,
inspect secrets/private context, or create another reviewer.

## Fixed context

- Task ID: `INCIDENT-AGGREGATION-005`, FIX-2 review round 2 of at most 3.
- Original base: `86257dc2c1fc5e0c3b81eca48c500fa741b6ebea`.
- Initial implementation: `1046f1003847120ceddbbc1666a1aa920ae6ae0e`.
- FIX-1 head: `2b8fa21771c4b45ad269556e0bcfdb630bbe28c4`.
- FIX-2 head: `52f8018614ae91612324db3237f339386dcb16e9`.
- Prior results:
  - `reports/codex/INCIDENT-AGGREGATION-005-review.json`
  - `reports/codex/INCIDENT-AGGREGATION-005-FIX-1-review.json`
- FIX-1 review closed IA005-B1 and IA005-B4. Reopen them only with new evidence.
- Migration 0010 remains unapproved/uncreated/unexecuted. Runtime remains Development
  Fake on 8083 -> `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`.
- Known unrelated dirty paths include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, and old report artifacts. The config SHA-256 remains
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.

## Required focused inspection

1. Diff `2b8fa21771c4b45ad269556e0bcfdb630bbe28c4..52f8018614ae91612324db3237f339386dcb16e9`.
2. IA005-F1-C1: trace MySQL 1205/1213 from `AnalysisService._apply` through transaction
   rollback into `analyze_work_order_task`; verify only those codes get at most two extra
   attempts and permanent/unknown OperationalError behavior is unchanged at the service
   boundary.
3. IA005-F1-C2: verify the active incident assignment query no longer uses SKIP LOCKED,
   so a locked live follower cannot disappear and release the only shared charge. Check
   capacity owner transfer and last-owner release remain correct.
4. Inspect focused tests in:
   - `backend/tests/unit/test_celery_ai_task.py`
   - `backend/tests/unit/test_incident_aggregation.py`
5. Inspect saved evidence itself:
   - `reports/incident-aggregation-005/fix2-concurrency-evidence.json`
   - `reports/incident-aggregation-005/fix2-contention-lifecycle-evidence.json`
   - `reports/incident-aggregation-005/run-fix1-concurrency.py`
   - `reports/incident-aggregation-005/run-fix2-contention-lifecycle.py`

## Evidence boundary

- Main session reports focused 27 passed and full unit 452 passed. Treat as
  main-session evidence; do not rerun.
- A fresh two-worker run created work orders 27-32 through authenticated API calls at
  FIX-2 head. All six completed analysis, converged to incident 3, and received one AUTO
  assignment each without operator replay. No 1205/1213 happened in that fresh run, so
  actual automatic retry remains unit-tested rather than runtime-triggered.
- The contention lifecycle locked follower assignment 35 with `SELECT ... FOR UPDATE`
  but performed no direct state mutation. Authenticated resident confirmation of primary
  order 27 remained blocked until that lock was rolled back. It then promoted active
  follower order 31 without reducing load; the six orders were completed through
  authenticated business APIs, all four original capacity charges released exactly once,
  and incident primary cleared. This is local API/DB evidence, not page evidence.
- Development Fake and local runtime do not prove real Qwen, production providers,
  wall-clock Beat, full UI, or full V1 acceptance.
- The accidentally published task `replay-875fd38782864137a8a77cbdf95370a2`
  remains in shared vhost `/property_ai`, queue `property_ai.auth001`, whose last read-only
  observation showed 7 ready messages and 0 consumers. No shared message was consumed or
  deleted. Exact reconciliation still requires explicit user approval and is an operator
  release blocker, not grounds to conceal the scoped product decision.

## Decision rules

- PASS if the scoped product implementation has no current-task code/business blocker,
  even if separately classified release blockers remain.
- Classify every finding as CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or NON_BLOCKING, with
  ID, exact path/location, trigger, impact, and evidence/rationale.
- State explicitly that the reviewer stayed read-only and did not rerun tests/runtime.
- Direct the next action to main Codex, never Cursor.
