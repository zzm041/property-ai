# AUTH-IDENTITY-AI-001 schema review (draft)

## Revision
- File: `backend/alembic/versions/20260919_0009_official_auth_identity_dispatch.py`
- `revision = 20260919_0009`
- `down_revision = 20260917_0007`
- Does **not** occupy reserved **0008** (outbox/claim).
- If 0008 lands first: retarget 0009 to revise 0008. Otherwise later 0008 must revise 0009. Never sibling heads.

## New tables
| Table | Purpose | Key constraints |
| --- | --- | --- |
| `user_phones` | Verified E.164 phones | unique active phone; unique active phone per user; hashed-not-needed (phone is account locator, not secret) |
| `user_passwords` | One Argon2id hash per user | unique user_id |
| `sms_challenges` | OTP hash, TTL, attempts, consume | unique open (phone, purpose) |
| `auth_sessions` | Refresh hash, expiry, revoke | unique refresh hash |
| `employee_invites` | Opaque hashed invite | unique active invite per employee |

## Alters
| Change | Existing data | Notes |
| --- | --- | --- |
| `users.profile_completed_at` nullable | all NULL | ORM currently does not map this column so 0007 tests keep running |
| `employees.user_id` nullable | existing rows stay non-null | unique(user_id) still allows multiple unbound NULLs |
| `work_order_assignments.assigned_source` default HUMAN | backfill HUMAN | `assigned_by_user_id` nullable; CHECK HUMAN requires actor, AUTO requires NULL |
| `work_order_assignments.payload_json` | NULL | score/filter snapshot |
| `ai_call_logs.provider_mode` | backfill DEVELOPMENT | DEVELOPMENT vs PRODUCTION evidence split |

## Outbox / claim
No outbox table in 0009. Auto-assign currently runs in the analysis transaction. Durable enqueue remains the reserved 0008 plan.

## Apply / regress
- Do **not** upgrade `property_ai` or `property_ai_day4`.
- Disposable `TEST_DATABASE_URL` upgrade requires an explicit operator approval.
- Downgrade drops new tables and reverses alters; it will fail if unbound employees or AUTO assignments exist.

## ORM note
Assignment AUTO columns and `ai_call_logs.provider_mode` are in the draft SQL but not yet mapped, so current 0007 integration tests keep inserting assignments/call logs. Auto-dispatch returns `SCHEMA_PENDING_0009` until those columns are mapped after an approved upgrade.
