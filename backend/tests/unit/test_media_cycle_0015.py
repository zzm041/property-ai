"""Contract tests for the scoped work-order media cycle migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / (
    "20261003_0015_work_order_media_cycle_links.py"
)


def test_0015_has_exact_lineage_and_no_legacy_backfill() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20261003_0015_work_order_media_cycle_links"' in source
    assert 'down_revision = "20260926_0014_identity_merge_sla_versions"' in source
    assert "UPDATE WORK_ORDER_MEDIA" not in source.upper()
    assert 'sa.Column("process_id", sa.BigInteger(), nullable=True)' in source
    assert 'sa.Column("receipt_id", sa.BigInteger(), nullable=True)' in source


def test_0015_defines_scoped_receipt_and_media_integrity() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "uq_work_order_receipts_id_process_scope" in source
    assert "fk_work_order_media_process_scope_work_order_processes" in source
    assert "fk_work_order_media_receipt_process_scope_work_order_receipts" in source
    assert "ix_work_order_media_work_order_process_id" in source
    assert "ix_work_order_media_receipt_id" in source


def test_0015_guard_names_only_the_approved_delivery_databases() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "property_ai_delivery_check" in source
    assert "property_ai_delivery_media_cycle_check" in source
    assert "PROPERTY_AI_APPROVE_0015_DATABASE" in source
    assert "PROPERTY_AI_APPROVE_0015_TARGET" in source
    assert "20260926_0014_identity_merge_sla_versions" in source
    assert "property_ai_auth001_test" not in source


def test_delivery_migrator_targets_the_fixed_revision_not_generic_head() -> None:
    source = (
        ROOT / "backend" / "app" / "cli" / "delivery_migrate.py"
    ).read_text(encoding="utf-8")
    assert 'HEAD = "20261111_0016_idempotency_key_scope"' in source
    assert "_alembic(HEAD)" in source
    assert '_alembic("head")' not in source
