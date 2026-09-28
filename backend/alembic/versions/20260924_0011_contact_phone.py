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
    target = f"{url.host}:{url.port}/{url.database}"
    auth001 = (os.environ.get("APP_ENV") == "test" and url.host == "127.0.0.1"
               and url.port == 3307 and url.database == "property_ai_auth001_test")
    delivery = (os.environ.get("PROPERTY_AI_DELIVERY_MIGRATION") == "approved"
                and os.environ.get("PROPERTY_AI_APPROVE_DELIVERY_TARGET") == target
                and os.environ.get("APP_ENV") == "staging" and url.host == "mysql"
                and url.port == 3306 and url.database == "property_ai_delivery_check")
    if not auth001 and not delivery:
        raise RuntimeError("Contact migration is approved only for isolated AUTH001 test")
    op.add_column("work_orders", sa.Column("contact_phone_e164", sa.String(16), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Contact snapshot deletion is not approved; preserve historical data")
