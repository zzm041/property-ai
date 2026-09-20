# Independent read-only review: INCIDENT-AGGREGATION-005

Review only this business slice and its direct regressions. Do not broaden into a
full-repository audit. Do not edit files, run tests, start/stop processes, operate
pages, send HTTP/business requests, inspect secrets/private context, or create another
reviewer.

## Fixed context

- Task ID: `INCIDENT-AGGREGATION-005`, initial review round 0 of at most 3 fixes.
- Overall goal remains frozen enterprise Production V1.
- Approved scope: common-incident aggregation and avoiding one separate capacity charge
  and employee selection for every resident report, using existing 0009 schema.
- Base: `86257dc2c1fc5e0c3b81eca48c500fa741b6ebea`.
- Product commit: `1046f1003847120ceddbbc1666a1aa920ae6ae0e`.
- Migration 0010 remains unapproved, uncreated, and unexecuted. Account-merge semantics
  are unchanged. Port 8082 and shared services remain untouched.
- Known unrelated dirty paths include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, and old report artifacts. Do not attribute them to
  this slice. The operator config SHA-256 remains
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.

## Intended behavior

1. A cluster requires the same tenant/property, building (when present), category,
   completed-analysis subcategory, and configured time window/threshold.
2. A property-row lock serializes incident creation without a new schema or migration.
3. When the threshold is reached, prior cluster members are linked and an already
   assigned member is preferred as the incident primary.
4. Newly linked earlier members still pending assignment are queued only after the
   owning outer transaction commits.
5. New incident members prefer the current eligible primary employee. They may reuse
   that employee at nominal capacity because they share the incident response, but no
   other employee/case bypasses hard capacity, live role/scope, attendance, duty, skill,
   user status, or recent-rejection checks.
6. An incident-follow assignment records AUTO provenance and does not increment or later
   decrement the employee's daily load. A genuine replacement updates the incident
   primary.
7. SQLAlchemy Session commit/rollback events must not schedule or clear outer intents on
   an internal auto-dispatch SAVEPOINT. Existing notification delivery received the same
   outer-commit guard because it is created inside that SAVEPOINT.

## Required inspection

- Diff `86257dc2c1fc5e0c3b81eca48c500fa741b6ebea..1046f1003847120ceddbbc1666a1aa920ae6ae0e`.
- Direct context in:
  - `backend/app/domain/ai.py`
  - `backend/app/repository/ai.py`
  - `backend/app/repository/work_order.py`
  - `backend/app/service/analysis.py`
  - `backend/app/service/incident.py`
  - `backend/app/service/auto_dispatch.py`
  - `backend/app/service/notification.py`
  - `backend/app/service/work_order.py`
  - `backend/app/service/sla.py`
  - `backend/tests/unit/test_incident_aggregation.py`
  - `backend/tests/unit/test_ai_policy.py`
  - `backend/tests/unit/test_auto_dispatch.py`
  - `reports/incident-aggregation-005/runtime-evidence.json`
  - `reports/incident-aggregation-005/run-runtime.py`
  - `reports/incident-aggregation-005/collect-existing-runtime.py`
- Trace the actual transaction boundaries, SAVEPOINT events, cluster query, incident
  reuse window, primary selection/update, capacity accounting, assignment ending/timeout,
  and post-commit redispatch.
- Check tenant/property isolation, eligibility bypass boundaries, idempotency, rollback,
  concurrency/deadlock risks, and compatibility with shared 0007 where reflected 0009
  assignment columns are absent.
- Treat the main-session test report as saved evidence, not reviewer-rerun tests:
  focused 37 passed and full unit 434 passed.
- Inspect the saved runtime evidence itself. It records work orders 15-20 on
  8083 -> 127.0.0.1:3307/property_ai_auth001_test @ 20260919_0009, one incident,
  Development Fake analysis, threshold/follow AUTO assignments, capacity/load proof,
  restored capacity audit, and succeeded development notifications. Do not upgrade it
  to real Qwen, production, wall-clock Beat, or full V1 acceptance.

## Decision rules

- `PASS` only if the scoped aggregation/shared-dispatch implementation has no current-task
  blocker.
- Classify findings as CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or NON_BLOCKING. Every
  finding needs an ID, exact path/location, trigger, impact, and evidence/rationale.
- Broader real concurrency, real self-hosted AI, production providers, account merge,
  Migration 0010, full UI/release validation, and remaining frozen features stay release
  gaps unless this slice newly makes them unsafe.
- State explicitly that the reviewer stayed read-only and did not rerun tests or runtime
  operations.
- Direct the next concrete action to main Codex, never Cursor.
