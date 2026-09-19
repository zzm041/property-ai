# ACCOUNT-MERGE-001 pre-approval audit

- Task: `ACCOUNT-MERGE-001-PREAPPROVAL-AUDIT`
- Repository: `D:\property-ai`
- Audited product/evidence HEAD: `f5a7b62d83303ab2c742bbd3baef27cf48f99999`
- Database used for the permitted write tests: `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`
Date: 2026-09-20

This is a requirements, current-0009 test, and design-impact audit. It is not an implementation or acceptance of account merge, not a migration run, and not Production V1 acceptance.

## 1. Requirements coverage and actual gaps

The formal sources read in full were `docs/00-PROJECT-SCOPE.md`, `01-PRD.md`, `02-ROLES-PERMISSIONS.md`, `03-DOMAIN-MODEL.md`, `04-WORKORDER-STATE-MACHINE.md`, `05-AI-DISPATCH-SPEC.md`, `06-API-CONTRACT.md`, `07-SECURITY-PRIVACY.md`, `08-NON-FUNCTIONAL.md`, `09-DEPLOYMENT.md`, `10-ACCEPTANCE.md`, `BACKLOG.md`, `AGENTS.md`, and `reports/project-handoff.md`. The original 83 coverage IDs remain the denominator; this audit adds current overlays rather than rewriting the historical baseline.

| Core module | Implemented now | Verified evidence boundary | Actual remaining gap |
| --- | --- | --- | --- |
| Account and credentials | Password/SMS login, refresh/logout, reset, profile, phone change, WeChat bind/rebind; one internal `users.id` | Current unit 407 pass; current isolated auth/identity 7 pass; prior scoped phone and WeChat page/API evidence remains version-bound | Account merge and high-risk recovery absent; logged-in password-change page absent; lockout incomplete; real SMS/WeChat/real-name/liveness unverified |
| Property/resident | Property/house catalog read, cascading selection, bind request/review, relationship model/history | Prior real-page apply/approve/reject evidence; current isolated identity test exercised a synthetic apply/approve path through APIs | Property/building/unit/house maintenance UI and import/export absent; vacancy/tenancy change and history administration incomplete |
| Employee | Pre-created employee, invite/claim, properties, duty/skill catalog, attendance, capacity/load and scoped roles | Prior page evidence and current isolated identity claim path; auto-dispatch evidence used actual eligibility data | Admin pages for employee/department/position/duty/skill/service-property lifecycle and metrics remain incomplete |
| Repair and media | Text repair, file registration/upload/download, work-order media relations, idempotent create | Prior text workflow and development persistent-media evidence; current unit regression | Native image/audio picker remains environment-unverified; production object storage, scan finalization and real ASR/VLM absent |
| AI analysis and dispatch | Create can enqueue; isolated Worker calls DEVELOPMENT provider; server filters/scores and persists `assigned_source=AUTO`; manual assignment remains fallback | Prior scoped cross-process Fake evidence on `d144bcd`; no relevant product change since | Real operator-controlled Qwen/VLM not run; production model capacity/availability and real inference not accepted; Fake is not real-model evidence |
| Work-order lifecycle | Accept/start/feedback/receipt, reject/escalate, supervisor decide/reassign, resident confirm/reopen, append/cancel/rate APIs and pages in current tree | Prior order 5 and order 8 evidence plus focused reviews; current unit regression | Native before/after photos, live supervisor REASSIGN page proof, formal assignee display and the full invalid-transition/concurrency matrix remain incomplete |
| SLA/events/notifications | SLA data model, in-app notifications, delivery rows, claim lease/retry code, incident list/summary | Prior notification page evidence; current code inspection; isolated Worker is present | No complete running SLA warning/overdue/escalation scheduler; incident aggregation does not yet establish duplicate-dispatch avoidance; production notification provider/DLQ acceptance absent |
| Announcements | Draft/review/publish/read with human publication path | Prior local page evidence and current code/unit regression | Fine-grained targeting, edit/publish history and complete rejection UI/evidence incomplete; AI must remain draft-only |
| Knowledge/RAG/statistics | Knowledge API/version/ingest Worker, assistant page and cited Fake response path | Prior Fake page evidence and code inspection | Admin ingest page, real bge-m3/Milvus retrieval/reranking/Qwen answer, permission-isolation runtime evidence, question aggregation and approved statistics/config UI incomplete |
| UI/governance/release | One native mini program, RBAC/scope/ownership checks, audits, request IDs, health/readiness, isolated async runtime | Current health/readiness and tests; prior page slices; no formal release action | Formal UI completion, comprehensive IDOR/security/performance, backup/restore, target deployment, real-device and production release acceptance remain open |

The coverage matrix is evidence bookkeeping, not the product definition. A PASS on a local synthetic slice does not remove the real-provider, production-runtime, formal-UI or release gap for the same module.

## 2. Current 0009 tests and environment

### Re-established current state

- 8083 responded on `/api/v1/healthz` and `/api/v1/readyz`; it targets the named isolated database through `scripts/ops/serve_auth001_isolated_api.py`.
- The isolated Worker uses queue `property_ai.auth001`; 8082 was left untouched.
- MySQL, Redis, RabbitMQ and Milvus readiness were reported up. LLM/VLM/ASR/Embedding/Reranker/Storage live vendors were explicitly `not_checked`.
- Startup scripts were inspected before using the runtime: they construct settings and start the process; they do not migrate, seed, drop, truncate or reset the database.
- `backend/tests/integration/conftest.py` deletes business-table contents around tests and was therefore excluded. `scripts/ops/provision_auth001_isolated_db.py` drops/creates/migrates and was not run.

### Run now

| Evidence | Result | Boundary |
| --- | --- | --- |
| `reports/account-merge-001-preapproval/unit-results.xml` | `407 passed`, 1 warning | Full current backend unit suite; no product completion inference |
| `reports/account-merge-001-preapproval/isolated-0009-results.xml` | `7 passed`, 1 warning | Only `test_isolated_auth_runtime.py` and `test_isolated_identity_runtime.py`; random synthetic identities; no schema change or database reset |
| Read-only database inspection | revision `20260919_0009`; 39 ACTIVE users; 45 phone rows (38 ACTIVE, 7 ENDED); every phone-bound user has exactly one ACTIVE phone | Current post-test snapshot; no `account_merges` or `identity_recovery_requests` table |
| Constraints | both `uq_user_phones_active_e164` and `uq_user_phones_active_user` present | Confirms the live 0009 single-current-phone model |

The seven isolated tests cover normal credential/session behavior and synthetic profile, house request/review and employee claim paths. They do not implement or test merge. They added only randomly named synthetic objects through the existing test/business paths; no status was SQL-stamped to APPROVED or CLOSED.

### Reused version-bound evidence, not rerun

- Automatic dispatch: scoped DEVELOPMENT-Fake, isolated Worker and persisted AUTO assignment evidence on `d144bcddcfc24e6e9eaf60509acd124711bc0561`.
- Normal lifecycle: scoped order-5 page/business evidence on `f2839370e493bc4c713f1e73edc9814bb9c419fb`.
- Persistent development media: scoped evidence on `b82c33d07bdfa177cb62240163551c5938af8bed`.
- Reject/escalate/reassign/exception cycle: scoped evidence on `f1de143a546912b0cefaa3aa6b50582feb466016`.
- Phone change: scoped synthetic page/API/concurrency evidence on `893fd65cfd5c603ea7046c2639c71b076c3fd349` with evidence commit `a25ff332e05f3d6d069fd6b8c191817a9ce5fadf`.
- WeChat bind/rebind: scoped Development-provider page/API evidence on `a42a7fbb646ee3b892c938fa9cb7cd8d6ab81584`, recorded by `f5a7b62`.

These histories remain useful because no subsequent relevant product change invalidated them. They were not represented as newly run. Real providers, real self-hosted inference, native media chooser, production storage, backup/performance and release remain unverified.

## 3. Proposed 0010 requirements and security conclusion

### Frozen meaning actually found

- `docs/01-PRD.md:33`: keep the target `user_id`, migrate identity bindings, roles, house relationships, work orders and evaluations.
- `docs/03-DOMAIN-MODEL.md:41`: an `AccountMerge` entity is required.
- `docs/03-DOMAIN-MODEL.md:120`: **V1 has one current effective phone per user**.
- `docs/03-DOMAIN-MODEL.md:154`: identity bindings move to the target, work-order reporter and relationship user IDs move, and the source is frozen with merge audit retained.
- `docs/03-DOMAIN-MODEL.md:32,173`: `unique(phone)` / `user_phones.phone_e164 UNIQUE` is written without an ACTIVE qualifier. This conflicts with 0009's active-only global key and its retained ENDED history; it is an unresolved spec/code semantic, not evidence that multiple current phones are allowed.
- `docs/06-API-CONTRACT.md:72-73`: preview plus idempotent confirm endpoints.
- `docs/10-ACCEPTANCE.md:42`: merge must be audited.

No formal source says that every source phone remains permanently ACTIVE, can log in to the target, can reset the target password, or becomes a target notification destination. “Migrate identity bindings” is not sufficient authority to introduce permanent login aliases.

### Five phone meanings must stay separate

| Meaning | Recommended V1 rule | Basis/impact |
| --- | --- | --- |
| Historical source phone | Retain the row and merge trace, but end it | Preserves history without violating the one-current-phone rule; whether any retired number may later be rebound is an explicit product decision |
| Trace from source identity | `AccountMerge` records source/target and a redacted impact manifest | Do not rewrite immutable actors merely to make a lookup convenient |
| Login phone | Only the target's current ACTIVE phone logs in | Current repositories return a scalar current phone, and password/SMS flows assume one |
| Password recovery phone | Only the target's current ACTIVE phone can reset the target password | A retired source number must not gain password-reset authority |
| Primary contact/notification phone | The target current phone | There is no approved multi-contact preference model or UI |

An ENDED source number must also be protected from the current SMS-login behavior that auto-creates a user when no ACTIVE row exists (`backend/app/service/credential_auth.py:241-254`). The future implementation needs an account-merge tombstone lookup and a stable rejection such as `AUTH.ACCOUNT_MERGED`; it must not transparently authenticate the MERGED source as the target and must not silently create a new user for that retired number.

### Unique-constraint conclusion

The previously proposed deletion of `uq_user_phones_active_user` is **not necessary for the recommended frozen-spec implementation and should be removed from the proposed 0010**.

- Keeping it enforces the formal single-current-phone rule.
- `uq_user_phones_active_e164` separately prevents one number from being ACTIVE on two users; it does not enforce one current phone per user.
- Dropping the per-user constraint would make `get_active_phone_for_user*()` scalar queries ambiguous and would leave change-phone's “current phone”, profile security inputs, reset authority, password login and the notification/contact destination without an approved selection rule.
- If the product owner instead wants multiple login/recovery aliases, that is a requirements change. It requires explicit decisions for alias revocation, password reset authority, contact selection, phone-change UI, rate limiting and takeover risk. It must not be introduced as a migration implementation detail.

The formal global-uniqueness wording at `docs/03-DOMAIN-MODEL.md:32,173` and the actual active-only 0009 key are not equivalent. For the merge security case, the recommendation is to reserve every merged-source phone indefinitely through the merge ledger/tombstone so SMS login cannot recreate an account. The broader rule for ordinary ENDED phone rows remains a product decision: (a) globally reserve every historically used number, matching the literal document, or (b) permit later rebind only after fresh verification, matching the active-only schema. 0010 must not silently settle that broader conflict. A preflight must also count repeated phone values across ACTIVE and ENDED rows before any future global-unique proposal.

### 0010 design recommendation

The original proposal **needs modification** before approval. A revised design should keep both phone unique constraints and add only the merge ledger/guard needed for controlled migration:

- `account_merges`: BIGINT PK; source/target/operator user FKs; optional source proven phone row; status `VERIFYING|PREVIEWED|COMPLETED|CANCELLED|EXPIRED`; hashed source OTP and hashed single-use confirmation token; OTP attempt/max-attempt counters; redacted `impact_json` plus `impact_hash`; target reauthenticated/source verified/expires/completed/cancelled timestamps; normal timestamps.
- CHECK source != target; source/target/operator and expiry indexes; an active-intent computed key unique for a source while VERIFYING/PREVIEWED. Expired intents must be expired on access/cleanup so they do not permanently occupy the source.
- Reuse `idempotency_records` for confirm, owned by the authenticated target, with request hash and merge resource ID. Do not copy all historical source idempotency rows.
- Create the VERIFYING intent only after recent target reauthentication. Send a merge-specific OTP whose hash, phone row, source, target, purpose, expiry and attempt state are all attached to that intent; do not reuse a generic login/reset challenge. Successful proof consumes the OTP and advances the same row to PREVIEWED. Confirm must re-read and compare the impact hash after locks are acquired.
- 0009 has one ACTIVE user without an ACTIVE phone. A phone-free source cannot use this self-service proof. Recommendation: reject the self-service preview with a stable recovery-required error and route it to the separately frozen high-risk recovery flow once that exists; do not improvise WeChat-only proof or admin override in 0010. The owner must approve this limitation or specify another verified route.
- Source password never overwrites an existing target password. If the target has no password, the frozen specification does not decide whether the source hash becomes a target credential. Recommendation: discard the source credential and require an explicit target-side password setup after merge/re-authentication. Transfer-versus-new-password remains an approval decision; only the chosen branch enters implementation/tests. Credential disposition is audited without secret material.
- Revoke source access/refresh sessions and open recovery tickets; do not reparent them. Existing target sessions should be rotated after successful merge because authorization changed.

The present services do not all take a common user-row/merge guard before account-owned writes. Locking two `users` rows alone is therefore insufficient against concurrent phone/WeChat changes, resident approvals, employee claims or work-order writes. The future change must introduce a shared merge-in-progress guard and a consistent lock order for every affected mutating service, or reject implementation approval until an equivalent serialization strategy is specified.

MySQL DDL and the business merge transaction are separate. DDL auto-commit cannot be promised to roll back with a failed business merge. Structural rollback should be allowed only before completed merges (or by restoring a verified pre-migration backup); a completed business merge is not safely reversible by a schema downgrade. If multiple ACTIVE phones had been allowed, the old unique key could not safely be restored without deleting or arbitrarily choosing data—another reason not to drop it.

The future migration file, if separately approved, must have `down_revision = "20260919_0009"`; it must retain the exact isolated-target guard for the first authorized rehearsal and must not use revision `0008`. No filename/revision is created by this audit. A production-target migration remains a separate rollout approval after isolated rehearsal and recovery evidence.

## 4. All references and future regression list

The current live schema has 24 foreign-key columns referencing `users.id` across 19 tables. The number 24 is columns, not tables.

| # | FK column | Disposition in a future merge | Conflict, authorization and trace rule |
| --- | --- | --- | --- |
| 1 | `announcement_reviews.reviewer_user_id` | Keep source | Historical reviewer actor is immutable |
| 2 | `announcements.created_by_user_id` | Keep source | Historical creator actor is immutable |
| 3 | `audit_logs.actor_user_id` | Keep source | Never rewrite audit actor; merge audit links source/target |
| 4 | `auth_sessions.user_id` | Keep source, revoke open sessions | Never reparent a source token/session to target |
| 5 | `employee_invites.claimed_user_id` | Keep source | Historical claimant; current employee ownership is handled separately |
| 6 | `employee_invites.inviter_user_id` | Keep source | Historical inviter actor |
| 7 | `employees.user_id` | Move source employee to target only if target has none | If both users own independent Employee records, reject for controlled resolution; never take the first row |
| 8 | `file_objects.uploader_user_id` | Move to target | Field is used for current upload/download authorization; record moved IDs in the merge manifest; storage keys remain unchanged |
| 9 | `idempotency_records.user_id` | Keep source | Historical request ownership; global keys remain unique; new merge-confirm record belongs to target |
| 10 | `knowledge_documents.created_by_user_id` | Keep source | Historical creator; knowledge authorization remains tenant/property/visibility based |
| 11 | `notifications.user_id` | Move to target | Current inbox ownership; block/settle claimed deliveries before commit to avoid a pre-merge recipient snapshot |
| 12 | `password_reset_tickets.user_id` | Keep source and consume every open ticket | Never reparent recovery authority |
| 13 | `resident_bind_requests.reviewer_id` | Keep source | Historical management actor |
| 14 | `resident_bind_requests.user_id` | Keep source | It is historical applicant attribution. Any source or target PENDING request blocks preview/confirm until the existing review flow reaches APPROVED or REJECTED; target access to approved housing comes from the migrated relationship, not rewritten application history |
| 15 | `resident_house_relations.user_id` | Move all source relations to target after conflict check | Preserve ended history; reject active duplicate/conflicting relationship type that cannot be deterministically resolved |
| 16 | `user_passwords.user_id` | Conditional transfer or remove source credential | Target password wins; source never overwrites it; credential disposition is audited without secret material |
| 17 | `user_phones.user_id` | End source current phone, then attach as target history/trace | Keep both unique keys; only target phone remains ACTIVE; retired source number cannot login/reset or auto-register |
| 18 | `user_roles.user_id` | Move only currently legal grants | Exact active duplicate may deduplicate with manifest evidence; tenant/property scope remains unchanged; never recreate revoked grants |
| 19 | `user_wechats.user_id` | Move a non-conflicting binding | Same appid/same identity may deduplicate; same appid/different identity is a hard conflict requiring explicit resolution |
| 20 | `work_order_assignments.assigned_by_user_id` | Keep source | Historical dispatcher actor; current assignee is Employee-based |
| 21 | `work_order_feedbacks.actor_user_id` | Keep source | Historical feedback actor; the work order itself moves through reporter ownership |
| 22 | `work_order_logs.actor_user_id` | Keep source | Append-only history actor; never rewrite |
| 23 | `work_order_media.uploader_user_id` | Keep source | Historical uploader actor; live file ownership is controlled by `file_objects.uploader_user_id` |
| 24 | `work_orders.reporter_user_id` | Move to target | Explicit formal requirement and current resident ownership check |

Additional actual references:

- Non-FK historical actor columns `announcements.reviewed_by_user_id`, `announcements.published_by_user_id`, and `knowledge_document_versions.created_by_user_id`: keep source.
- JSON: `audit_logs.details_json`, assignment payloads and work-order log payloads are evidence/reason snapshots, not authoritative ownership in current code. Do not run a blind JSON user-ID rewrite. Any future newly discovered authoritative JSON reference must be named and migrated explicitly.
- JWT: `sub=user_id` and `sid=session_id`. Source sessions are revoked and source status becomes MERGED; old tokens must fail. They are never mapped to target.
- Redis/rate limits: current auth keys use phone/IP/challenge; failed-task records hold task arguments. They are not authoritative user ownership, but caches derived from grants/identity must be invalidated if added.
- Worker arguments: current analysis, ingest and delivery tasks carry work-order/document/delivery IDs, not user IDs. Workers resolve current database state after commit.
- Notification race: delivery claims copy `notifications.user_id` before an external provider call. Confirm must refuse or quiesce an in-flight source delivery; a MySQL rollback cannot recall an already accepted external push.
- File/object storage: object keys and bytes do not move. Transfer only current `file_objects` ownership; keep historical media uploader; re-run authorization checks across directly owned and work-order-linked objects.
- Employee load/processing: moving `employees.user_id` leaves Employee IDs, assignments, attendance and load intact. A dual-Employee conflict is rejected.
- Knowledge and announcements: creator/reviewer history stays source; tenant/property/visibility authorization does not broaden.
- Indexes are access paths, not ownership. Revalidate every unique collision before update rather than treating FK success as business success.

### Required post-approval test plan (not executed now)

1. Migrate a protected 0009 copy to the approved 0010; assert exact revision, constraints, counts and no unrelated data change.
2. Preview privacy and recent-target reauthentication; source OTP bound to source/target/purpose; hash, expiry, one-use and replay rejection.
3. Success with target password present and absent; source phone retired, target phone current, source login/reset rejected, no auto-registration, target session rotation and source session/ticket revocation.
4. source=target, disabled/MERGED users, duplicate/reverse/chain merge, one source concurrently targeting two accounts, expired preview reuse.
5. Impact-hash rejection when phone, WeChat, role, house relationship or pending request changes between preview and confirm.
6. Employee/WeChat/house/role conflicts, cross-tenant/property scopes and previously ended/revoked data; no privilege resurrection.
7. Concurrent phone/WeChat change, resident approval, employee claim, work-order create/update/assignment and merge under a shared guard; deterministic lock order and no deadlock leak.
8. Fault injection at every database mutation stage: one atomic rollback, source and target credentials/business relations still usable, no partial privilege move.
9. Verify all 24 FK dispositions, three non-FK actors, JSON untouched, historical actors unchanged, target authorization correct, source frozen and merge audit complete/redacted.
10. Pending and claimed notification, queued analysis/ingest/delivery, object authorization, ongoing work orders, employee load, and media/knowledge cross-regression.
11. Separate DDL-failure/recovery drill and business-transaction tests; never describe MySQL DDL rollback as business rollback.

Environment provenance note: the two JUnit files independently establish 407 and 7 passing tests. The single-warning observations, listener/process state, readiness result and database snapshot were observed in this main audit session but were not separately saved as raw captures in this directory. They must remain report-derived observations, not be upgraded to independently reviewed runtime proof.

## 5. Approval recommendation and critical path

Do **not** approve the previously described 0010 unchanged. It is worth approving a revised 0010 only after the owner decides the three explicit policy questions below and the proposal supplies the shared concurrency guard plus the exact collision policy. The migration itself is small; the safe service impact is not.

Recommended decision:

1. Keep both phone unique constraints.
2. Retire source phones as history and trace; do not make them login/recovery/contact aliases.
3. Add an account-merge ledger/preview token and explicit tombstone lookup that blocks source-number re-registration.
4. Reject dual-Employee, conflicting same-appid WeChat, ambiguous active-house and irreconcilable permission conflicts instead of silently choosing rows.
5. Require a common merge guard/lock contract across affected writes, plus the test plan above, before implementation acceptance.

Owner decisions still required before 0010 approval, with the audit recommendation first:

1. **Retired phone reuse:** reserve every merged-source number indefinitely; separately decide whether ordinary non-merge ENDED numbers are globally reserved or may be rebound after fresh verification. Recommendation for ordinary rows: permit verified rebind to remain compatible with 0009, then clarify the overly broad formal UNIQUE wording.
2. **Phone-free source proof:** reject self-service merge and route to high-risk recovery when available. Do not add an implicit WeChat-only or administrator bypass.
3. **Source password when target has none:** discard it and require explicit target-side password setup; do not transfer a credential by default.

The automatic-dispatch mainline does not depend on account merge. While the revised migration decision is pending, the planned non-dependent work can continue with employee/admin data quality and UI, real-model deployment planning without spending or downloading, running SLA/notification reliability, incident aggregation, and the remaining formal work-order/media UI. Any new migration, paid/real provider, shared-service maintenance or release remains separately gated.

**0010尚未获批、未创建、未执行。**

### Independent read-only review

- Round 1: `NEEDS_CHANGES`; four audit-design findings were incorporated without changing product code.
- Same-session round 2: `PASS` for this report as a conservative approval decision basis with the three product decisions above still outstanding.
- The reviewer did not rerun tests, inspect the live database/processes, operate pages or send business requests. It did not review an account-merge implementation or 0010 migration because neither exists.
- Results: `reports/codex/ACCOUNT-MERGE-001-PREAPPROVAL-review.json` and `reports/codex/ACCOUNT-MERGE-001-PREAPPROVAL-review-r2.json`.
