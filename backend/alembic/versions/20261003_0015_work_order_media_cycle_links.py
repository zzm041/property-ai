"""Bind work-order media to its processing cycle and receipt.

Revision ID: 20261003_0015_work_order_media_cycle_links
Revises: 20260926_0014_identity_merge_sla_versions

Legacy media stays unlinked.  New linkage is created only by the application
while it owns the work-order transaction; this migration does not infer
historical relationships from timestamps.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from app.cli.customer_install_authority import permits_customer_install


revision = "20261003_0015_work_order_media_cycle_links"
down_revision = "20260926_0014_identity_merge_sla_versions"
branch_labels = None
depends_on = None


_LIVE_DATABASE = "property_ai_delivery_check"
_DRILL_DATABASE = "property_ai_delivery_media_cycle_check"


def _execution_guard() -> None:
    bind = op.get_bind()
    url = bind.engine.url
    actual_target = f"{url.host}:{url.port}/{url.database}"
    approved_database = os.environ.get("PROPERTY_AI_APPROVE_0015_DATABASE")
    approved_target = os.environ.get("PROPERTY_AI_APPROVE_0015_TARGET")
    exact_target = (
        os.environ.get("APP_ENV") == "staging"
        and os.environ.get("PROPERTY_AI_DELIVERY_MIGRATION") == "approved"
        and approved_database == url.database
        and approved_target == actual_target
        and url.drivername == "mysql+pymysql"
        and url.host == "mysql"
        and url.port == 3306
    )
    approved_mode = (
        url.database == _LIVE_DATABASE
        and os.environ.get("PROPERTY_AI_MEDIA_CYCLE_MIGRATION") == "approved"
    ) or (
        url.database == _DRILL_DATABASE
        and os.environ.get("PROPERTY_AI_MEDIA_CYCLE_DRILL") == "approved"
    )
    if (not exact_target or not approved_mode) and not permits_customer_install(bind, revision):
        raise RuntimeError("0015 requires an explicit exact delivery target approval")

    current = bind.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    if current != down_revision:
        raise RuntimeError("0015 requires actual 0014; no stamp or alternate head")

    required_tables = set(
        bind.execute(
            sa.text(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=:database AND TABLE_NAME IN "
                "('work_order_media','work_order_processes','work_order_receipts')"
            ),
            {"database": url.database},
        ).scalars()
    )
    if required_tables != {
        "work_order_media",
        "work_order_processes",
        "work_order_receipts",
    }:
        raise RuntimeError("0015 requires the complete 0014 work-order schema")


def upgrade() -> None:
    _execution_guard()
    op.create_unique_constraint(
        "uq_work_order_receipts_id_process_scope",
        "work_order_receipts",
        ["id", "process_id", "work_order_id", "tenant_id", "property_id"],
    )
    op.add_column(
        "work_order_media",
        sa.Column("process_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "work_order_media",
        sa.Column("receipt_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_work_order_media_process_scope_work_order_processes",
        "work_order_media",
        "work_order_processes",
        ["process_id", "work_order_id", "tenant_id", "property_id"],
        ["id", "work_order_id", "tenant_id", "property_id"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )
    op.create_foreign_key(
        "fk_work_order_media_receipt_process_scope_work_order_receipts",
        "work_order_media",
        "work_order_receipts",
        ["receipt_id", "process_id", "work_order_id", "tenant_id", "property_id"],
        ["id", "process_id", "work_order_id", "tenant_id", "property_id"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )
    op.create_index(
        "ix_work_order_media_work_order_process_id",
        "work_order_media",
        ["work_order_id", "process_id", "id"],
    )
    op.create_index(
        "ix_work_order_media_receipt_id",
        "work_order_media",
        ["receipt_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_order_media_receipt_id", table_name="work_order_media")
    op.drop_index(
        "ix_work_order_media_work_order_process_id",
        table_name="work_order_media",
    )
    op.drop_constraint(
        "fk_work_order_media_receipt_process_scope_work_order_receipts",
        "work_order_media",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_work_order_media_process_scope_work_order_processes",
        "work_order_media",
        type_="foreignkey",
    )
    op.drop_column("work_order_media", "receipt_id")
    op.drop_column("work_order_media", "process_id")
    op.drop_constraint(
        "uq_work_order_receipts_id_process_scope",
        "work_order_receipts",
        type_="unique",
    )
