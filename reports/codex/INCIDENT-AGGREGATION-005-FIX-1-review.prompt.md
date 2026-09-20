# Independent read-only re-review: INCIDENT-AGGREGATION-005 FIX-1

Review only the four findings from the initial review, the transient-deadlock recovery
added after real concurrency exposed it, and direct regressions. Do not broaden into a
full-repository audit. Do not edit files, run tests, start/stop processes, operate pages,
send HTTP/business requests, inspect secrets/private context, or create another reviewer.

## Fixed context

- Task ID: `INCIDENT-AGGREGATION-005`, FIX-1 review round 1 of at most 3.
- Base before this slice: `86257dc2c1fc5e0c3b81eca48c500fa741b6ebea`.
- Initial implementation: `1046f1003847120ceddbbc1666a1aa920ae6ae0e`.
- Fix commits: `be59b5377c56a62dd2b46f6efd0b79e3747e7ed1`,
  `3f768839d96ba6656b08e15739cc9366f166434b`, and
  `2b8fa21771c4b45ad269556e0bcfdb630bbe28c4`.
- Initial review result is `reports/codex/INCIDENT-AGGREGATION-005-review.json`.
  Do not reopen a closed finding without new evidence.
- Migration 0010 remains unapproved/uncreated/unexecuted. This run is Development Fake
  on 8083 -> `127.0.0.1:3307/property_ai_auth001_test` at revision `20260919_0009`.
- Known unrelated dirty paths include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, and old report artifacts. Do not attribute them to
  this slice. The operator config SHA-256 remains
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.

## Required focused inspection

1. Diff `1046f1003847120ceddbbc1666a1aa920ae6ae0e..2b8fa21771c4b45ad269556e0bcfdb630bbe28c4`
   and enough base context to judge only the original four findings plus the deadlock
   retry change.
2. IA005-B1: verify `_apply` performs the locking content read before cluster/link reads
   under MySQL REPEATABLE READ; do not confuse this with `_prepare`.
3. IA005-B2/B3: trace explicit `incident_capacity_owner`, transfer/release, manual
   reassignment, timeout, primary promotion/clearing, and active follower behavior.
4. IA005-B4: trace nested SAVEPOINT rollback/commit handling for both incident redispatch
   intents and notification intents, including snapshot restoration.
5. Review `backend/app/worker/tasks.py` for bounded retry of only MySQL 1205/1213, rollback
   before retry, preservation of permanent error behavior, and the existing max two
   additional attempts.
6. Inspect relevant tests in `backend/tests/unit/test_incident_aggregation.py` and
   `backend/tests/unit/test_celery_ai_task.py`.
7. Inspect saved evidence, not just its path:
   - `reports/incident-aggregation-005/runtime-evidence.json`
   - `reports/incident-aggregation-005/fix1-lifecycle-evidence.json`
   - `reports/incident-aggregation-005/fix1-concurrency-evidence.json`
   - their three evidence scripts in the same directory.

## Evidence boundary

- Main session reports final focused 25 passed and complete unit 450 passed. Treat as
  saved/reported main-session evidence; this reviewer must not rerun it.
- Sequential evidence covers work orders 15-20, capacity-at-limit shared follow, and
  development notifications. Lifecycle evidence closes the same six orders and records
  capacity-owner transfer until the last closes.
- The two-worker probe created exactly work orders 21-26 through authenticated API calls.
  It exposed a real MySQL 1213 failure for order 21; the product fix added bounded
  transaction retry. The original isolated failure record was then replayed once through
  the existing operator mechanism, and final saved evidence shows all six analyses
  completed, one incident, six unique links, and six AUTO assignments to employee 7.
- The first operator replay attempt was accidentally published to shared vhost
  `/property_ai`, queue `property_ai.auth001`, task id
  `replay-875fd38782864137a8a77cbdf95370a2`. Read-only `rabbitmqctl` observed that queue
  with 7 ready messages and 0 consumers; no shared message was acknowledged, deleted, or
  consumed. Exact cleanup is pending explicit user approval. Distinguish this operator
  residue from product-code correctness, but classify its operational impact accurately.
- Do not upgrade any evidence to real Qwen, production providers, production runtime,
  wall-clock Beat, full UI, or full V1 acceptance.

## Decision rules

- PASS only if the scoped product implementation has no current-task code/business
  blocker. Classify any operator cleanup or broader deployment gap separately and do not
  conceal it.
- Classify findings as CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or NON_BLOCKING. Every
  finding needs an ID, exact path/location, trigger, impact, and evidence/rationale.
- State explicitly that the reviewer stayed read-only and did not rerun tests/runtime.
- Direct the next action to main Codex, never Cursor.
