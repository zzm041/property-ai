# Independent read-only review: SLA-POLICY-MGMT-010

Review only this current-schema SLA policy administration slice, its direct authorization/concurrency/work-order context, and its committed evidence. Do not broaden into a full-repository audit. Do not edit files, run tests, start or stop processes, operate pages, send HTTP/business requests, inspect `tmp/` private contexts or secrets, or create another reviewer.

## Fixed context

- Overall product: frozen Production V1, not a demo.
- Task ID: `SLA-POLICY-MGMT-010`, initial review round 1 of at most 3.
- Product base: `a38e25087801a171fcc61dfb375ea9827b049fc0`.
- Product implementation commit: `7814d45531a91efb6a63c63df261415fa446a1c4`.
- Main evidence commit: `405de4d4d451e48708bc3faaacb6ee2f9fdb6b40`.
- First-create concurrency evidence / current review HEAD: `39a58f3c9be053ddc5f90ec6f5613f1a32585a5f`.
- Runtime used by the evidence: 8083 -> `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`; isolated RabbitMQ vhost/queue `property_ai_auth001` / `property_ai.auth001`; Redis DB 12; Milvus collection `knowledge_chunks_auth001`; Development providers only.
- Migration 0010 remains unapproved, uncreated, and unexecuted.
- Known unrelated dirty paths include `backend/app/model/ai.py`, `miniprogram/project.config.json`, older report/call artifacts, and the uncommitted `reports/sla-policy-management-010/page-run-error.txt`. Do not attribute those paths to these commits. Operator config SHA-256 remains `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.
- The uncommitted page error is a transparent first screenshot timing failure. The API had already completed its intended page reads, and it did not perform any SLA policy PUT. The successful committed page evidence is a separate run.
- Before the committed first-create concurrency evidence, one baseline GET using an expired normal access token returned 401. It created no SLA object and no result file. The committed script then refreshed that same normal session through `/auth/token/refresh`; it did not use Seed login or `/auth/dev/token`.

## Formal scope

Read only the directly relevant portions of:

- `docs/01-PRD.md`, especially the SLA section
- `docs/02-ROLES-PERMISSIONS.md`, especially SLA permissions and audit requirements
- `docs/03-DOMAIN-MODEL.md`
- `docs/06-API-CONTRACT.md`
- `reports/project-handoff.md`

The current `0009` schema stores one `respond_minutes`, one `complete_minutes`, status, category, priority, and tenant/property scope. It does not store separate accept/respond deadlines, an editable warning lead, escalation recipient, or escalation action. The page must accurately expose the configurable current fields and clearly label fixed existing scheduling behavior; do not demand a new Migration in this current-schema slice. Keep any truly required richer policy/version schema as a release blocker.

## Required code review

1. Inspect `git diff a38e25087801a171fcc61dfb375ea9827b049fc0..7814d45531a91efb6a63c63df261415fa446a1c4` and the focused tests in `backend/tests/unit/test_sla_policy_management.py`.
2. Verify `GET/PUT /admin/sla-policies`, schema validation, stable errors, router registration, and the dedicated `sla:manage` permission.
3. Verify effective resolution is `PROPERTY -> TENANT -> CODE_DEFAULT`, a disabled property override falls back without disappearing from the saved layer, and a tenant default cannot be disabled.
4. Verify role and Scope behavior for PROPERTY_ADMIN, TENANT_ADMIN, resident/staff denial, claimed-tenant mismatch, mixed-role grants, and reload-before-write authorization.
5. Verify parent/row locking and optimistic concurrency for existing updates and first creation. Same-value replay must be a no-op with no duplicate audit; different concurrent updates must not silently overwrite each other.
6. Verify `respond_minutes <= complete_minutes`, fixed warning behavior is safe for short deadlines, SLA change audit contents are scoped and useful, and a policy update is not retroactively applied to an already-created work-order deadline.
7. Verify Home navigation and SLA editor behavior for tenant versus property administrators. A property administrator must not gain a tenant-default editor, and switching scope/key must not accidentally copy a property override into the tenant layer.
8. Do not invent style refactors when the frozen current-schema behavior is satisfied.

## Required evidence review

Read these committed artifacts:

- `reports/sla-policy-management-010/test-results.json`
- `reports/sla-policy-management-010/runtime-environment.json`
- `reports/sla-policy-management-010/rbac-provision-evidence.json`
- `reports/sla-policy-management-010/api-runtime-evidence.json`
- `reports/sla-policy-management-010/database-evidence.json`
- `reports/sla-policy-management-010/page-evidence.json`
- `reports/sla-policy-management-010/page-database-evidence.json`
- `reports/sla-policy-management-010/final-visual-evidence.json`
- `reports/sla-policy-management-010/first-create-concurrency-evidence.json`
- all seven committed evidence/provision scripts in this report directory

Actually open and visually inspect every screenshot with the supported local image viewing mechanism; a path is not visual evidence:

- `reports/sla-policy-management-010/01-tenant-admin-home-entry.png`
- `reports/sla-policy-management-010/02-tenant-default-editor.png`
- `reports/sla-policy-management-010/03-tenant-default-saved.png`
- `reports/sla-policy-management-010/04-property-admin-property-only.png`
- `reports/sla-policy-management-010/05-property-override-active.png`
- `reports/sla-policy-management-010/06-property-disabled-tenant-fallback.png`
- `reports/sla-policy-management-010/07-reload-persisted-fallback.png`
- `reports/sla-policy-management-010/08-staff-no-sla-entry.png`
- `reports/sla-policy-management-010/09-disabled-editor-after-relogin.png`
- `reports/sla-policy-management-010/10-effective-and-saved-layers.png`

Correlate page state, server request rows, screenshots, and read-only database rows for the same current AUTH001 tenant/property and policy objects:

- API policies #1 and #2, including the work-order #6 snapshot and unchanged historical deadlines after policy #2 was updated.
- Page-created tenant policy #3 and property policy #4, including three page PUT 200 responses, matching audits, DISABLED property override, tenant fallback, reload, and normal Development SMS sessions.
- First-create policies #5 and #6: same-body concurrency returned two 200 responses for one policy and one audit; different-body concurrency returned 200 plus `409 SLA.CONFLICT` and one audit.
- Resident/staff and scope-negative API results, the existing-row 200/409 concurrency result, and same-value no-audit behavior.

Main session reports focused `67 passed, 1 warning` and pure unit `528 passed, 1 warning` at the exact product commit. Treat these as reported main-session terminal results; do not rerun. The warning is an existing Starlette/pytest-asyncio deprecation. This reviewer did not operate the pages or runtime.

## Boundary and decision rules

- Development Seed provisioned `sla:manage` only for PROPERTY_ADMIN/TENANT_ADMIN in this isolated environment. Formal production RBAC provisioning remains a release task; the Seed identity was not used as the accepted page/API session.
- Local Development page/API/DB evidence does not establish production scheduler concurrency, Beat timing, production notification Provider/DLQ operations, performance, release, or full Production V1 acceptance.
- `sla_policy_id` currently points to a mutable policy row while the work order keeps applied deadlines. Evaluate this honestly as a version/provenance release gap unless it causes a direct current-slice failure.
- Existing accept/respond/reassign and notification slices remain closed unless this change introduces direct regression evidence.
- `ACCOUNT-MERGE-001` / 0010 is out of this task and remains unapproved.
- PASS if this scoped current-schema SLA administration slice has no current-task blocker. A separately identified release blocker does not force NEEDS_CHANGES for safe local completion.
- Every finding title and rationale must include `CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or `NON_BLOCKING`, plus a stable ID, exact path/location, trigger, impact, and evidence.
- State exactly which screenshots you opened. If image viewing is unavailable or any screenshot was not opened, list that explicitly under `unverified`; do not claim visual review.
- State that you remained read-only and did not rerun tests, operate pages, send requests, or inspect private contexts.
- Direct `next_action` to the main Codex session, never Cursor.
- Do not repeat unrelated approval requests. `approval_requests` should be empty unless this slice itself requires a new user decision.
