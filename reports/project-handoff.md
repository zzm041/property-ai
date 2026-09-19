# Project handoff (latest facts only)

Updated for main-Codex implementation takeover. Cursor is editor/terminal only; independent review sessions remain read-only. Not a full-repo audit. Not Production V1 / Day 4 / real-AI PASS.

Current implementation task: `AUTO-DISPATCH-MAINLINE-001-FIX-1` on `aa268d1c4d9e17b0758fb18c4b4b579e50c08628`. The read-only review in `reports/codex/AUTO-DISPATCH-MAINLINE-001-review.json` is **NEEDS_CHANGES**: AUTO persistence code exists, but ordinary create/enqueue Settings injection, isolated publisher/worker proof, rule completeness, cross-process media and same-work-order audit/log/notification evidence are not yet accepted.

Headline for MP-CURRENT-FULL-REGRESSION-001: **回归完成，仍有缺口**.
Codex FIX-1 review: `reports/codex/MP-CURRENT-FULL-REGRESSION-001-FIX-1-review.json` **NEEDS_CHANGES** (evidence provenance / denominator). Page wiring for invalid-invite 400 is accepted on `b1458bf`, not on `ab0b882`.
FIX-2 evidence normalize in the same follow-up: matrix IDs = combined-results IDs (83); FIX-1 overlays tagged `code_sha_product=b1458bf` with `baseline_status`; automator 9434 then 9435; extra V1 items include lockout/RAG/AI-fail-create; mock unit tests for invite 400/403. Not Production V1 PASS.

| Item | Current fact |
| --- | --- |
| Repo | `D:\property-ai` |
| Current HEAD | `aa268d1c4d9e17b0758fb18c4b4b579e50c08628` — AUTO assignment persistence code; review is NEEDS_CHANGES, not PASS |
| Implementation owner | Main Codex session. Cursor is editor/terminal only. Independent Codex review sessions are read-only |
| Product baseline | `ab0b882` (AUTH-C). Reports parent `d91d179`. FIX-1 adds invalid-invite HTTP 400 + DevTools evidence. Do not treat FIX-1 UI PASS as `ab0b882` originally passing. |
| Calendar | Spec/git work started **2026-09-15**. Operator last confirmed actual development **day 5 on 2026-09-18**. **Today is 2026-09-19**. Original one-week aim ≈ **2026-09-21**. Full Production V1 cannot meet that date without deleting frozen features. |
| Working tree preserve | `miniprogram/project.config.json` SHA-256 `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. Do not stage, revert, or overwrite. |
| Mini program import | `D:\property-ai\miniprogram` |
| Business API | `http://127.0.0.1:8083/api/v1` → `127.0.0.1:3307/property_ai_auth001_test` @ `20260919_0009`. Restarted after invite 400 fix; listen PID recorded in `reports/mp-current-full-regression/env-baseline-fix1.json`. |
| 8082 | Day4 leftover, kept running, **not** used this round. |
| env.local.js | Gitignored. Was found back on 8082/`localPreview true` during FIX-1; re-patched to 8083/`false` for this round only. Not a product-source fix. AppID untouched. |
| DevTools | HTTP `127.0.0.1:24540`. Automator first-round `ws://127.0.0.1:9433`; FIX-1 `ws://127.0.0.1:9435`. 9420 not listening ≠ tool missing. |
| Providers/runtime | Real password; development SMS inbox; Fake AI. Auth001 API and an isolated Celery worker are currently running; exact publisher broker/vhost/queue match and cross-process storage still require evidence. |
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

Auto-dispatch: 0009 has `assigned_source`; `aa268d1` adds AUTO persistence and isolated runtime scripts. Defaults remain off. Current blocker is wiring/proof and rule completeness, not shared-0007 schema. Manual dispatch evidence remains fallback-only.

---

## Original Production V1 feature matrix

Status values: `已实现未接线` / `部分实现` / `未实现` / `缺真实外部配置`.
None of these gaps are removed from Production V1.

Frozen username rule: PRD/API 为 **手机号 + 密码**. `users.display_name` 仅为展示，**不另设独立用户名登录身份**.

Still missing or incomplete (non-exhaustive; see coverage-matrix extra_v1): phone change, WeChat rebind, merge, recovery, logged-in password-change **page**, employee/dept/skill admin **pages**, knowledge ingest **page**, house import/export, stats/config, incident aggregation, announcement targeting/history, complete receipt fields, staff reject/escalate UI, supervisor exception decisions, SLA Warning/Overdue/Escalation, notification retry/DLQ, isolated Worker, persistent object storage, real SMS/WeChat/realname, self-hosted Qwen/RAG, native media picker, Day4/production release.

---

## This-phase execution

0. 2026-09-19 responsibility takeover: main Codex is the sole implementer. No Git Hook or automatic Cursor handoff is configured; `scripts/ops/codex_exec_readonly.py` remains a manually invoked read-only review helper.
1. Official-session DevTools regression on 8083/0009, not 8082.
2. FIX-1 evidence-gate plus one in-scope API wiring fix (invalid invite 400).
3. Local release-pack isolation from AUTH-C remains: 79 files / 0 dev-login; operator project.config.json untouched.
4. Self-hosted Qwen recommendation unchanged; no endpoint, no download, no GPU rental.
5. Do not push, reset, restore, or stage `project.config.json` / `env.local.js` / `accounts.local.json`.
6. Next: repair and prove the default ordinary auto-dispatch path in the already authorized auth001 isolated synthetic environment; do not create 0010, touch shared 0007, enable production defaults, or claim Fake as real AI.
