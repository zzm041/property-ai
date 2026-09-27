# Post-0012 identity recovery and SLA provenance — final DDL for review

Status: **PENDING EXECUTION APPROVAL — NOT APPLIED**
Proposed revision: `20260926_0014_identity_merge_sla_versions`
Parent: `20260925_0012_task_intents`
Original reviewed migration SHA-256 (3307-only guard):
`D2CBF32AD9A865B65A67279D24006E4CEF141F21B77567C6247644E1F8FC020A`

The owner subsequently approved a guard-only adaptation for the clean retry
target at `127.0.0.1:3317/property_ai_auth001_test`. The business DDL,
revision and parent are unchanged. The adapted file has a different byte hash;
its preflight SHA-256 is
`8DF73D9474E0BE7B0ACCFD5085BD635B55AF9C7CFA7362EA80844863DEBC8B55`.
It is not represented as byte-identical to the original.

The executable DDL is the migration file at
`backend/alembic/versions/20260926_0014_identity_merge_sla_versions.py`.
This document records its intended boundary; it is not permission to run it.

## Exact structural delta

The proposal creates six tables and no columns on existing business tables:

1. `identity_recovery_cases`: purpose, requester/source/target user references,
   subject phone, opaque proof hashes, 30-day quarantine dates, review/expiry/
   completion dates, redacted impact/hash, attempt/version counters, and a
   generated active-subject key. Unique `(purpose, active_subject_key)` blocks
   parallel active cases for the same number.
2. `identity_recovery_reviews`: case/reviewer/decision/reason with unique
   `(case_id, reviewer_user_id)`, enforcing distinct reviewers at storage level.
3. `phone_identity_tombstones`: appendable phone/source history and lifecycle.
   Generated `active_phone_key` permits only one QUARANTINED/RELEASED lifecycle
   at a time while allowing prior CLAIMED records to remain as history.
4. `account_merges`: terminal source/target/operator/recovery ledger, redacted
   impact/hash, explicit target reauthentication and source-proof timestamps,
   and a unique completed-source guard. It does not copy a source password.
5. `sla_policy_versions`: immutable numbered policy values for accept, respond,
   complete, warning lead and escalation grace, including actor, reason and
   effective interval.
6. `work_order_sla_applications`: one immutable policy/version snapshot per
   work order. New orders use it for accept, response, warning and escalation;
   later policy changes cannot rewrite the snapshot.

Separately, existing RBAC tables receive only the new `identity:review`
permission and a SYSTEM_ADMIN grant when absent. This RBAC data delta is the
previously described seventh migration item; it is not a seventh table.

Both existing phone uniqueness constraints are mandatory preconditions and are
neither dropped nor changed:

- `uq_user_phones_active_e164`
- `uq_user_phones_active_user`

The migration does not create or execute 0010/0013, edit historical revisions,
alter 3307 bindings, or touch any other database.

## Existing-data effect

- Existing SLA policies receive a migration-time version-1 capture using their
  then-current stored response/completion values; accept equals the prior
  response window. Its `effective_from` is the migration time, not the policy
  creation time, and the required SYSTEM_ADMIN actor is only a technical
  baseline attribution rather than a claim about a historical editor.
- Existing work orders receive no invented application snapshot. Assignment and
  response deadlines may already have been restarted, so subtracting
  `created_at` cannot recover an original policy. Their existing `sla_clocks`
  values remain authoritative and their later assignment resets keep the
  explicit legacy policy path. Only post-0014 work orders receive immutable
  applied-policy snapshots.
- Identity tables receive no fabricated cases, reviews, tombstones or merges.
- No old phone, credential, user, work-order or audit row is rewritten.
- Downgrade deliberately refuses destructive deletion because these tables
  become identity/audit history once used.

## Execution guard and proposed first verification

The migration refuses unless all of these are true: `APP_ENV=test`, PyMySQL,
host `127.0.0.1`, port in the explicitly reviewed set (`3307` or the clean
retry `3317`), database equals `property_ai_auth001_test`, a separate approval
variable equals the complete actual host/port/database target, actual Alembic
head equals 0012, both phone unique constraints exist, and at least one
SYSTEM_ADMIN exists. The execution runner supplies the exact target variable;
`APP_ENV=test` by itself is insufficient.

If execution is separately approved, the first run should be AUTH001-only after
a fresh container-external backup. Verify: revision/head and all constraints;
unchanged pre-existing table counts/hashes; zero fabricated historical work-order
applications and unchanged existing clocks; two-reviewer uniqueness and
current-authority recheck; 30-day
quarantine; generic SMS rejection for ended phone history; credential/session/
ticket invalidation and all account-ownership collision cases before enabling
merge completion. A disposable restore is required for structural recovery
practice; the live restore database and other schemas remain untouched.

## Functionality boundary before execution

When these tables are absent, identity recovery remains explicitly disabled and
legacy SLA behavior remains compatible. The account-ownership completion
transaction is now implemented and unit-verified, but it still requires this
schema and a trusted identity-proof provider before it is operational. The
current code stores no raw ID document, face image or biometric template and
does not treat SMS as identity proof.
