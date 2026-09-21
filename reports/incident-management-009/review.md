# INCIDENT-MGMT-UI-009 evidence summary

This is a scoped current-schema Development result, not Production V1, real Qwen, or release acceptance.

## Fixed scope and versions

- Task: `INCIDENT-MGMT-UI-009`.
- Product base: `9ddec601517a3d5c0a86c2b7fff897092cf0a7a0`.
- Product implementation: `9033caae493f120789f07f919d69666afc33625b`.
- Runtime database: `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`.
- Runtime messaging: vhost `property_ai_auth001`, queue `property_ai.auth001`; the existing DLQ count stayed at 2 and no DLQ message was read, replayed, consumed, or cleared.
- API/Worker source: the same `9033caa` checkout through `tmp/run_auth001_recovered.py`; API log `tmp/auth001-runtime/api-9033caa-incident.stdout.log`, Worker log `tmp/auth001-runtime/worker-9033caa-incident.stdout.log`.
- Provider: Development Fake AI. The result does not claim real Qwen or production AI quality.

The preserved operator file `miniprogram/project.config.json` remained unstaged and unchanged at SHA-256 `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. The unrelated pre-existing `backend/app/model/ai.py` working-tree change was not staged or modified.

## Implemented user capability

- A manager enters Incident management from the normal Home page, selects only an authorized property, filters/paginates the Incident list, opens the same Incident detail, generates or refreshes a Development AI operations summary, and creates an announcement that remains a human-review `DRAFT`.
- A staff user enters the same page from the normal Home page as “my participated Incidents,” sees only an Incident with a current active assignment to their own Employee identity, and receives no summary or announcement management controls.
- The backend applies the participant filter in SQL before the limit, rechecks authorization after the summary Provider call, detects stale Incident state before writeback, and surfaces Provider/invalid-output failures instead of returning a false success.
- Work-order detail now exposes `incident_id`, allowing an assigned staff member to navigate from their own task to the linked Incident.

No Incident transition API was invented. The frozen API contract defines list, detail, and summary but no Incident transition graph or update request. Formal status-disposition semantics remain a separate product/API policy gap rather than being silently guessed in this slice.

## Tests and runtime chain

`test-results.json` records the already completed terminal runs on the exact product commit: focused direct regression `30 passed, 1 warning`, and pure unit `511 passed, 1 warning`. They were not rerun after page evidence because product source did not change.

Before generating business objects, the main session independently confirmed:

- target database and revision, with zero pre-existing WorkOrders and Incidents;
- runtime `incident_threshold=5`, window 10 minutes, `app_env=test`;
- ready API on 8083;
- isolated queue ready/unacknowledged `0/0`, consumer `1`;
- no interaction with the shared vhost or 8082.

`run-api-evidence.py` then used official Development-SMS sessions and normal APIs to bind the resident if needed, configure the existing synthetic employee, check in, and create five uniquely marked repair requests. It refuses to rerun after a private state file exists. The resulting objects are tied by `run_id=incident-mgmt-d6605d3f5e`: WorkOrders 1–5, Incident 1, Employee 1.

The helper was hardened after its successful one-time run with an internal AUTH001 target guard and fail-closed current-assignment checks. The executed run was already protected by the external target/database/queue checks above; `database-evidence.json` supplies the missing authoritative assignment provenance.

The page run used actual WeChat DevTools controls at `ws://127.0.0.1:9437`:

- Manager and staff each logged in through the normal SMS login page; no Seed or DevAuth token was used.
- The manager clicked the Home Incident entry, the Incident card, the AI-summary button, and the announcement-draft button.
- The staff logged in separately, clicked the Home participant Incident entry and the same Incident card.
- `page-evidence.json` binds page state to server request completion rows using hashed request IDs.
- All six screenshots were opened and visually inspected by the main session, not merely listed by path.

One initial page attempt used an obsolete manager password and received one `AUTH.INVALID_PASSWORD` before reaching Home. It created no Incident summary, announcement, or other business object. The retained local `page-run-error.txt` records only the automation stack; the successful evidence uses the official SMS page and does not treat the rejected login as a product defect.

## Same-object persistence evidence

`database-evidence.json` is a guarded read-only query result and confirms:

- Incident 1 has exactly WorkOrders 1–5, category `ELECTRICAL`, subcategory `outlet_spark`, primary WorkOrder 1, and primary Employee 1.
- All five WorkOrders are `ASSIGNED`; every current assignment is active, belongs to Employee 1, has `assigned_by_user_id=null`, and has `assigned_source=AUTO`. Four reasons are `AUTO_DISPATCH`; the threshold follower is `AUTO_INCIDENT_FOLLOW`.
- Each WorkOrder has a completed Development analysis from `dev-llm-v1`; this is software-chain evidence only.
- The only current participant user is the synthetic staff user used by the staff page.
- The summary is persisted and audited as `INCIDENT_UPDATED` by the manager identity.
- Announcement 1 points to Incident 1, remains `DRAFT`, has `ai_draft=1`, has zero review rows and no reviewer, publisher, publication time, or auto-publication. Its creation audit matches the page request hash.

## Evidence files

- `api-runtime-evidence.json`
- `page-evidence.json`
- `database-evidence.json`
- `test-results.json`
- `01-manager-incident-list.png` through `06-staff-participant-detail.png`
- `run-api-evidence.py`, `run-page-evidence.js`, and `inspect-db-evidence.py`

## Remaining boundaries

- Real Qwen/VLM behavior, production RAG, real providers, production Worker supervision, release configuration, performance, and formal release remain unverified.
- Incident status disposition lacks a frozen transition/API contract and remains visible as a product/API gap.
- `ACCOUNT-MERGE-001` / Migration 0010 remains unapproved, uncreated, and unexecuted.
- The shared vhost historical messages were not inspected or changed.
