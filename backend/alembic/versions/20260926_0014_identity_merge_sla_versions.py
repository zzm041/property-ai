"""Pending-review identity merge/recovery and immutable SLA provenance.

Revision ID: 20260926_0014_identity_merge_sla_versions
Revises: 20260925_0012_task_intents

This migration is intentionally not approved for execution by its creation.
The exact database name must be supplied in PROPERTY_AI_APPROVE_0014_DATABASE.
0010 and the reserved 0013 are not reused.
"""
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260926_0014_identity_merge_sla_versions"
down_revision = "20260925_0012_task_intents"
branch_labels = None
depends_on = None


def _execution_guard() -> None:
    bind = op.get_bind()
    url = bind.engine.url
    approved_database = os.environ.get("PROPERTY_AI_APPROVE_0014_DATABASE")
    if (
        os.environ.get("APP_ENV") != "test"
        or approved_database != "property_ai_auth001_test"
        or url.drivername != "mysql+pymysql"
        or url.database != approved_database
        or url.host != "127.0.0.1"
        or url.port != 3307
    ):
        raise RuntimeError("0014 requires an explicit exact local target approval")
    current = bind.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    if current != down_revision:
        raise RuntimeError("0014 requires actual 0012; no stamp or alternate head")
    phone_uniques = set(bind.execute(sa.text(
        "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=:db AND TABLE_NAME='user_phones' AND CONSTRAINT_TYPE='UNIQUE' "
        "AND CONSTRAINT_NAME IN ('uq_user_phones_active_e164','uq_user_phones_active_user')"
    ), {"db": url.database}).scalars())
    if phone_uniques != {"uq_user_phones_active_e164", "uq_user_phones_active_user"}:
        raise RuntimeError("0014 refuses to alter or proceed without both phone uniqueness guards")
    system_admins = bind.execute(sa.text("SELECT COUNT(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.code='SYSTEM_ADMIN'")).scalar_one()
    if not system_admins:
        raise RuntimeError("0014 requires an existing SYSTEM_ADMIN actor for baseline provenance")


def upgrade() -> None:
    _execution_guard()
    table_options = dict(
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_table(
        "identity_recovery_cases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("requester_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_user_id", sa.BigInteger(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_phone_e164", sa.String(16), nullable=False),
        sa.Column("proof_provider", sa.String(64), nullable=True),
        sa.Column("proof_reference_hash", sa.String(64), nullable=True),
        sa.Column("proof_result_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING_PROOF"),
        sa.Column("quarantine_started_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("quarantine_ends_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("proof_verified_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("review_completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("impact_json", sa.JSON(), nullable=True),
        sa.Column("impact_hash", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("active_subject_key", sa.String(16), sa.Computed("CASE WHEN BINARY status IN ('PENDING_PROOF','PENDING_REVIEW','APPROVED') THEN subject_phone_e164 ELSE NULL END", persisted=True), nullable=True),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], name="fk_identity_recovery_requester", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["source_user_id"], ["users.id"], name="fk_identity_recovery_source", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], name="fk_identity_recovery_target", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("purpose", "active_subject_key", name="uq_identity_recovery_active_subject"),
        sa.CheckConstraint("BINARY purpose IN ('MERGE_SOURCE_PROOF','ACCOUNT_RECOVERY','PHONE_REUSE')", name="purpose"),
        sa.CheckConstraint("BINARY status IN ('PENDING_PROOF','PENDING_REVIEW','APPROVED','REJECTED','EXPIRED','COMPLETED','CANCELLED')", name="status"),
        sa.CheckConstraint("expires_at > quarantine_started_at AND quarantine_ends_at >= quarantine_started_at", name="time_window"),
        sa.CheckConstraint("attempt_count >= 0 AND version >= 1", name="counters"),
        **table_options,
    )
    op.create_index("ix_identity_recovery_status_expiry", "identity_recovery_cases", ["status", "expires_at", "id"])
    op.create_index("ix_identity_recovery_source", "identity_recovery_cases", ["source_user_id", "id"])

    op.create_table(
        "identity_recovery_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["case_id"], ["identity_recovery_cases.id"], name="fk_identity_recovery_reviews_case", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], name="fk_identity_recovery_reviews_reviewer", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("case_id", "reviewer_user_id", name="uq_identity_recovery_distinct_reviewer"),
        sa.CheckConstraint("BINARY decision IN ('APPROVE','REJECT')", name="decision"),
        **table_options,
    )
    op.create_index("ix_identity_recovery_reviews_case", "identity_recovery_reviews", ["case_id", "id"])

    op.create_table(
        "phone_identity_tombstones",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("phone_e164", sa.String(16), nullable=False),
        sa.Column("source_user_id", sa.BigInteger(), nullable=False),
        sa.Column("recovery_case_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUARANTINED"),
        sa.Column("quarantine_until", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("released_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("claimed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("active_phone_key", sa.String(16), sa.Computed("CASE WHEN BINARY status IN ('QUARANTINED','RELEASED') THEN phone_e164 ELSE NULL END", persisted=True), nullable=True),
        sa.ForeignKeyConstraint(["source_user_id"], ["users.id"], name="fk_phone_tombstones_source_user", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["recovery_case_id"], ["identity_recovery_cases.id"], name="fk_phone_tombstones_recovery", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("active_phone_key", name="uq_phone_tombstones_active_phone"),
        sa.CheckConstraint("BINARY status IN ('QUARANTINED','RELEASED','CLAIMED')", name="status"),
        **table_options,
    )
    op.create_index("ix_phone_tombstones_history", "phone_identity_tombstones", ["phone_e164", "id"])
    op.create_index("ix_phone_tombstones_due", "phone_identity_tombstones", ["status", "quarantine_until", "id"])

    op.create_table(
        "account_merges",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("initiated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_phone_id", sa.BigInteger(), nullable=True),
        sa.Column("recovery_case_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="COMPLETED"),
        sa.Column("impact_json", sa.JSON(), nullable=True),
        sa.Column("impact_hash", sa.String(64), nullable=True),
        sa.Column("target_reauthenticated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("source_verified_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("cancelled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("completed_source_key", sa.BigInteger(), sa.Computed("CASE WHEN BINARY status = 'COMPLETED' THEN source_user_id ELSE NULL END", persisted=True), nullable=True),
        sa.ForeignKeyConstraint(["source_user_id"], ["users.id"], name="fk_account_merges_source_user", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], name="fk_account_merges_target_user", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"], name="fk_account_merges_initiator", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["source_phone_id"], ["user_phones.id"], name="fk_account_merges_source_phone", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["recovery_case_id"], ["identity_recovery_cases.id"], name="fk_account_merges_recovery", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("completed_source_key", name="uq_account_merges_completed_source"),
        sa.CheckConstraint("source_user_id <> target_user_id", name="different_users"),
        sa.CheckConstraint("BINARY status IN ('COMPLETED','CANCELLED')", name="status"),
        **table_options,
    )
    op.create_index("ix_account_merges_target", "account_merges", ["target_user_id", "id"])
    op.create_index("ix_account_merges_status", "account_merges", ["status", "id"])

    op.create_table(
        "sla_policy_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("policy_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("accept_minutes", sa.Integer(), nullable=False),
        sa.Column("respond_minutes", sa.Integer(), nullable=False),
        sa.Column("complete_minutes", sa.Integer(), nullable=False),
        sa.Column("warning_lead_minutes", sa.Integer(), nullable=False),
        sa.Column("escalation_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("change_reason", sa.String(255), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("effective_to", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["policy_id"], ["sla_policies.id"], name="fk_sla_policy_versions_policy", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_sla_policy_versions_creator", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("policy_id", "version_no", name="uq_sla_policy_versions_policy_version"),
        sa.CheckConstraint("accept_minutes >= 1 AND respond_minutes >= accept_minutes AND complete_minutes >= respond_minutes", name="ordered_deadlines"),
        sa.CheckConstraint("warning_lead_minutes >= 0 AND escalation_minutes >= 0", name="nonnegative_controls"),
        sa.CheckConstraint("BINARY status IN ('ACTIVE','DISABLED')", name="status"),
        sa.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="effective_window"),
        **table_options,
    )
    op.create_index("ix_sla_policy_versions_policy", "sla_policy_versions", ["policy_id", "version_no", "id"])

    op.create_table(
        "work_order_sla_applications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("work_order_id", sa.BigInteger(), nullable=False),
        sa.Column("policy_version_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("accept_minutes", sa.Integer(), nullable=False),
        sa.Column("respond_minutes", sa.Integer(), nullable=False),
        sa.Column("complete_minutes", sa.Integer(), nullable=False),
        sa.Column("warning_lead_minutes", sa.Integer(), nullable=False),
        sa.Column("escalation_minutes", sa.Integer(), nullable=False),
        sa.Column("applied_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], name="fk_work_order_sla_applications_work_order", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_version_id"], ["sla_policy_versions.id"], name="fk_work_order_sla_applications_version", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("work_order_id", name="uq_work_order_sla_applications_work_order"),
        sa.CheckConstraint("BINARY source IN ('PROPERTY','TENANT','CODE_DEFAULT')", name="source"),
        sa.CheckConstraint("accept_minutes >= 1 AND respond_minutes >= accept_minutes AND complete_minutes >= respond_minutes", name="ordered_deadlines"),
        sa.CheckConstraint("warning_lead_minutes >= 0 AND escalation_minutes >= 0", name="nonnegative_controls"),
        **table_options,
    )
    op.create_index("ix_work_order_sla_applications_version", "work_order_sla_applications", ["policy_version_id", "work_order_id"])

    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO sla_policy_versions "
        "(policy_id,version_no,accept_minutes,respond_minutes,complete_minutes,warning_lead_minutes,escalation_minutes,status,change_reason,created_by_user_id,effective_from,effective_to,created_at) "
        "SELECT p.id,1,p.respond_minutes,p.respond_minutes,p.complete_minutes,"
        "LEAST(p.complete_minutes,GREATEST(FLOOR(p.complete_minutes/4),15)),0,p.status,'0014 baseline from existing policy',"
        "(SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.code='SYSTEM_ADMIN' ORDER BY ur.id LIMIT 1),"
        "CURRENT_TIMESTAMP(6),NULL,CURRENT_TIMESTAMP(6) FROM sla_policies p"
    ))
    # Version 1 is a migration-time capture of the current legacy policy, not a
    # reconstruction of its historical values. The first SYSTEM_ADMIN is only
    # the required technical baseline actor; no historical editor is inferred.
    # Do not invent applied windows for pre-0014 work orders. Their accept and
    # response deadlines may already have been restarted at assignment/accept,
    # so subtracting created_at would not recover the original policy. Existing
    # clocks remain authoritative and old orders keep the explicit legacy path;
    # only work orders created after 0014 receive immutable applications.
    bind.execute(sa.text("INSERT INTO permissions (code,name,created_at,updated_at) SELECT 'identity:review','High-risk identity review',CURRENT_TIMESTAMP(6),CURRENT_TIMESTAMP(6) WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code='identity:review')"))
    bind.execute(sa.text("INSERT INTO role_permissions (role_id,permission_id,created_at) SELECT r.id,p.id,CURRENT_TIMESTAMP(6) FROM roles r JOIN permissions p ON p.code='identity:review' WHERE r.code='SYSTEM_ADMIN' AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id)"))


def downgrade() -> None:
    raise RuntimeError("0014 contains identity and SLA history; destructive downgrade is not approved")
