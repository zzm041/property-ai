# Independent read-only review: WO-RECEIPT-REASSIGN-002

Review the focused work-order slice only. Do not edit files, run tests, operate pages,
send HTTP/business requests, inspect secrets/private context, or create another reviewer.

## Fixed context

- Overall goal: frozen enterprise property-management Production V1, not a demo.
- Current task: `WO-RECEIPT-REASSIGN-002`.
- Approved scope: complete existing receipt/photo and supervisor-reassign paths using
  the existing 0009 schema and APIs; no Migration, frozen dependency change, account
  merge semantics, shared service rebuild, paid provider, production release, or 8082
  change.
- Base before this slice: `c7f1a793d8c37c93ac7ba2f3a40dd20d10ea567a`.
- Product implementation commit: `75caa5e13535f6d5ed90ad695c2ebec19e0d4373`.
- Evidence commit / current review HEAD: `406358d` (resolve to the full current SHA).
- `miniprogram/project.config.json` is operator-owned pre-existing dirt and was not
  included. `backend/app/model/ai.py` and unrelated old report dirt are out of scope.
- ACCOUNT-MERGE-001 migration 0010 remains unapproved and uncreated/unexecuted.
- Main-session test evidence: targeted 7 passed and full backend unit 418 passed, one
  pre-existing Starlette warning. Do not call these rerun by this reviewer.

## Intended behavior and changes

1. The receipt request now accepts explicit `before_file_ids` and
   `after_file_ids`; legacy `file_ids` remains compatible as AFTER media.
2. Duplicate IDs within a phase are normalized; an ID in both phases is rejected.
3. Both resolved and unresolved receipts attach BEFORE and AFTER media within the
   existing service transaction. Previously the unresolved branch silently discarded
   its `file_ids`.
4. The staff page now keeps ON_SITE feedback photos separate from BEFORE/AFTER receipt
   photos and submits the explicit receipt fields.
5. Existing synthetic order 10 retained its original AUTO assignment to employee 10,
   then actual page actions escalated it; a tenant admin used the supervisor page to
   reassign it to eligible employee 9; employee 9 accepted, started, uploaded two
   persistent images and submitted a resolved receipt. The final state is WAIT_CONFIRM.

## Required inspection

- Inspect only the focused implementation, direct call context, tests, and evidence:
  - `backend/app/schema/work_order.py`
  - `backend/app/api/v1/staff_work_orders.py`
  - receipt/media functions in `backend/app/service/work_order.py`
  - `backend/tests/unit/test_work_order_receipt_media.py`
  - `backend/tests/unit/test_miniprogram_work_order_lifecycle.py`
  - `miniprogram/pages/staff-orders/detail.js`
  - `miniprogram/pages/staff-orders/detail.wxml`
  - `reports/work-order-receipt-reassign-002/page-evidence.json`
  - `reports/work-order-receipt-reassign-002/persistence-evidence.json`
  - the six PNGs in that evidence directory
- Actually inspect the screenshots with a supported image-reading tool. A pathname is
  not visual inspection. State exactly which images were viewed and what each proves.
- Verify the actual request sequence, phase persistence, object existence/hash/size,
  actor/employee consistency, assignment/process history, audit trail, transaction and
  backward-compatibility behavior.
- Review failure paths: cross-phase duplicate, foreign/uploader/scope file checks,
  unresolved receipt media, retry/already-receipted behavior, and regression risk to
  legacy callers.
- Explicitly distinguish:
  - actual page interaction;
  - actual local API/storage/0009 persistence;
  - automator-mocked native `wx.chooseMedia` input;
  - DEVELOPMENT/Fake LLM inherited from the earlier AUTO assignment;
  - unverified real device/native picker, real Qwen, production, and full V1.
- Do not reopen prior closed work without new evidence and do not review the whole repo.

## Decision rules

- `PASS` only if this focused implementation has no current-task blocker and the saved
  evidence supports the scoped claim.
- Findings must include an ID plus `CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or
  `NON_BLOCKING`, exact path/location, trigger, impact, and evidence.
- A real-model or native-picker production gap may remain a release blocker without
  invalidating safe local software-mechanism acceptance.
- State that the reviewer remained read-only and did not rerun tests or business
  operations.
- Direct `next_action` to the main Codex session. If PASS, require updating the existing
  matrix/handoff and then continuing to the next independent approved slice (SLA
  scheduling and notification retry/idempotency), not returning work to Cursor.
- Approval requests must contain only genuine user decisions; do not request approval
  for ordinary report updates or next-slice code work inside existing schema.
