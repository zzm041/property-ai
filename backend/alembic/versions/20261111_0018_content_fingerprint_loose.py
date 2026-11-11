"""Add the boundary-free content fingerprint used by the duplicate window.

Revision ID: 20261111_0018_content_fingerprint_loose
Revises: 20261111_0017_content_fingerprint
Create Date: 2026-11-11

PA-169 (t154): the strict fingerprint keeps punctuation as a token boundary, so
`厨房水管，漏水` and `厨房水管漏水` are different identities - correct for the
quarantine, wrong for "did this resident just file this report". The loose column
stores the boundary-free identity and is read only by the 10-minute duplicate
window. Existing rows stay NULL: the duplicate window is short-lived, so a
backfill would buy nothing and would have to re-implement NFKC folding in SQL.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261111_0018_content_fingerprint_loose"
down_revision = "20261111_0017_content_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("content_fingerprint_loose", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_work_orders_house_loose_created",
        "work_orders",
        ["house_id", "content_fingerprint_loose", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_orders_house_loose_created", table_name="work_orders")
    op.drop_column("work_orders", "content_fingerprint_loose")
