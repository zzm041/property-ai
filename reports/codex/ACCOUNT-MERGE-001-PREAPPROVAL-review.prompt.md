# Independent read-only review: ACCOUNT-MERGE-001 pre-approval audit

You are the single independent reviewer for this audit. Do not edit files, run tests, operate pages, send HTTP/business requests, inspect secrets/.env, start services, or create another reviewer. Review only the working tree and saved evidence. Return the configured structured schema.

## Fixed scope

- Overall product: frozen Production V1, not a demo.
- Current review task: `ACCOUNT-MERGE-001-PREAPPROVAL-AUDIT`.
- Audited repository HEAD/base: `f5a7b62d83303ab2c742bbd3baef27cf48f99999`.
- The intended audit changes are uncommitted and limited to:
  - `reports/account-merge-001-preapproval/review.md`
  - `reports/account-merge-001-preapproval/unit-results.xml`
  - `reports/account-merge-001-preapproval/isolated-0009-results.xml`
  - `reports/mp-current-full-regression/coverage-matrix.json`
  - `reports/project-handoff.md`
- Preserve and ignore unrelated pre-existing dirty/untracked files, especially `miniprogram/project.config.json`.
- 0010 is not approved. No migration or account merge was implemented or executed.

## Review question

Determine whether the audit provides a sound approval basis, not whether account merge itself passes. Focus only on:

1. Formal-source completeness and contradictions: check `docs/01-PRD.md`, `02-ROLES-PERMISSIONS.md`, `03-DOMAIN-MODEL.md`, `06-API-CONTRACT.md`, `07-SECURITY-PRIVACY.md`, `10-ACCEPTANCE.md` and the cited current code. Verify especially the distinction between historical phone binding, trace, login alias, recovery authority and primary contact.
2. The conclusion about `uq_user_phones_active_user`: verify whether the frozen spec and current scalar repository/service/page assumptions support keeping it, and whether the recommendation safely prevents a retired source phone from auto-creating a new user.
3. The 24 live `users.id` FK columns listed in the report: look for missing/misclassified ownership versus historical-actor semantics. Check the three named non-FK actor columns and actual JWT/session, JSON, worker argument, notification delivery and file-access references. Do not treat the number of FKs as the number of tables.
4. Concurrency/transaction safety: verify that two user-row locks alone are not presented as sufficient, that preview freshness/idempotency/expiry/session revocation are covered, and that MySQL atomicity is not claimed to cover Redis, queues, providers or object storage.
5. Coverage/test evidence provenance: verify that the 83 IDs remain exactly present, historical evidence is not called rerun, current JUnit counts match the files, Fake is not called real AI, and missing Production V1 work stays visible.
6. Migration approval quality: verify revision-chain statements, table/index/status rationale, DDL failure/recovery distinction and safe downgrade limits. Flag any recommendation that still requires an explicit product choice.

Use exact paths and triggers for each finding. Categorize findings as CURRENT_TASK_BLOCKER, RELEASE_BLOCKER or NON_BLOCKING in the title/rationale. A PASS means the pre-approval audit is usable and conservative; it does not approve 0010 and does not pass account merge or Production V1. Do not reopen unrelated closed findings without new evidence.

The next action must be directed to the main Codex session, not Cursor. Approval requests should include only genuine user decisions. Explicitly state that you remained read-only and did not rerun tests or business operations.
