# Independent read-only review: PROPERTY-HOUSE-RELATION-006 FIX-1

Review only the three round-1 blockers, their direct regressions, and the new saved
evidence. Do not broaden into a full-repository audit. Do not edit files, run tests,
start/stop processes, operate pages, send HTTP/business requests, inspect secrets/private
context, or create another reviewer. The task-specific context below supersedes the older
`Current task` sentence in the shared preamble.

## Fixed context

- Task ID: `PROPERTY-HOUSE-RELATION-006`, review round 2 of at most 3.
- Original base: `b603407d385be13be74aa5448b13fc195a54c3f5`.
- Round-1 product head: `e19f01bac095f95afbd30dc3eb4d79f272dd8e2c`.
- FIX-1 product head: `05219fa8ee5255bc4e1302c3cddbeaaee4a33150`.
- Read `reports/codex/PROPERTY-HOUSE-RELATION-006-review.json`; do not reopen other
  issues without new evidence.
- Evidence/scripts remain intentionally uncommitted for review. Ignore unrelated dirty
  paths including `backend/app/model/ai.py`, `miniprogram/project.config.json`, old
  reports, and raw `.call.json`. The protected config hash remains
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.
- Runtime is Development SMS on 8083 ->
  `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`. Migration 0010 remains
  unapproved/uncreated/unexecuted. No table clearing, shared-service operation, real
  provider, or production action occurred.

## Required focused inspection

1. Diff
   `e19f01bac095f95afbd30dc3eb4d79f272dd8e2c..05219fa8ee5255bc4e1302c3cddbeaaee4a33150`.
2. PHR006-B1: verify every CSV row checks cached building and unit names, so a second row
   with the same parent code but a different parent name raises a stable conflict inside
   the same owned transaction. Inspect the two new parameterized failure cases and saved
   zero rollback counts.
3. PHR006-B2: verify building/unit PATCH schemas, routes, service methods, scoped row
   locking, normalized inputs, database uniqueness conflict mapping, constrained audit
   actions, mini-program API wiring, selectors/edit forms, and edit/restore behavior.
   Moving a unit between buildings was not requested; code/name maintenance is the scope.
4. PHR006-B3: verify home entry uses `property:read`, while the archive page continues to
   expose mutation controls only when the selected property has `property:manage`.
   Verify backend writes still reject the read-only SUPERVISOR.
5. Inspect the focused new evidence and harness provenance:
   - `scripts/ops/verify_property_house_relation_006_fix1.py`
   - `reports/property-house-relation-006/fix1-runtime-attempts.json`
   - `reports/property-house-relation-006/fix1-runtime-evidence.json`
   - `reports/property-house-relation-006/run-fix1-page-flow.js`
   - `reports/property-house-relation-006/fix1-page-attempts.json`
   - `reports/property-house-relation-006/fix1-page-evidence.json`
   - `fix1-01-building-edit.png`, `fix1-02-unit-edit.png`, and
     `fix1-03-supervisor-readonly.png` in the same directory. Actually inspect image
     content when tools support it; otherwise list it as unverified.

## Reported main-session verification boundary

- Updated property-admin unit file: 11 passed with one existing warning.
- Full backend unit suite after FIX-1: 466 passed with one existing warning.
- Targeted Python compile, relevant `node --check`, and `git diff --check` passed.
- API process on FIX-1 was observed healthy; its log identifies the exact isolated DB,
  queue/vhost and Redis DB. The pre-existing isolated Worker remained running and was not
  needed for this slice.
- Runtime evidence records official-session non-SYSTEM PROPERTY_ADMIN update/duplicate
  tests; both cached parent-name conflicts returning 409 with zero surviving parent rows;
  update audits; and an official-session synthetic SUPERVISOR prepared by the authorized
  development role fixture reading the archive while PATCH returned 403.
- The first runtime attempt stopped at the fixture transaction guard after earlier API
  checks and left its successful synthetic manager mutations and one extra unit. They were
  not erased. The second used the project owned-transaction wrapper and passed.
- Page evidence records real DevTools controls for building/unit edit then restore and a
  SUPERVISOR entering from home with `canEdit=false`; the only rendered button was
  `刷新当前档案`. Attempt history accurately records one pre-write harness race and one
  insufficient screenshot frame before the final pass.

Do not rerun any of this. Development fixtures/SMS and local runtime do not prove real
providers, production, or full V1 acceptance. The round-1 PHR006-N1 quoted-newline CSV
round-trip edge remains NON_BLOCKING unless this diff provides new severity evidence.

## Decision rules

- PASS if PHR006-B1, PHR006-B2 and PHR006-B3 are closed with no new current-task blocker.
- Every remaining/new finding must be CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or
  NON_BLOCKING with stable ID, path/location, trigger, impact, and evidence.
- Keep the known shared RabbitMQ replay issue separate as a RELEASE_BLOCKER; do not use it
  to block this safe local slice.
- State that the reviewer stayed read-only and did not rerun tests/runtime.
- Direct `next_action` to main Codex, never Cursor.
