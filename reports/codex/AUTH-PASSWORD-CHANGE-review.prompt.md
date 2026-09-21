You are the single independent read-only Codex reviewer for `D:\property-ai`.
Do not edit files, start or stop processes, run tests, operate the mini-program,
send HTTP/business requests, inspect private credential files under `tmp/`, or
create another reviewer. Return the configured structured JSON schema.

## Fixed review identity

- Task: `AUTH-PASSWORD-CHANGE`, bounded review round 1 of at most 3.
- Overall goal: complete the frozen enterprise property Production V1 without
  treating local Development evidence as production release acceptance.
- Baseline Git HEAD: `63ad2d54b9341b280a066ce539c22dbb102159b0`.
- Review the current scoped working-tree implementation and saved evidence; it is
  intentionally not committed until this review passes.
- Approved environment: local 8083 ->
  `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`, Development
  providers, existing isolated infrastructure. No migration or dependency change.
- `ACCOUNT-MERGE-001` / migration 0010 remains unapproved and is unrelated.

Exclude unrelated pre-existing dirt/noise from task findings:

- `backend/app/model/ai.py`
- `miniprogram/project.config.json` (operator-owned; protected hash
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`)
- old `reports/codex/AUTH-IDENTITY-AI-001-login-review*` changes and unrelated
  untracked historical reports.

## Frozen behavior to verify

An authenticated user can open the normal `pages/profile/profile` account and
security page and change their own password using current password, new password,
and confirmation. New-password policy remains 8-128 Unicode code points, does not
require uppercase, rejects pure ASCII digits and pure ASCII lowercase, and hashes
the exact input without case-folding. Do not invent a same-as-old prohibition.

Success must serialize per user, update the credential, revoke all sessions that
existed before the change, create one replacement password session for the caller,
write audit evidence, update the page's access/refresh tokens, clear the three
fields, and preserve the current device login. Wrong current password, mismatch,
weak value, rapid repeat submit, and concurrent change must fail safely. A wrong
current-password 401 from this credential endpoint must not enter the generic
refresh/relaunch behavior. The existing password-reset flow must keep a compatible
lock order; do not expand this task into reset-ticket policy changes.

## Source scope

Inspect the exact current diff and direct context in:

- `backend/app/schema/auth.py`
- `backend/app/api/v1/credential_auth.py`
- `backend/app/repository/credential.py`
- `backend/app/service/credential_auth.py`
- `miniprogram/services/api.js`
- `miniprogram/pages/profile/profile.js`
- `miniprogram/pages/profile/profile.wxml`
- `miniprogram/utils/password.js` and `miniprogram/utils/ui.js` only as direct context
- `backend/tests/unit/test_password_change.py`

Check authorization/ownership, transaction and row-lock semantics, stale ORM state,
lock ordering with reset, session revocation/replacement, audit linkage, stable
error codes, exact password case, UI loading/repeat behavior, and response handling.

## Saved runtime/page evidence

Actually read these files:

- `reports/auth-password-change/fixture-evidence.json`
- `reports/auth-password-change/runtime-api-db-evidence.json`
- `reports/auth-password-change/page-evidence.json`
- `reports/auth-password-change/page-database-evidence.json`
- `reports/auth-password-change/test-results.json`
- `reports/auth-password-change/page-run-error.txt` only to confirm the documented
  one-time navigation acknowledgement timeout and bounded resume.

Actually open all three PNGs with supported image inspection, not merely their
paths:

- `reports/auth-password-change/01-password-change-form.png`
- `reports/auth-password-change/02-password-change-success-cleared.png`
- `reports/auth-password-change/03-new-password-relogin-home.png`

The second screenshot visibly contains the success notice but its viewport is below
the password inputs. The same execution's captured page state records all three
fields empty. Judge the combined evidence accurately; do not claim the screenshot
alone shows the fields, and do not require another state-changing password rotation
solely to improve framing unless a genuine acceptance gap remains.

Main-session checks already run; do not rerun them:

- focused password unit tests: `9 passed`;
- pure unit controlled rerun: `499 passed` (the first attempt had 497 passed and
  two tmp-path setup permission errors before test logic; using a fresh workspace
  `--basetemp` produced the clean result);
- Node syntax and scoped `git diff --check`: passed;
- actual WeChat DevTools page controls: local empty/mismatch/weak branches sent
  zero change requests; wrong current sent one 401; rapid double tap sent one 200;
  replacement tokens were stored; old access/refresh and old password failed; new
  lowercase+digits/symbol password returned the same synthetic user;
- read-only DB evidence ties the page request hash to one password-change audit and
  three `PASSWORD_CHANGE` session-revoke audits at revision 0009.

Verify that public evidence contains no phone, password, token, OTP, password hash,
raw request ID, private resident information, or production claim. Synthetic marker
and synthetic numeric object IDs are allowed. Do not inspect ignored private context.

## Result rules

Use stable finding IDs. Prefix each title with one of
`CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or `NON_BLOCKING`; include exact path,
trigger, impact, and evidence. PASS only if the current slice has no real blocker.
Do not reopen unrelated closed findings without contrary evidence.

Real SMS/WeChat, production security/operations, full Production V1, real Qwen,
real bge-m3/reranker, and account merge remain release/project gaps but do not block
this scoped local slice. Remaining current-schema sequence after PASS is incident
management page, SLA policy management, then stats/config; the main session—not
Cursor—implements it.

State your actual read-only boundary. `next_action` on PASS should be one concrete
main-Codex action: update the existing matrix/handoff with this bounded result,
commit only scoped source/tests/redacted evidence, then begin the incident-management
slice. Approval requests must contain only genuine user decisions.
