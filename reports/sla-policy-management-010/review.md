# SLA-POLICY-MGMT-010 evidence summary

This is a scoped current-schema local Development result, not Production V1 or release acceptance.

## Fixed versions and environment

- Product base: `a38e25087801a171fcc61dfb375ea9827b049fc0`.
- Product implementation: `7814d45531a91efb6a63c63df261415fa446a1c4`.
- Main evidence commit: `405de4d4d451e48708bc3faaacb6ee2f9fdb6b40`.
- First-create concurrency evidence HEAD: `39a58f3c9be053ddc5f90ec6f5613f1a32585a5f`.
- Database: `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`.
- Messaging: RabbitMQ vhost `property_ai_auth001`, queue `property_ai.auth001`; the shared vhost was not read, consumed, replayed, or cleared.
- API/Worker: 8083 and the isolated AUTH001 Worker loaded the same product source through the recovered CPython 3.12.10 launch path.

`miniprogram/project.config.json` remained unstaged and unchanged at SHA-256 `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. Migration 0010 was not created or executed.

## Implemented user capability

- Tenant and property administrators can open SLA management from Home and see only their authorized properties.
- Tenant administrators can maintain tenant defaults; property administrators can maintain only property overrides and cannot switch into a tenant-default editor.
- Effective policy resolution is property override, then tenant default, then code default. A disabled property override remains visible in the saved layer while effective resolution falls back to the tenant policy.
- The API validates `respond_minutes <= complete_minutes`, prohibits a disabled tenant default, uses a dedicated `sla:manage` permission, reloads authorization before writes, and writes `SLA_POLICY_CHANGED` audits only for actual changes.
- Existing-row optimistic updates and serialized first creation reject conflicting values without silent overwrite. Same-value replay is idempotent and does not duplicate the audit.
- A newly created work order snapshots its effective deadlines. Later edits do not retroactively change those stored deadlines.
- The page accurately labels the current 0009 limitation: one shared accept/respond threshold plus fixed warning and escalation behavior.

## Tests and runtime evidence

At the exact product commit, `test-results.json` records:

- focused SLA policy tests: `67 passed, 1 warning`;
- pure unit regression: `528 passed, 1 warning`.

The warning is the existing Starlette/pytest-asyncio deprecation. Product source did not change after these runs, so the suites were not repeated for the evidence-only commits.

Normal Development SMS sessions, not Seed login or `/auth/dev/token`, supplied the accepted roles. Runtime evidence covers resident/staff 403 results, tenant/property Scope negatives, invalid inputs, existing-row `200/409 SLA.CONFLICT`, and a same-value replay with no audit. Work order 6 used policy 2 and retained its original deadline snapshot after that policy row changed.

Developer Tools page evidence covers tenant policy 3, property policy 4, property disable and tenant fallback, reload/relogin persistence, and absence of the SLA entry for staff. Each of the three page writes maps to one API 200 response and one persisted audit. All ten committed screenshots were opened by the main session and again by the independent reviewer.

First-create evidence uses policies 5 and 6 under the same normal property-admin session. Identical concurrent creates returned `200/200`, the same policy ID, and one audit. Different concurrent creates returned `200/409 SLA.CONFLICT` and one audit. Database verification was read-only.

An initial automator screenshot attempt timed out after page reads and did not issue an SLA PUT. A later expired-access baseline GET returned 401 before the first-create run and created no SLA object. The successful run refreshed the same normal session and did not repeat a business write.

## Independent review

`reports/codex/SLA-POLICY-MGMT-010-review.json` is round-1 `PASS` with no `CURRENT_TASK_BLOCKER`. The reviewer remained read-only, opened all ten screenshots, and did not rerun tests, operate pages, send requests, or inspect private contexts.

- `SLA010-N1`: the prompt said seven scripts, but the committed directory contains six. All six were reviewed; this report records the correct count.
- `SLA010-N2`: the living handoff was stale at the reviewed HEAD; the post-review handoff/matrix update closes that documentation issue.
- `SLA010-N3`: screenshots 07 and 10 do not each prove their full filename claim in one crop. Screenshots 06/09 plus page and database evidence supply the same-object fallback/reload proof.
- `SLA010-R1`: separate accept/respond fields, editable warning/escalation rules, and immutable applied-policy provenance require richer schema/product work and remain a release blocker.
- `SLA010-R2`: production RBAC, scheduler/Beat concurrency, notification Provider/DLQ, performance, and release acceptance remain unverified.

## Evidence inventory

- Ten screenshots, `01-tenant-admin-home-entry.png` through `10-effective-and-saved-layers.png`.
- Eight committed public JSON evidence files, including test, runtime, RBAC, API, page, database, final visual, and first-create concurrency results.
- Six committed evidence/provision scripts: `provision-rbac.py`, `run-api-evidence.py`, `run-page-evidence.js`, `inspect-db-evidence.py`, `inspect-page-db-evidence.py`, and `run-first-create-concurrency.py`.

No real Qwen/bge-m3 result, Production V1 acceptance, account merge, or Migration 0010 is claimed.
