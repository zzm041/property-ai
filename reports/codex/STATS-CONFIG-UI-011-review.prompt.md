# Independent read-only review: STATS-CONFIG-UI-011

Review only this current-schema scoped operations-overview slice, its direct authorization/query/UI context, and its committed evidence. Do not broaden into a full-repository audit. Do not edit files, run tests, start or stop processes, operate pages, send HTTP/business requests, inspect `tmp/` private contexts or secrets, or create another reviewer.

## Fixed context

- Overall product: frozen Production V1, not a demo.
- Task ID: `STATS-CONFIG-UI-011`, initial review round 1 of at most 3.
- Product base: `942777e10d2421db34897761f0dfab895b3464fc`.
- Main product commit: `8a64cb356608174ffbb7153dacbb9a3715c13c5d`.
- Follow-up product fixes: `dee53380a89cbca10783fea0f51ca3950c284e31` and `01785a0a76a3c849f7f4d88fd27fbcb3162929dd`.
- Evidence / current review HEAD: `cb055439c96e2937cb3abe5cbbec13bf47c5d788`.
- Runtime used by evidence: 8083 -> `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`; isolated RabbitMQ vhost/queue `property_ai_auth001` / `property_ai.auth001`; Redis DB 12; Milvus collection `knowledge_chunks_auth001`; Development providers only.
- Migration 0010 remains unapproved, uncreated, and unexecuted.
- Known unrelated dirty paths include `backend/app/model/ai.py`, `miniprogram/project.config.json`, and older report/call artifacts. Do not attribute those paths to these commits. Operator config SHA-256 remains `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.
- API PID 42520 was launched at `dee5338`; commits after that launch do not change `backend/app`. Existing isolated Worker PIDs 35540/38268 were not restarted because this read-only overview does not enqueue or consume a task.

## Formal scope

Read only the directly relevant portions of:

- `docs/01-PRD.md`
- `docs/02-ROLES-PERMISSIONS.md`
- `docs/03-DOMAIN-MODEL.md`
- `docs/06-API-CONTRACT.md`
- `docs/10-ACCEPTANCE.md`
- `reports/project-handoff.md`
- the `STATS-CONFIG` row and current-state layer in `reports/mp-current-full-regression/coverage-matrix.json`

The frozen documents do not define a generic statistics period, KPI denominator, or an editable deployment/provider configuration page. This slice is deliberately limited to a current database snapshot plus links to already implemented configuration pages. Do not demand invented 7/30/90-day metrics, employee rankings, new provider/secret editing, or a new Migration in this slice. Keep genuinely missing historical KPI semantics, production configuration, performance, and formal release work as explicit gaps.

## Required code review

1. Inspect `git diff 942777e10d2421db34897761f0dfab895b3464fc..01785a0a76a3c849f7f4d88fd27fbcb3162929dd` and `backend/tests/unit/test_stats_config_overview.py`.
2. Verify `GET /admin/operations-overview`, router/schema/errors, strict `scope_type=PROPERTY|TENANT` input shape, stable error handling, and dedicated `stats:read` authorization.
3. Verify only PROPERTY_ADMIN and TENANT_ADMIN receive the Development permission; SYSTEM/Supervisor/Staff/Resident do not gain it by default. Tenant/property authorization must come from current server-side scopes, not trusted query parameters or UI state. Check mixed-role and cross-tenant behavior.
4. Verify aggregation is fully tenant/property filtered and does not expose work-order text, resident identity, employee identity, secrets, or raw configuration. Check stable zero-filled enum distributions and `property_count`.
5. Verify one generated instant is used for current assignment validity, Property timezone/business date, and staffing rows. An assignment is current only when `valid_from <= generated_at` and `valid_to IS NULL OR valid_to > generated_at`.
6. Verify the page clearly says current snapshot, not period KPI; Tenant Admin can switch property/tenant scopes; Property Admin cannot choose tenant scope; Resident/Staff do not see the entry. The page must reject mismatched responses, suppress out-of-order responses, clear an old property snapshot while switching property scope, and preserve a tenant aggregate while only changing the existing configuration target.
7. Verify the page links to existing staff, SLA, property, and knowledge pages according to their existing permissions. It must not implement a second configuration save path or expose deployment/provider settings.
8. Verify the labels distinguish Incident total from active and label an OPEN SLA clock as running/timing rather than an open Incident.
9. Do not invent style refactors when the bounded implementation satisfies the frozen current-schema behavior.

## Required evidence review

Read every committed artifact under `reports/stats-config-011/`, including both evidence scripts and these JSON files:

- `test-results.json`
- `runtime-environment.json`
- `api-database-evidence.json`
- `page-evidence.json`
- `visual-inspection.json`

Actually open and visually inspect every screenshot with the supported local image viewing mechanism; a path is not visual evidence:

- `reports/stats-config-011/01-tenant-admin-home-entry.png`
- `reports/stats-config-011/02-tenant-property-snapshot.png`
- `reports/stats-config-011/03-tenant-aggregate-snapshot.png`
- `reports/stats-config-011/04-config-links.png`
- `reports/stats-config-011/05-sla-linked-scope.png`
- `reports/stats-config-011/06-property-admin-property-only.png`
- `reports/stats-config-011/07-resident-no-stats-entry.png`
- `reports/stats-config-011/08-staff-no-stats-entry.png`
- `reports/stats-config-011/09-reload-current-snapshot.png`

Correlate page state, API request hashes, screenshots, and the read-only database aggregation for the same current AUTH001 tenant/property:

- tenant 1 / property 1;
- six ASSIGNED/ELECTRICAL/HIGH work orders;
- one OPEN Incident;
- six OPEN SLA clocks, zero warning/overdue;
- one current property assignment and zero current daily status rows;
- Tenant Admin property and aggregate results;
- Property Admin property-only result;
- resident/staff denial/entry exclusion;
- same-scope reload;
- navigation to the existing SLA page with the same tenant/property.

The page used normal Development SMS challenge/send/test-inbox/login controls, not Seed login or `/auth/dev/token`. The test inbox is a local Development mechanism and does not establish real SMS. The stats page and config-link verification sent no business/config writes.

Main session reports focused `11 passed, 1 warning` and pure unit `539 passed, 1 warning` at the exact product code commit. Treat these as reported main-session terminal results; do not rerun. The warning is an existing Starlette/pytest-asyncio deprecation. The first pure-unit attempt encountered only Windows temporary-directory permission errors; the controlled repository-local `--basetemp` rerun produced the recorded clean result.

## Boundary and decision rules

- Development Seed provisioned `stats:read` only for PROPERTY_ADMIN/TENANT_ADMIN in this isolated environment. Formal production RBAC provisioning remains a release task; Seed login was not used for page/API acceptance.
- Local Development page/API/DB evidence does not establish production volume/performance, real providers, real Qwen/bge-m3, real device behavior, release, or full Production V1 acceptance.
- Generic historical KPI definitions, stored report templates, scheduled exports, and deployment/provider configuration remain outside this bounded current-schema closure unless the frozen spec already supplies exact semantics.
- Existing earlier milestones remain closed unless this diff provides direct contrary evidence.
- `ACCOUNT-MERGE-001` / 0010 is out of this task and remains unapproved.
- PASS if this scoped current-schema operations-overview slice has no current-task blocker. A separately identified release blocker does not force NEEDS_CHANGES for safe local completion.
- Every finding title and rationale must include `CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or `NON_BLOCKING`, plus a stable ID, exact path/location, trigger, impact, and evidence.
- State exactly which screenshots you opened. If image viewing is unavailable or any screenshot was not opened, list that explicitly under `unverified`; do not claim visual review.
- State that you remained read-only and did not rerun tests, operate pages, send requests, inspect private contexts, or touch services/databases.
- Direct `next_action` to the main Codex session, never Cursor.
- Do not repeat unrelated approval requests. `approval_requests` should be empty unless this slice itself requires a new user decision.
