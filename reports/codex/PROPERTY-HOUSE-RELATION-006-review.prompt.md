# Independent read-only review: PROPERTY-HOUSE-RELATION-006

Review this completed slice and its direct regressions only. Do not broaden into a
full-repository audit. Do not edit files, run tests, start/stop processes, operate pages,
send HTTP or business requests, inspect secrets/private local context, or create another
reviewer. The task-specific context below supersedes the older `Current task` sentence
in the shared preamble.

## Fixed context

- Overall product: frozen Production V1, not a demo.
- Current task ID: `PROPERTY-HOUSE-RELATION-006`, review round 1 of at most 3.
- Approved slice: scoped property/building/unit/house maintenance, atomic CSV import and
  export, and complete resident-house relation history/normal relationship ending using
  the existing 0009 schema. It does not include account merge or Migration 0010.
- Base: `b603407d385be13be74aa5448b13fc195a54c3f5`.
- Product head: `e19f01bac095f95afbd30dc3eb4d79f272dd8e2c`.
- Evidence and its verification scripts are intentionally uncommitted for this review.
- Known unrelated dirty paths include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, older report artifacts, and raw `.call.json` files.
  Do not treat them as this task. The protected config SHA-256 remains
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.
- Runtime boundary: 8083 -> `127.0.0.1:3307/property_ai_auth001_test` at
  `20260919_0009`. Development SMS substitute was used. No migration, table clearing,
  direct SQL business-state change, real provider, paid model, or production action.

## Required inspection

1. Inspect diff
   `b603407d385be13be74aa5448b13fc195a54c3f5..e19f01bac095f95afbd30dc3eb4d79f272dd8e2c`.
   Focus on the new/changed property admin and resident relation files, permissions,
   audit validation, router registration, mini-program page and API methods.
2. Authorization and scope:
   - only the intended tenant/property administrator roles gain `property:manage`;
   - list/read/write operations enforce tenant/property scope and do not trust raw client
     IDs;
   - cross-property and cross-tenant access is denied; SYSTEM is not used as acceptance.
3. Transaction/data behavior using the existing 0009 models:
   - building/unit/house creates and updates preserve hierarchy scope and uniqueness;
   - CSV import is a single service transaction, rejects conflicts without partial
     writes, and identical retry is idempotent;
   - export stays scoped;
   - relationship ending uses the existing state machine/locks, preserves the same
     history row and valid_to, and current resident access no longer includes it;
   - relevant management operations produce constrained audit records.
4. UI/UX and API wiring:
   - normal home entry and role-based visibility;
   - property selector exposes only allowed properties and edits only with
     `property:manage`;
   - create/update/import/export/history/end actions call the actual API and do not fake
     front-end state;
   - identify any concrete operator-blocking UI issue. Do not demand an unrelated
     redesign merely for elegance.
5. Inspect tests and saved evidence:
   - `backend/tests/unit/test_property_admin.py`
   - `scripts/ops/verify_property_house_relation_006.py`
   - `reports/property-house-relation-006/runtime-evidence.json`
   - `reports/property-house-relation-006/run-page-flow.js`
   - `reports/property-house-relation-006/page-flow-attempts.json`
   - `reports/property-house-relation-006/page-evidence.json`
   - the three PNG screenshots in that directory. Actually inspect the image content if
     the available read-only tools support it; otherwise list image content as unverified
     rather than treating a pathname as visual inspection.

## Reported main-session verification boundary

- Focused property tests: 8 passed.
- Focused property/resident tests: 18 passed.
- Full unit suite after the product changes: 463 passed with one existing warning.
- Targeted Python compile, relevant `node --check`, and `git diff --check` passed.
- `ruff` and `mypy` are absent from the frozen environment and were not installed.
- Saved runtime evidence reports official SMS sessions for a non-SYSTEM PROPERTY_ADMIN
  and resident; API-created archive/status roundtrip; atomic import/retry/conflict
  rollback; scoped export; cross-scope 403; bind/approve/current visibility; normal
  relation ending and retained ENDED history; and audit rows.
- Saved DevTools evidence reports actual page controls for home entry, create hierarchy,
  house INACTIVE/ACTIVE roundtrip, CSV import/export, and history loading. The first UI
  attempt failed before property writes because the harness read an older SMS code; the
  second attempt added the correct asynchronous wait and passed.

Treat these as saved/report evidence and inspect provenance; do not rerun them. Development
SMS and local runtime do not prove production providers, real Qwen/RAG, real identity
providers, production deployment, or full V1 acceptance.

## Decision rules

- Return `PASS` only if there is no `CURRENT_TASK_BLOCKER` for this slice.
- A separate `RELEASE_BLOCKER` does not force NEEDS_CHANGES for safe local product code.
- Every finding title/rationale must include one of `CURRENT_TASK_BLOCKER`,
  `RELEASE_BLOCKER`, or `NON_BLOCKING`, with stable ID, exact path/location, trigger,
  impact, and evidence.
- Preserve prior closed results unless this diff provides new contrary evidence.
- State that the reviewer stayed read-only and did not rerun tests/runtime.
- `next_action` is directed to main Codex, never Cursor.
- Keep Migration 0010 and ACCOUNT-MERGE-001 unapproved/unimplemented. Retain the known
  shared RabbitMQ replay issue as a separate release blocker, not a reason to block this
  local slice.
