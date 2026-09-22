You are the single independent read-only Codex reviewer for `D:\property-ai`.
Do not edit files, start/stop processes, run tests, operate pages, send HTTP or
business requests, inspect private tmp contexts/secrets, or create another reviewer.
Review current source plus saved sanitized evidence and the six attached images.

## Fixed review identity

- Task: `NOTIFICATION-EVENTS-INBOX-013`, initial review round 1 of at most 3.
- Overall goal: frozen enterprise property Production V1. Do not shrink account
  merge/recovery, real self-hosted Qwen/bge-m3 RAG, production providers/storage,
  security/performance, real-device, deployment, or formal release work.
- Approval scope: current schema `20260919_0009` only; in-app event creation,
  descending notification pagination/load-more, unread/read state, safe resource
  navigation, scoped local Development runtime/page evidence. No Migration,
  dependency upgrade, shared-vhost operation, external charge, model download, or
  release action.
- Base SHA: `ada791429ca460871cf1e9d6aee036d51ee09ffc`.
- Product commits: `afa8cfd` and concurrency fix `93838c1`.
- Expected current HEAD: `93838c1a624b6c03bf0f3982ebee09b48f727a60`.
- Migration 0010 / ACCOUNT-MERGE-001 remains unapproved, uncreated and unexecuted.
- Known unrelated dirty/pre-existing files include `backend/app/model/ai.py`,
  `miniprogram/project.config.json`, earlier AUTH review outputs and numerous old
  untracked evidence artifacts. Do not accept, restore, or review them as task work.
- The operator-owned `miniprogram/project.config.json` was not edited by this slice;
  page evidence records the same before/after SHA-256
  `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`.

## Completion conditions to review

1. Frozen in-app events cover meaningful work-order creation, AUTO/manual assignment,
   accept/start/reject, supervisor decision, escalation/unresolved receipt, resolved
   receipt, final completion/reopen/cancel plus existing SLA and announcement paths.
   Notifications must be created in the same owned business transaction after the
   state/log/audit changes, without a second FSM or a generic log mirror that creates
   transient/duplicate notifications.
2. Recipients are deduplicated and tenant/property/resource scoped; actors are
   excluded only where appropriate. AI never names staff directly; AUTO dispatch still
   uses the existing rules. External delivery remains at-least-once, not falsely
   claimed exactly-once.
3. `GET /notifications` is true descending keyset pagination: subsequent rows use
   `id < after_id`, ordered `id DESC`, limit+1 lookahead, no overlap, and no false
   cursor on the final page. Query bounds are server validated.
4. List and mark-read require an ACTIVE actor; mark-read is Anti-IDOR and idempotent.
   Concurrent mark-read must serialize and preserve one timestamp.
5. Mini-program load-more merges/deduplicates and rejects non-advancing cursors.
   Clicking a notification uses a fixed resource allowlist, current `/me`, a single
   matching grant for tenant/property/permission, server-side resource validation and
   exact resource navigation. Unknown or inaccessible targets must not form arbitrary
   URLs.
6. Actual page evidence must show real `wx.request`, page state, screenshots and the
   same work order/announcement objects; refresh/relogin must retain read state.
7. Development/Fake/local evidence must not be upgraded to real provider/model,
   real-device, production, external exactly-once, or complete V1 acceptance.

Please pay special attention to:

- repository cursor direction and final-page semantics;
- the `FOR UPDATE` ownership query and two-session concurrent read behavior;
- business notification placement relative to transaction commit and auto-dispatch
  nested savepoint restoration;
- recipient scope/deduplication and whether emitted recipients can actually navigate
  to the resource under current authorization;
- mixed-role route decisions using one matching grant rather than unioned permissions;
- exact announcement lookup, resident bind deep-link, and supervisor deep-link scope;
- whether tests and evidence actually support the claimed conditions, including the
  fact that saved page screenshots were visually inspected by the main session but
  the reviewer must independently inspect all attached PNGs;
- secret/redaction boundaries in committed evidence.

Do not turn pre-existing manager cancellation cleanup, historical former-assignee
read policy, cross-property tenant-wide announcement evidence, production providers,
or real model/release work into a current-task blocker unless this diff directly
regresses them. Keep real gaps visible under stable NON_BLOCKING or RELEASE_BLOCKER
IDs. Do not reopen earlier closed issues without new evidence.

## Source and tests to inspect

- Diff `ada791429ca460871cf1e9d6aee036d51ee09ffc..93838c1a624b6c03bf0f3982ebee09b48f727a60`.
- `backend/app/repository/knowledge.py`
- `backend/app/service/notification.py`
- `backend/app/api/v1/notifications.py`
- `backend/app/schema/knowledge.py`
- `backend/app/service/work_order.py`
- `backend/app/service/auto_dispatch.py`
- directly required transaction/provider/repository context
- `backend/tests/unit/test_notification_events_inbox.py`
- related existing SLA, announcement and work-order tests as needed
- `miniprogram/services/api.js`
- `miniprogram/utils/notification-route.js`
- `miniprogram/pages/notifications/list.js`
- `miniprogram/pages/notifications/list.wxml`
- `miniprogram/pages/announcements/detail.js`
- `miniprogram/pages/houses/houses.js`
- `miniprogram/pages/houses/houses.wxml`
- `miniprogram/pages/supervisor/queue.js`

Main-session terminal results, not reviewer-rerun:

- focused notification suite: `14 passed, 3 warnings`;
- full pure-unit suite: first run `564 passed` plus two `tmp_path` setup errors caused
  by a denied global Temp directory; retry with a fresh workspace basetemp passed
  `566 passed, 1 warning`;
- changed/evidence JavaScript syntax and `git diff --check` passed.

## Sanitized evidence to inspect

- `reports/notification-events-inbox-013/runtime-evidence.json`
- `reports/notification-events-inbox-013/page-evidence.json`
- `reports/notification-events-inbox-013/post-page-database-evidence.json`
- `reports/notification-events-inbox-013/test-results.json`
- the three evidence scripts, checking their guards and read/write boundaries
- all six attached PNGs under `reports/notification-events-inbox-013/`

The saved run is `ntf013-feaff1e125c8`, 8083 to
`127.0.0.1:3307/property_ai_auth001_test@20260919_0009`, isolated RabbitMQ vhost
`property_ai_auth001`, queue `property_ai.auth001`. Normal Development SMS sessions
created work order 23, AUTO-assigned employee 1, completed its escalation/continue/
receipt/confirmation lifecycle to CLOSED, and published announcement 2 through the
governed API. The run records 9 resident, 2 staff and 2 manager new notifications,
terminal Development deliveries, 404 cross-user mark-read, two concurrent 200 reads
with one timestamp, a stable repeat/relogin read state, query-bound 422s, and staff
pagination 20+3 with no overlap and null final cursor. The actual page run records
the same IDs, one read request per clicked item, exact work-order/announcement fetch,
and unchanged project config hash. Treat these as claims to verify, not automatic PASS.

## Required result

Use stable finding IDs. For each finding, prefix the title with exactly one category:
`CURRENT_TASK_BLOCKER`, `RELEASE_BLOCKER`, or `NON_BLOCKING`; give exact path,
trigger, impact and evidence. State your read-only boundary and precisely which files
and images you inspected. PASS only if no current-task blocker remains. If PASS,
make `next_action` the exact main-Codex handoff/matrix/evidence commit closure followed
by selection of the next current-schema V1 slice; no user approval is needed for that
normal closure. Preserve 0010, paid resources, shared maintenance, real providers and
formal release as their existing approval gates.
