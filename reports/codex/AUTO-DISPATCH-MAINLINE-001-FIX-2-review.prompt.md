Review task `AUTO-DISPATCH-MAINLINE-001`, repair round 2 of at most 3. This is an independent read-only review. Do not edit files, start/restart services, operate the mini program, or run tests that can change MySQL, Redis, RabbitMQ, object storage, or any application state.

Baseline and scope:
- Repository: `D:\property-ai`.
- Actual current HEAD is `b3bf34d893d2395334b4344c152b50ea28a4fc7f`.
- Product implementation commit is `d144bcddcfc24e6e9eaf60509acd124711bc0561`; its two descendants only update the living handoff and read-only reviewer context.
- Review the implementation commit against its parent `ae2071e3cb2c562bcf2271cddf4bff35b8d2623f`, the two documentation descendants, and the named evidence. Do not attribute the preserved dirty files in `git status` to this task.
- `miniprogram/project.config.json`, `backend/app/model/ai.py`, the existing AUTH login-review files, and unrelated pre-existing untracked reports remain outside scope and must stay untouched.
- The main Codex is the only implementation session. This reviewer is read-only. Cursor is editor/terminal only.

FIX-1 findings to close or retain with concrete evidence:
1. Verify a replay using the same create idempotency key no longer calls `_enqueue_analysis`; inspect the new focused test. Distinguish this from concurrent first-create delivery guarantees that were not the original finding.
2. Verify completion and same-building history use the employee's actual `WorkOrderProcess` plus resolved receipt, so a former assignee is not credited for the later handler's completion.
3. Verify duty/skill catalog rows must be ACTIVE in both recommendation and assignment paths. Formal `docs/05-AI-DISPATCH-SPEC.md` section 4.1 says “员工 User 状态正常、角色含 STAFF 或以上”. Therefore scoped `SUPERVISOR`, `PROPERTY_ADMIN`, and `TENANT_ADMIN` eligibility is intentional when all employee, property, check-in, on-duty, capability, and capacity filters pass; do not require an additional literal STAFF grant unless another approved specification contradicts this.
4. Verify repeated add-only duty/skill requests audit only actually inserted IDs. Verify the existence check plus nested savepoint converts the exact composite unique race into an idempotent no-op without committing the outer service transaction or swallowing unrelated integrity failures.
5. Inspect `scripts/ops/inspect_auth001_auto_dispatch.py` and the saved `order-3-inspector.json` / `order-4-inspector.json`. Audits must be request-and-resource scoped; employee daily status and attendance must be candidate/assignment employee plus exact work-order business-date scoped. Cross-check timestamps: order 3 analyze precedes the 2026-09-20 check-in and persisted `NOT_CHECKED_IN`; order 4 create follows check-in and persisted AUTO assignment, logs, audits, load, and successful notification.

Retain the original slice completion checks:
- Settings injection and isolated `property_ai.auth001` publisher/worker path; no shared 8082 operation and no new migration.
- DEVELOPMENT provider provenance is Fake, never real-model acceptance, and supplies no employee identity. Server rules own filtering/scoring/assignment.
- Configured seven weights, incident primary, current load, deterministic tie-breaks, and assignment-time next-candidate retry.
- 0009 AUTO persistence has a no-PII filter snapshot, system actor/log/audit, load and notification; shared 0007 remains live-schema gated.
- Employee catalog/capability API RBAC, tenant/scope validation, stable errors, add-only history.
- Nullable system assignment serialization and bounded resident polling/visible persisted AUTO marker.
- Actually view the three PNG screenshots under `reports/auto-dispatch-mainline-001/`; a path alone is not visual inspection.
- Check tracked source/evidence for broker/app secrets. The ignored secret file itself is not review output.

Test evidence is implementer-run: targeted `30 passed`, then full unit `346 passed, 1 warning`; `git diff --check` and two `node --check` commands passed. You may inspect test source and recorded results but do not claim an independent test rerun unless you actually perform a safe read-only/code-only test.

Decision rules:
- Use task_id `AUTO-DISPATCH-MAINLINE-001` and state this is repair round 2/3 in `phase` or `session_note`; do not reset the task counter with a new ID.
- Classify issue titles as CURRENT_TASK_BLOCKER, RELEASE_BLOCKER, or NON_BLOCKING, with path/location, trigger, impact, and evidence.
- A real current-task blocker requires NEEDS_CHANGES. Release-only gaps such as real self-hosted Qwen/VLM/ASR, persistent production storage, real Embedding/RAG, broader V1 UI and formal release remain open but do not invalidate this isolated DEVELOPMENT-Fake slice.
- Do not reopen closed security items without new evidence and do not invent a defect to demonstrate a repair loop.
- Return one schema-valid final JSON. If PASS, give the main Codex the next concrete implementation slice with completion conditions and approval boundaries; do not hand work to Cursor or ask the user to relay findings.
