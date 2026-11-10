"""Give the input quarantine a content identity.

PA-169 (t150): the quarantine used to read `(work_order_id, input_hash)`, but
`input_hash` covers the work order id and its context. A resident who re-files
the same report therefore produced a brand new work order, a brand new hash and
no quarantine at all — exactly the case the guard exists for. `input_hash` keeps
its meaning (one attempt on one work order); the new `content_fingerprint`
column carries the content-only identity that the quarantine follows, and the
same value on `work_orders` lets the create path recognise a repeat inside the
dedup window without re-reading report text.

Both columns are nullable: existing rows keep working with no backfill, and a
NULL fingerprint never matches a lookup.

Revision ID: 20261111_0017_content_fingerprint
Revises: 20261111_0016_idempotency_key_scope
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20261111_0017_content_fingerprint"
down_revision = "20261111_0016_idempotency_key_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_work_orders_house_fingerprint_created",
        "work_orders",
        ["house_id", "content_fingerprint", "created_at"],
        unique=False,
    )
    op.add_column(
        "ai_call_logs",
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_ai_call_logs_content_fingerprint",
        "ai_call_logs",
        ["content_fingerprint", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_call_logs_content_fingerprint", table_name="ai_call_logs")
    op.drop_column("ai_call_logs", "content_fingerprint")
    op.drop_index(
        "ix_work_orders_house_fingerprint_created",
        table_name="work_orders",
    )
    op.drop_column("work_orders", "content_fingerprint")
