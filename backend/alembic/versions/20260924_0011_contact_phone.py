"""Approved isolated contact snapshot only; no ACCOUNT-MERGE / 0010.

Revision ID: 20260924_0011_contact_phone
Revises: 20260919_0009
"""
import os

from alembic import op
import sqlalchemy as sa

revision = "20260924_0011_contact_phone"
down_revision = "20260919_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    url = op.get_bind().engine.url
    if (os.environ.get("APP_ENV") != "test" or url.host != "127.0.0.1"
            or url.port != 3307 or url.database != "property_ai_auth001_test"):
        raise RuntimeError("Contact migration is approved only for isolated AUTH001 test")
    op.add_column("work_orders", sa.Column("contact_phone_e164", sa.String(16), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Contact snapshot deletion is not approved; preserve historical data")
