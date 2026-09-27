# PENDING0014 implementation checkpoint — 2026-09-27

Status: **SOFTWARE FOCUSED PASS; SCHEMA/RUNTIME BLOCKED**

Product implementation commit: `f5c8154` (base `0a9c9824bbd69e6a1f12bb99fca60ed0cbdc9834`).

The fixed deadline `2026-09-27 14:00 Asia/Shanghai` has passed. This checkpoint
does not claim Production V1 delivery.

## Implemented in the pending candidate

- Approval and completion remain separate. Completion rechecks two distinct
  currently authorized reviewers, the proof lifetime, the approved impact hash,
  and deterministic source/target locks.
- Merge and account recovery move only the reviewed current-ownership records,
  preserve historical actors, revoke source/target sessions and open reset
  tickets, discard the source password, retain immutable merge/tombstone/audit
  history, and preserve both phone uniqueness constraints.
- A source account with no ACTIVE phone can use the approved ACCOUNT_RECOVERY
  route while retaining its historical phone row; it is never reactivated to
  manufacture proof.
- A target with no password must provide a fresh proof assertion bound to the
  completion case and its one current ACTIVE phone before setting an explicit
  password. The isolated test provider is server-signed and guarded by
  `APP_ENV=test`, exact AUTH001 connection coordinates and an explicit secret.
- Current-ownership writers use a shared transaction guard that locks and
  refreshes the User row before creating mutable ownership. Directly affected
  credential, work-order/file, notification, resident relation, employee,
  WeChat and RBAC paths recheck ACTIVE state rather than relying on an old
  Principal or a surviving foreign key.
- The mini-program distinguishes approval from execution, requires explicit
  phone-replacement confirmation, does not replay a proof/password failure as
  an expired-session retry, and clears the revoked local session immediately
  after successful completion.
- The VLM adapter now uses the canonical server JSON Schema and rejects the
  saved nested JSON wrapper instead of recursively decoding or rewriting it.
  This is offline protocol work only; R3 remains NOT_READY.

## Verification performed

The main implementation session ran the directly affected pure-unit group with
the restored Python 3.12.10 interpreter and existing isolated site packages:
`192 passed`, with the existing Starlette deprecation warning and local pytest
cache permission warnings. This includes identity completion, credentials,
work-order/file ownership, notifications, resident relations, employee claim,
WeChat binding, and RBAC mutation paths. No database, API, page, queue, Qwen or
ASR request was made in this checkpoint.

The same independent read-only review context closed P14-R1-01 through
P14-R1-05 and returned `PASS_SCOPED` for the source and offline-test boundary.
The exact result is preserved at
`reports/codex/REAL-AI-INTEGRATION-027-PENDING0014-implementation-review.final.raw.json`.
It explicitly leaves MySQL concurrency/failure rollback, API/page behavior,
real proof, migration and Production acceptance unverified.

## 0014 and runtime boundary

The migration file hash remains
`D2CBF32AD9A865B65A67279D24006E4CEF141F21B77567C6247644E1F8FC020A`.
It creates six tables plus the separately described RBAC data delta. It was not
edited or executed.

`property-ai-mysql-test-1` is stopped, exposes no current 3307 listener, and its
MySQL data directory is tmpfs. The current AUTH001@0012 source therefore cannot
be proven present. Under the explicit instruction not to start or rebuild a
stopped tmpfs service to bypass unknown data, this session did not take a new
backup, create `property_ai_auth001_pre0014_restore_20260927`, execute 0014,
load 8083/Worker, or claim MySQL/API/page/SLA runtime evidence.

Real identity proof, isolated migration/restore validation, SLA MySQL/API/page
evidence, target deployment and formal release remain open external/runtime
conditions.
