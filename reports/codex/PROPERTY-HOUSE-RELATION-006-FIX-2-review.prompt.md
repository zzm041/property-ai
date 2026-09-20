# Independent read-only final review: PROPERTY-HOUSE-RELATION-006 FIX-2

This is the third and final automatic review round. Review only PHR006-B4, its direct
regressions, and the new saved evidence. Do not broaden into a full-repository audit. Do
not edit files, run tests, start/stop processes, operate pages, send HTTP/business
requests, inspect secrets/private context, or create another reviewer. The task-specific
context below supersedes the older `Current task` sentence in the shared preamble.

## Fixed context

- Task ID: `PROPERTY-HOUSE-RELATION-006`, review round 3 of 3.
- Original base: `b603407d385be13be74aa5448b13fc195a54c3f5`.
- Round-1 head: `e19f01bac095f95afbd30dc3eb4d79f272dd8e2c`.
- FIX-1 head: `05219fa8ee5255bc4e1302c3cddbeaaee4a33150`.
- FIX-2 head: `e2bb791791f139918c83cf609a31724c49722289`.
- Read both prior focused results:
  - `reports/codex/PROPERTY-HOUSE-RELATION-006-review.json`
  - `reports/codex/PROPERTY-HOUSE-RELATION-006-FIX-1-review.json`
- B1, B2 and B3 were closed in the prior review and must not be reopened without new
  evidence. PHR006-N1 remains NON_BLOCKING. The shared RabbitMQ replay remains a separate
  RELEASE_BLOCKER.
- Known unrelated dirty paths include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, old reports and raw `.call.json`. Config SHA-256 is
  still `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.
- Runtime remains Development SMS on 8083 ->
  `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`. Migration 0010 remains
  unapproved/uncreated/unexecuted.

## Required focused inspection

1. Diff
   `05219fa8ee5255bc4e1302c3cddbeaaee4a33150..e2bb791791f139918c83cf609a31724c49722289`.
2. Verify `PropertyRepository.get_building_for_update`, `get_unit_for_update`, and the
   analogous `get_house_for_update` use `populate_existing=True` on the locking SELECT so
   identity-mapped rows loaded by the preceding scope lookup are refreshed from the
   locking read.
3. Inspect the added unit assertion in `backend/tests/unit/test_property_admin.py`.
4. Inspect:
   - `scripts/ops/verify_property_house_relation_006_fix2.py`
   - `reports/property-house-relation-006/fix2-concurrency-evidence.json`
   Confirm the harness uses two independent SQLAlchemy Sessions and that both competing
   mutations use `PropertyAdminService`; the callback occurs after A's `Session.get` and
   before A's `SELECT FOR UPDATE`. Read-only SQL is used only after both service
   transactions to compare final values/audits.
5. Verify saved evidence shows, for both building and unit, that A's non-expired return,
   final row, and A audit contain B's newly committed code plus A's name. Do not treat it
   as a general database serializability or production claim.

## Reported verification boundary

- Updated focused file: 12 passed with one existing warning.
- Full backend unit suite: 467 passed with one existing warning.
- Python compile and `git diff --check` passed.
- The 8083 API on FIX-2 was observed healthy and its log names the exact isolated DB,
  queue/vhost and Redis DB. Worker/8082 were not restarted.
- Saved interleaving run is on code SHA `e2bb791791f139918c83cf609a31724c49722289`.

Do not rerun these. The earlier page evidence remains on FIX-1 because FIX-2 changes only
repository refresh semantics and a unit test; no mini-program source changed. Development
evidence does not establish production or full V1 acceptance.

## Decision rules

- PASS if PHR006-B4 is closed and no new current-task blocker exists. Do not invent a
  defect merely to consume the third round.
- Every remaining/new finding must be CURRENT_TASK_BLOCKER, RELEASE_BLOCKER or
  NON_BLOCKING with stable ID, exact path/location, trigger, impact and evidence.
- State that the reviewer stayed read-only and did not rerun tests/runtime.
- Direct next action to main Codex, never Cursor.
