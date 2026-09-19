Review `AUTO-DISPATCH-MAINLINE-001-FIX-1` only. This is an independent read-only review; do not edit files, start services, operate the mini program, or run tests that write any service state.

Baseline and scope:
- Repository: `D:\property-ai`.
- Actual committed baseline is current HEAD `ae2071e3cb2c562bcf2271cddf4bff35b8d2623f` plus the current uncommitted AUTO-DISPATCH-MAINLINE-001-FIX-1 working tree. Do not reset to the older SHA named in the preamble.
- The main Codex session is now the only implementer. Cursor is editor/terminal only.
- Operator-owned `miniprogram/project.config.json`, the two existing AUTH login-review files, and unrelated pre-existing untracked reports are outside this review and must remain untouched.
- Review the implementation files shown by `git diff`, plus `scripts/ops/inspect_auth001_auto_dispatch.py`, `reports/auto-dispatch-mainline-001/`, and only the direct call context needed to assess them.

Completion conditions to check with concrete code/evidence, not implementer PASS claims:
1. A normal resident POST `/work-orders` receives the application Settings, publishes exactly once to the isolated `property_ai.auth001` queue, and the worker consumes it without touching 8082/shared queues.
2. The DEVELOPMENT provider produces structured analysis but never an employee id. Server rules enforce active Employee/User, effective property service, current-business-day open check-in, ON_DUTY, scoped staff role, duty/skill, and capacity.
3. Ranking implements the configured seven weights from docs/05, true per-employee same-building history, response/completion/rating history, primary incident handler, current load, and deterministic tie-breakers. Assignment-time revalidation can try the next ranked candidate if one becomes ineligible.
4. AUTO assignment on 0009 persists `assigned_source=AUTO`, a no-PII candidate/filter snapshot, system logs, audit, load increment, incident primary handler where appropriate, and notification. Shared 0007 compatibility is preserved through live-schema gating; no new migration was created.
5. AI call evidence distinguishes DEVELOPMENT Fake from future production provider mode. No Fake result is described as real model acceptance.
6. Employee duty/skill catalog and add-only employee capability APIs have RBAC/scope checks, append-only audit, tenant validation, stable errors, and no history deletion. Assess concurrency/idempotency and transaction behavior.
7. Automatic assignments with `assigned_by_user_id=NULL` serialize safely. Resident detail has bounded polling only while PENDING_ANALYSIS/PENDING_ASSIGNMENT and visibly marks a persisted AUTO result without inventing front-end success.
8. Actually inspect these screenshots, not just their paths: `reports/auto-dispatch-mainline-001/staff-current-day-checkin.png`, `resident-auto-assigned.png`, `staff-auto-assigned-list.png`. Cross-check `page-evidence.json`, `runtime-evidence.json`, worker/runtime code, and the read-only inspector.
9. Check the failed-but-preserved work order 3 boundary described by evidence: it remained PENDING_ASSIGNMENT with `NOT_CHECKED_IN` across the Asia/Shanghai business-date rollover; the successful page work order 4 used page check-in first and then reached ASSIGNED/AUTO. Do not require deletion or SQL stamping.
10. Check secrets: no broker/app secret remains in tracked source or evidence; local secret file is under ignored `tmp/`. Check that runtime logs/evidence are redacted.
11. Treat `336 passed` as implementer-reported unless independently rerun; session id, exit code, or PASS string alone are not evidence.

Classify each finding as CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or NON_BLOCKING in its title. For issues include exact path/location, trigger, impact, and evidence. A real current-task blocker requires NEEDS_CHANGES; release-only gaps (real self-hosted Qwen/VLM/ASR, production storage, formal release, broader V1) remain open but do not invalidate this local DEVELOPMENT-Fake slice. Do not reopen closed security items without new evidence.

Return one schema-valid final JSON. If PASS, give the main Codex a concrete next implementation slice; do not hand work back to Cursor.
