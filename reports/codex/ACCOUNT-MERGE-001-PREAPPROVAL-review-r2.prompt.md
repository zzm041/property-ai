# Same-session read-only re-review

Continue the same independent review session. Do not edit files, run tests, inspect live processes/database, operate pages, send requests, or create another reviewer.

The main session revised only the audit documents after your `NEEDS_CHANGES` result:

- `reports/account-merge-001-preapproval/review.md`
- `reports/mp-current-full-regression/coverage-matrix.json`
- `reports/project-handoff.md`

Re-review only the four prior CURRENT_TASK_BLOCKER findings and any direct regression in those revisions:

1. The report now names the conflict between `docs/03-DOMAIN-MODEL.md:32,173` global-unique wording and 0009 active-only history. It recommends permanently reserving merged-source numbers, keeps ordinary ENDED reuse as an explicit owner decision, and still rejects dropping `uq_user_phones_active_user`.
2. The design now uses a VERIFYING account-merge intent with bound source/target/phone/purpose OTP hash, expiry, attempt limits and one-use transition. A source with no ACTIVE phone is rejected from self-service and recommended for the separate high-risk recovery flow, pending owner approval.
3. `resident_bind_requests.user_id` now stays as historical applicant attribution. Any PENDING request on either account blocks merge until resolved; target housing access comes from migrated relations.
4. Source-password transfer when the target has none is now an explicit owner decision. The recommendation is to discard it and require target-side password setup, never silently transfer.

Also verify the added exact future `down_revision=20260919_0009`, isolated-target guard and evidence-provenance note. The purpose of this re-review is to decide whether the revised audit is now a sound **approval decision basis with explicitly unresolved product decisions**. It is not permission for 0010 and not an account-merge implementation PASS.

Return the same structured schema. Direct the next action to main Codex. State that this is the same read-only session and that you did not rerun tests or business operations.
