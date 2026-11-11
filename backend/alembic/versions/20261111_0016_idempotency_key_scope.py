"""Scope idempotency keys to their owning user.

D1: `idempotency_records` was unique on `idempotency_key` alone while every
lookup filters `(user_id, idempotency_key)`.  A key chosen by one user therefore
blocked every other user's creation with the same key (409) and revealed that
the key existed.  The uniqueness now matches the lookup.

Downgrade restores the global constraint and is refused while two users share a
key, because collapsing rows would destroy replay evidence.

Revision ID: 20261111_0016_idempotency_key_scope
Revises: 20261003_0015_work_order_media_cycle_links
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20261111_0016_idempotency_key_scope"
down_revision = "20261003_0015_work_order_media_cycle_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_idempotency_records_key",
        "idempotency_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_idempotency_records_user_key",
        "idempotency_records",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT idempotency_key
                FROM idempotency_records
                GROUP BY idempotency_key
                HAVING COUNT(*) > 1
            ) AS shared
            """
        )
    ).scalar_one()
    if duplicate:
        raise RuntimeError(
            "Cannot restore the globally unique idempotency key while "
            f"{duplicate} key(s) are shared by more than one user"
        )
    op.drop_constraint(
        "uq_idempotency_records_user_key",
        "idempotency_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_idempotency_records_key",
        "idempotency_records",
        ["idempotency_key"],
    )
