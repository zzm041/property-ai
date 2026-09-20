You are the single independent read-only Codex reviewer for `D:\property-ai`.
Do not edit files, start/stop processes, run tests, operate pages, send HTTP or
business requests, inspect secrets/private local configuration, or create another
reviewer. Review only current source plus saved, sanitized evidence. Return the
configured structured JSON schema.

## Fixed review identity

- Task: `ANNOUNCEMENT-GOVERNANCE-007`
- Approval scope: current 0009 schema only; announcement targeting, complete
  rejection/edit/resubmit/publish flow, manager history, resident/staff visibility,
  page wiring, tests and saved local evidence. No Migration or dependency upgrade.
- Base SHA: `b3181dbdf3722ba904b9e2a85b0e23a0a6a620a2`
- Product commits: `0eb299c325572acea4fdd2e9f8252f82cdcd2cc8` and
  page-state fix `1218bcbe43e15d506adf2db364359e0fe02c1af7`
- Current HEAD must be verified; do not demand rollback if it has advanced for
  task evidence only.
- The known operator file `miniprogram/project.config.json` and pre-existing
  `backend/app/model/ai.py` line-ending-only dirt are unrelated and must not be
  accepted, reverted, or reviewed as task changes.
- 0010/account merge remains unapproved and unrelated.

## Frozen requirements and completion conditions

Read the relevant clauses in `AGENTS.md`, `docs/01-PRD.md`,
`docs/02-ROLES-PERMISSIONS.md`, `docs/03-DOMAIN-MODEL.md`,
`docs/06-API-CONTRACT.md`, and `docs/10-ACCEPTANCE.md`.

Check that:

1. Human review is mandatory; AI never publishes. Draft, submit, reject with a
   required reason, edit after rejection, resubmit and approve are distinct and
   transactionally/audit safe.
2. Tenant/property/building/unit and RESIDENT/OWNER/TENANT/STAFF targets use the
   existing AnnouncementScope model without cross-tenant/property leakage.
   Tenant-wide creation requires tenant scope. Building/unit IDs are validated
   against the anchored property.
3. Resident and staff reads return only PUBLISHED announcements matching active
   house relations or active employee assignments; notifications use the same
   target policy. Ended relations/assignments must not grant visibility.
4. Draft/edit/submit/reject/publish history is append-only and visible only in
   management endpoints; resident responses do not expose review reasons/history.
5. Management UI does not combine submit and approve; role capability is respected;
   list and detail states remain synchronized after transitions.
6. There is no new schema/migration, weakened permission, direct database business
   mutation, production-provider claim, or Production V1 claim.

## Source to inspect

- `backend/app/domain/announcement.py`
- `backend/app/domain/audit.py`
- `backend/app/service/audit.py`
- `backend/app/repository/ai.py`
- `backend/app/repository/audit.py`
- `backend/app/repository/employee.py`
- `backend/app/repository/resident.py`
- `backend/app/service/announcement.py`
- `backend/app/schema/ai.py`
- `backend/app/api/v1/admin_announcements.py`
- `backend/app/api/v1/announcements.py`
- `backend/tests/unit/test_announcement_governance.py`
- `miniprogram/services/api.js`
- `miniprogram/pages/supervisor/announcements.js`
- `miniprogram/pages/supervisor/announcements.wxml`
- `docs/06-API-CONTRACT.md`

Review the actual diff from base through product HEAD, not just the implementer
summary. Inspect direct model/call context as needed, but do not begin a full-repo
audit.

## Saved evidence to inspect

- `reports/announcement-governance-007/runtime-evidence.json` (initial pass;
  environment lacked an outside-unit resident)
- `reports/announcement-governance-007/runtime-fix-evidence.json` (business-API
  preparation of a synthetic outside-unit resident and negative visibility pass)
- `reports/announcement-governance-007/page-attempt1-evidence.json` and the three
  `attempt1-*` screenshots (first page run exposed stale list cards)
- `reports/announcement-governance-007/page-fix-attempts.json`
- `reports/announcement-governance-007/page-evidence.json`
- `reports/announcement-governance-007/01-targeted-draft-form.png`
- `reports/announcement-governance-007/02-rejected-with-history.png`
- `reports/announcement-governance-007/03-published-edit-review-history.png`

Actually open/read the three final PNGs with supported image inspection. A path
alone is not visual evidence. Verify that list/detail status synchronization is
visible after the fix and that screenshots do not expose resident information.

Main-session reported tests: focused `54 passed, 1 warning`; full pure-unit
`473 passed, 1 warning`; Node syntax and `git diff --check` passed. Do not relabel
these as reviewer-rerun evidence. The reviewer must remain read-only and explicitly
state that it did not rerun tests/runtime/page operations.

## Known boundaries

- API/page/DB evidence is local `127.0.0.1:8083` -> exclusive
  `127.0.0.1:3307/property_ai_auth001_test` @ `20260919_0009`, DEVELOPMENT SMS,
  no AI used. 8082 was untouched.
- Actual cross-property tenant-wide visibility was not exercised because the
  selected synthetic tenant has only one property. Tenant scope creation/publish
  is saved, and code/unit evidence exists; decide whether the missing cross-property
  runtime case is CURRENT_TASK_BLOCKER or a concrete NON_BLOCKING evidence gap.
- One automator attempt after the UI fix timed out with no valid result; the bounded
  retry passed and is recorded. Do not treat the timeout as PASS.
- Shared RabbitMQ accidental replay remains an unrelated RELEASE_BLOCKER; do not
  operate or inspect shared messages.
- Real providers, production runtime/release, controlled account merge, real
  self-hosted Qwen/RAG and other frozen V1 gaps remain open.

## Result rules

Use stable finding IDs. Every finding needs category (`CURRENT_TASK_BLOCKER`,
`RELEASE_BLOCKER`, or `NON_BLOCKING`), exact path/location, trigger, impact, and
evidence. PASS only if no current-task blocker remains. Do not force refactors for
style. Preserve the first-run stale-list finding as closed if the final screenshots
and code prove it fixed. Do not claim the whole Production V1 passes.

`next_action` must be a concrete operation for the main Codex session. Approval
requests must contain only genuine user decisions. Explicitly state your read-only
boundary and actual evidence inspected.
