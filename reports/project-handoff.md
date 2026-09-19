# Project handoff (latest facts only)

Updated for main-Codex implementation takeover. Cursor is editor/terminal only; independent review sessions remain read-only. Not a full-repo audit. Not Production V1 / Day 4 / real-AI PASS.

Completed tasks: `AUTO-DISPATCH-MAINLINE-001` on `d144bcddcfc24e6e9eaf60509acd124711bc0561`, `AUTO-WO-LIFECYCLE-001` on `f2839370e493bc4c713f1e73edc9814bb9c419fb`, `PERSISTENT-MEDIA-001` on `b82c33d07bdfa177cb62240163551c5938af8bed`, `AUTO-REASSIGN-EXCEPTION-001` on `f1de143a546912b0cefaa3aa6b50582feb466016`, and `PHONE-CHANGE-001` on `893fd65cfd5c603ea7046c2639c71b076c3fd349`. Their scoped independent read-only reviews are **PASS** in the corresponding `reports/codex/*review.json` files. Production storage, real providers, controlled merge, release validation, and the other frozen V1 gaps remain release blockers rather than local-task blockers.

Latest local milestone evidence: backend unit suite `401 passed, 1 warning` with JUnit evidence. `PHONE-CHANGE-001` actual page flow verifies wrong-OTP persistence, old/new phone verification, masked audit, old-session and concurrent-refresh-successor revocation, immediate/new-login display label, and same-user new-phone relogin. Deterministic MySQL probes cover both refresh orderings and concurrent logout without a surviving successor or deadlock. Independent FIX-2 review is **PASS**. This is synthetic data with Development SMS, not real-SMS or controlled-merge acceptance. Evidence: `reports/phone-change-001/` and `reports/codex/PHONE-CHANGE-001-FIX-2-review.json`.

Headline for MP-CURRENT-FULL-REGRESSION-001: **回归完成，仍有缺口**.
Codex FIX-1 review: `reports/codex/MP-CURRENT-FULL-REGRESSION-001-FIX-1-review.json` **NEEDS_CHANGES** (evidence provenance / denominator). Page wiring for invalid-invite 400 is accepted on `b1458bf`, not on `ab0b882`.
FIX-2 evidence normalize in the same follow-up: matrix IDs = combined-results IDs (83); FIX-1 overlays tagged `code_sha_product=b1458bf` with `baseline_status`; automator 9434 then 9435; extra V1 items include lockout/RAG/AI-fail-create; mock unit tests for invite 400/403. Not Production V1 PASS.

| Item | Current fact |
| --- | --- |
| Repo | `D:\property-ai` |
| Current implementation commit | `893fd65cfd5c603ea7046c2639c71b076c3fd349` (PHONE-CHANGE-001 FIX-2; parent `5ae38f7`) — independent round-3 review **PASS** |
| Implementation owner | Main Codex session. Cursor is editor/terminal only. Independent Codex review sessions are read-only |
| Product baseline | `ab0b882` (AUTH-C). Reports parent `d91d179`. FIX-1 adds invalid-invite HTTP 400 + DevTools evidence. Do not treat FIX-1 UI PASS as `ab0b882` originally passing. |
| Calendar | Spec/git work started **2026-09-15**. Operator last confirmed actual development **day 5 on 2026-09-18**. Current workspace date is **2026-09-20**. Original one-week aim was approximately **2026-09-21**. Full Production V1 cannot meet that date without deleting frozen features. |
| Working tree preserve | `miniprogram/project.config.json` SHA-256 `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. Do not stage, revert, or overwrite. |
| Mini program import | `D:\property-ai\miniprogram` |
| Business API | `http://127.0.0.1:8083/api/v1` → `127.0.0.1:3307/property_ai_auth001_test` @ `20260919_0009`. Restarted on `893fd65`; log `tmp/auth001-runtime/api-20260920-040442.out.log`. Isolated Worker log `tmp/auth001-runtime/worker-20260920-024454.out.log` names only `property_ai.auth001`. 8082 remains untouched. Runtime PIDs are not durable identity; re-check before later process operations. |
| 8082 | Day4 leftover, kept running, **not** used this round. |
| env.local.js | Gitignored. Was found back on 8082/`localPreview true` during FIX-1; re-patched to 8083/`false` for this round only. Not a product-source fix. AppID untouched. |
| DevTools | HTTP `127.0.0.1:24540`. Automator first-round `ws://127.0.0.1:9433`; FIX-1 `ws://127.0.0.1:9435`. 9420 not listening ≠ tool missing. |
| Providers/runtime | Real password; development SMS inbox; DEVELOPMENT Fake AI. Isolated publisher/worker evidence names vhost/queue `property_ai_auth001` / `property_ai.auth001`; do not describe it as real self-hosted inference. Development media survived an API process replacement, but production object storage, malware-scan finalization and signed/private delivery remain release work. |
| Codex CLI | `codex.cmd` 0.155.1. Helper `scripts/ops/codex_exec_readonly.py`. Schema requires `task_result` / `product_gap_changes` / `schedule_risk` / `next_action` / `approval_requests`. |
| learning/ | Not a design source |

Do not export secrets, tokens, OTPs, or `.env`. Public regression screenshots are redacted; unredacted copies stay in gitignored `reports/mp-current-full-regression/private/`. Credentials: `accounts-public.json` (role last4 only) and gitignored `accounts.local.json`.

---

## MP-CURRENT-FULL-REGRESSION-001

Matrix: `reports/mp-current-full-regression/coverage-matrix.json` (current pages + extra V1 from `docs/01-PRD.md` / `docs/10-ACCEPTANCE.md`).
First round: `first-round-results.json` / `env-baseline.json` / run `mp-cfull-20260919-1`.
FIX-1: `fix1-results.json` / `combined-results.json` / `summary.json` / `env-baseline-fix1.json` / run `mp-cfull-20260919-fix1`.

FIX-1 overlays (do not back-date onto `ab0b882`):
- AUTH-RESET / AUTH-RESET-REVOKE / AUTH-RESET-RELOGIN: old password stays on login; sidecar `/me` and `/auth/token/refresh` 401; new password reaches home. First `reset-back-login.png` lagged on the reset page and is not UI proof.
- STAFF-CLAIM-DUP: **FAIL on baseline** because invalid invite returned **401** and `api.js` reLaunched login. **PASS after** invite invalid/expired uses **400** and the claim page shows the business error. Navigated 首页 → 账号与安全 → 员工邀请认领.
- AUTH-SMS-REUSE PASS. AUTH-SMS-EXPIRE remains PARTIAL (no 300s page wait; isolated 0009 API still covers expire).
- INCIDENT-UI PARTIAL (list/empty, raw 小区 ID). STAFF-DAILY-PAGE PASS (shows employee daily row).
- Simulator stopped on the official login page.

Closed security items SEC-REFRESH-STAGGER, SEC-WECHAT-BIND-401, SEC-EMPLOYEE-INVITE-LOCK-ORDER stay closed. The 401→400 change is an authenticated business-error status, not a lock-order change.

Auto-dispatch and normal processing lifecycle: **local isolated slice PASS** on `d144bcd` + `f283937`. It covers DEVELOPMENT-Fake create-to-AUTO, same-key single publish, seven-factor configurable ranking, assignment-time revalidation, employee catalog/capability APIs, nullable system assignment serialization, resident polling/marker, staff accept/start/formal receipt, resident receipt readback/confirm, and persisted relogin `CLOSED`. Shared 0007 remains protected by live-schema gating and defaults remain off. Manual dispatch remains fallback-only; real self-hosted inference is still open. The receipt-ordering UI note was closed in `3b3036b`; ARCHIVE actor attribution still needs a formal convention.

---

## Original Production V1 feature matrix

Status values: `已实现未接线` / `部分实现` / `未实现` / `缺真实外部配置`.
None of these gaps are removed from Production V1.

Frozen username rule: PRD/API 为 **手机号 + 密码**. `users.display_name` 仅为展示，**不另设独立用户名登录身份**.

Still missing or incomplete (non-exhaustive; see coverage-matrix extra_v1): WeChat rebind, controlled merge, recovery, logged-in password-change **page**, employee/dept/skill admin **pages**, knowledge ingest **page**, house import/export, stats/config, incident aggregation, announcement targeting/history, live supervisor-REASSIGN page evidence, formal assignee-name display, SLA Warning/Overdue/Escalation, production Worker reliability/notification retry/DLQ, production object storage and scan-finalization, real SMS/WeChat/realname, self-hosted Qwen/RAG, native media picker, Day4/production release. Phone change has scoped local synthetic acceptance only; real-SMS validation remains open.

---

## This-phase execution

0. 2026-09-19 responsibility takeover: main Codex is the sole implementer. No Git Hook or automatic Cursor handoff is configured; `scripts/ops/codex_exec_readonly.py` remains a manually invoked read-only review helper.
1. Official-session DevTools regression on 8083/0009, not 8082.
2. FIX-1 evidence-gate plus one in-scope API wiring fix (invalid invite 400).
3. Local release-pack isolation from AUTH-C remains: 79 files / 0 dev-login; operator project.config.json untouched.
4. Self-hosted Qwen recommendation unchanged; no endpoint, no download, no GPU rental.
5. Do not push, reset, restore, or stage `project.config.json` / `env.local.js` / `accounts.local.json`.
6. `AUTO-WO-LIFECYCLE-001` PASS on `f283937`: existing AUTO order 5 went accept → start → complete receipt → resident confirm → persisted/relogin CLOSED with matching identity/history. No new order or manual dispatch was used.
7. `PERSISTENT-MEDIA-001` remediation round 1 on `b82c33d` passed independent read-only re-review. Development persistence, immutable/idempotent upload, attachment ownership/scope, authenticated download and actual mini-program preview are demonstrated. Production storage/scanner completion remains a release blocker. The raw first-review call capture is kept local and excluded from Git because it contains environment output matching credential-like patterns; the structured review result is the retained review artifact.
8. `AUTO-REASSIGN-EXCEPTION-001` FIX-1 passed independent review on `f1de143`. The first live retry exposed and fixed an ORM transaction-boundary bug; safe `NO_ELIGIBLE` preserved `PENDING_ASSIGNMENT`, then a business-API synthetic capacity adjustment allowed AUTO reassignment while the rejecter was `RECENTLY_REJECTED`. Work orders 7 and 8 preserve assignment/log/audit/load/notification/process/receipt identities. Live supervisor REASSIGN remains non-blocking unverified evidence; no real third-party contact occurred.
9. `PHONE-CHANGE-001` passed round-3 independent review on `893fd65`. Final user 32 evidence covers actual page controls and read-only DB inspection; users 29-31 cover deterministic MySQL refresh/logout interleavings. OTP/audit/session/display identities remain consistent. Controlled account merge and real SMS remain separate gaps.
10. Current: `WECHAT-REBIND-001`. Read the frozen WeChat credential/recovery specifications and existing bind/login code, then implement the smallest official-session rebind slice with takeover prevention, old-binding invalidation, audit, failure paths, Development-provider page evidence, and independent review. No real WeChat call, migration, paid provider, shared database, or formal release action without approval.
