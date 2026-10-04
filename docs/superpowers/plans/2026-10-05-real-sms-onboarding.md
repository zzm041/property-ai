# Real SMS Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking. The primary Codex session implements; a separate read-only Codex session reviews the final increment.

**Goal:** Deliver safe real SMS onboarding on isolated 8093, including first password, actual forgotten-password recovery, house admission, durable four-attempt budget, and evidence tied to one user.

**Architecture:** Keep the existing OTP and identity data model. Add a PNVS provider and a Redis AOF-backed staging budget gate, then make OTPs unavailable until an accepted send is precisely activated by challenge ID. Reuse existing mini-program entry points and resident binding workflow.

**Tech Stack:** Python 3.12.10, FastAPI, Pydantic v2, SQLAlchemy 2.x/PyMySQL, Redis Lua/AOF, official `alibabacloud-dypnsapi20170525==2.0.0`, native WeChat Mini Program, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-04-real-sms-onboarding-design.md`

## Global Constraints

- No migration, clearing 3327, pre-creating the test user, AI/model call, publication, or public network expansion.
- Keep `APP_ENV=staging`; select `SMS_PROVIDER=production` only after offline acceptance and live prerequisites.
- Real sends: one locally specified mainland number, at most four attempts and 0.30 CNY; normal scenario uses three attempts. Failures and timeouts consume attempts, unknown results pause the gate.
- Provider sends once with `AutoRetry=0`, SDK RuntimeOptions retry off, finite timeout, no application retry.
- Keep the user-owned dirty worktree, 0015, WO20, and previous acceptance evidence intact.
- Do not print, commit, put in reports, or expose environment values or their hashes.

## Review Focus

- A late success from send A after send B begins must not activate A or invalidate B; `test_late_first_send_cannot_activate_or_cancel_second` in Task 4.
- Redis state disappears after a restart: `test_missing_budget_after_restart_refuses_send` in Task 2.
- The SDK returns HTTP success with a business rejection: `test_business_rejection_does_not_activate_code` in Task 3/4.
- The second password reset must revoke prior access/refresh sessions while retaining the same user: `test_real_flow_password_recovery_same_user_and_revocation` in Task 6.
- A logged in unrelated phone tries to use the bounded provider: `test_unapproved_phone_never_reaches_sdk` in Task 2/3.

---

### Task 1: Build isolation and credential impact map

**Files:** Modify `.dockerignore`, `docs/superpowers/specs/2026-10-04-real-sms-onboarding-design.md`; create scoped redacted evidence under `reports/real-sms-onboarding-20261005/`.

**Interfaces:** Produces verified path and ignore rules; no code consumer.

- [x] Run a read-only Docker bind-mount probe against `deploy/.env.delivery` using the effective root `build.context`; observe that the old rules allowed access.
- [x] Add `deploy/.env.delivery` and `deploy/.env.delivery.*` to the root `.dockerignore`; verify there is no `deploy/Dockerfile.dockerignore` and rerun the probe until the target is `not found`.
- [ ] Compare non-empty sensitive key names with actual Compose service environments and backing stores without outputting values; mark effective, unused, or unknown.
- [ ] Write the minimum coherent rotation order, including each server credential and consumer URL; do not rotate a server and leave consumers on old values.
- [ ] Verify the public report contains no secret, value, hash, full phone, or raw Compose configuration.

### Task 2: Persistent pre-send budget

**Files:** Create `backend/app/provider/sms/budget.py`, `backend/app/cli/arm_sms_budget.py`, `backend/tests/unit/test_sms_budget.py`; modify `backend/app/core/config.py`, `deploy/.env.delivery.example`, `deploy/docker-compose.delivery.yml`.

**Interfaces:** Produces `RedisSmsBudget.reserve(phone_e164: str, request_id: str) -> None`, `RedisSmsBudget.pause() -> None`, and fail-closed staging configuration; Task 3 consumes them.

- [ ] Write tests for atomic concurrent reservations, allowed phone, four-attempt/300-milli-CNY cap at 75 milli-CNY per attempt, failures remaining charged, missing/malformed key, Redis outage, restart persistence, and paused state. Run tests and see the expected failures.
- [ ] Implement a non-expiring fixed run-key Redis Lua reservation. The key is initialized once by the guarded CLI only after live approval and never silently recreated by the request path. Set the delivery Redis AOF to `appendfsync always` before arming.
- [ ] Make `SMS_LIVE_ALLOWED_PHONE_E164` mandatory in staging production SMS mode; require exact normalization and never emit the number in logs/errors.
- [ ] Run the task tests and confirm the budget cannot reset through process restart, concurrent callers, or a new subtask.

### Task 3: PNVS provider and frozen dependencies

**Files:** Create `backend/app/provider/sms/aliyun.py`, `backend/tests/unit/test_aliyun_sms.py`; modify `backend/app/provider/sms/__init__.py`, `backend/app/provider/factory.py`, `backend/app/core/config.py`, `backend/pyproject.toml`, `deploy/requirements.delivery.lock`, `deploy/.env.delivery.example`.

**Interfaces:** Produces `AliyunSmsProvider.send(phone_e164: str, purpose: str, code: str, request_id: str) -> SmsSendResult`; Task 4 uses the extended provider contract.

- [ ] Inspect exact SDK 2.0.0 metadata and transitive requirements. Pin only necessary new distributions; verify existing frozen versions remain unchanged.
- [ ] Write failing tests for three purpose/template mappings, 86 number conversion, JSON `code`/`min`, 300-second TTL, `AutoRetry=0`, SDK retry disabled, finite timeout, response rejection, timeout/unknown pause, no fallback, and no secret leakage.
- [ ] Implement provider with one SDK call and the Task 2 pre-send budget. The API result must indicate acceptance, not final handset delivery.
- [ ] Run targeted provider/config tests and inspect the dependency diff for unrelated upgrades.

### Task 4: OTP pending state and precise activation

**Files:** Modify `backend/app/service/credential_auth.py`, `backend/app/repository/credential.py`, `backend/tests/unit/test_official_auth_security.py`; add a focused MySQL/Redis integration test in `backend/tests/integration/test_isolated_auth_runtime.py`.

**Interfaces:** Consumes Task 3's provider contract. Preserves public `/auth/sms/send` and `/auth/sms/login` APIs.

- [ ] Write failing tests proving pending OTP cannot be consumed before accepted send, on business rejection, timeout, failed activation/compensation, or budget refusal; prove precise ID matching and late-send concurrency.
- [ ] Save a new OTP with non-null `consumed_at`. On accepted send, lock its exact ID and activate only if it remains latest for the phone/purpose and has not expired; otherwise return a stable failure.
- [ ] Ensure no `except` path can re-open or consume a failed/uncertain OTP and all provider failures map to `AUTH.SMS_PROVIDER_DOWN` without Development fallback.
- [ ] Run auth unit and focused integration tests, including explicit failure paths.

### Task 5: Normal mini-program flows

**Files:** Modify `miniprogram/pages/login/login.js`, `miniprogram/pages/password-reset/password-reset.js`, `miniprogram/pages/profile/profile.js` only where needed; add focused native page tests under the existing test layout.

**Interfaces:** Consumes existing API `SmsSendData.provider_mode`; produces honest production/development notices without exposing OTP.

- [ ] Write failing page tests for production acceptance wording, Development substitute wording, countdown only after API success, and reset flow remaining reachable after login.
- [ ] Make the smallest page changes and run the focused page tests.
- [ ] Verify actual page navigation and error state without sending a real SMS.

### Task 6: Offline full-flow and incremental review

**Files:** Extend `backend/tests/integration/test_isolated_auth_runtime.py`; create redacted evidence under `reports/real-sms-onboarding-20261005/`.

**Interfaces:** Produces offline acceptance bundle for separate read-only reviewer.

- [ ] Write and run the failing full-flow test: unseeded phone → SMS login/user creation → initial password → logout/password login → separate forgot-password SMS → same user/new password/old password and required old sessions rejected → bind request/review/legal resident access.
- [ ] Run complete relevant backend/unit/integration and mini-program tests; record exact commands and failures.
- [ ] Inspect diff and runtime configuration for secret, AI, migration, 0015 and WO20 spillover.
- [ ] Dispatch a separate read-only Codex session to review spec coverage, security, implementation, UI, environment, evidence, and severity; fix verified issues and rerun affected tests.

### Task 7: Controlled 8093 staging and real acceptance

**Files:** Only ignored local configuration and redacted evidence; no Migration.

**Interfaces:** Consumes Tasks 1–6 and the owner's phone-ready confirmation.

- [ ] Confirm all SMS variables, current system sign/templates, RAM permission, official pricing, own-number binding, credential rotation completion, scope-limited phone entry, and unchanged `APP_ENV` without printing values.
- [ ] Arm the one-time budget, then switch only `SMS_PROVIDER`, rebuild/recreate the approved 8093 application services, and verify image digest, configuration, `/readyz`, and absence of Development fallback.
- [ ] After explicit phone-ready confirmation, perform at most three normal sends; a fourth is contingency only. Stop further sends on unknown result while continuing read-only diagnosis.
- [ ] Inspect page interaction, API results, one `user_id`, password/session invalidation, house application/review/access, read-only DB, masked screenshots, budget ledger and actual operator billing.
- [ ] Close the limited phone entry and report exact attempt count, cost, unknowns, binding, evidence and independent-review scope. Do not declare Production release acceptance.
