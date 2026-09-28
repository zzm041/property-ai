"""Static contract checks for the pending, never-auto-applied 0014 proposal."""

from pathlib import Path

from app.model import Base


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "backend/alembic/versions/20260926_0014_identity_merge_sla_versions.py"


def test_pending_0014_is_single_head_guarded_and_never_reuses_forbidden_ids() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "20260925_0012_task_intents"' in source
    assert 'revision = "20260926_0014_identity_merge_sla_versions"' in source
    assert "APP_ENV" in source and "property_ai_auth001_test" in source
    assert "PROPERTY_AI_APPROVE_0014_TARGET" in source
    assert "url.port in {3307, 3317}" in source
    assert 'url.host == "mysql"' in source
    assert 'url.port == 3306' in source
    assert 'approved_database == "property_ai_delivery_check"' in source
    assert "PROPERTY_AI_DELIVERY_MIGRATION" in source
    assert "0014 requires actual 0012" in source
    assert "op.drop_" not in source
    assert "uq_user_phones_active_e164" in source
    assert "uq_user_phones_active_user" in source
    assert "INSERT INTO work_order_sla_applications" not in source
    assert "Do not invent applied windows for pre-0014 work orders" in source


def test_3317_runner_requires_clean_recorded_runtime_before_any_seed_or_upgrade() -> None:
    source = (ROOT / "scripts" / "ops" / "migrate_auth001_0014.py").read_text(encoding="utf-8")
    main = source[source.index("def main()") :]
    preflight = main.index("_verify_retry_3317_preconditions()")
    seed = main.index("_prepare_synthetic_system_admin(engine)")
    upgrade = main.index("command.upgrade(config, AFTER)")
    assert preflight < seed < upgrade
    assert 'choices=(3307, 3317), required=True' in source
    assert "alembic_version_column\"][\"length\"] != 64" in source
    assert "retry._assert_clean_0012(metadata)" in source
    assert "retry._require_recorded_runtime()" in source


def test_pending_0014_metadata_captures_decided_identity_and_sla_contracts() -> None:
    tables = Base.metadata.tables
    recovery = tables["identity_recovery_cases"]
    assert {
        "purpose",
        "requester_user_id",
        "source_user_id",
        "target_user_id",
        "subject_phone_e164",
        "proof_reference_hash",
        "proof_result_hash",
        "quarantine_started_at",
        "quarantine_ends_at",
        "impact_hash",
        "attempt_count",
        "version",
    } <= set(recovery.c.keys())
    reviews = tables["identity_recovery_reviews"]
    assert {"case_id", "reviewer_user_id", "decision", "reason"} <= set(reviews.c.keys())
    tombstones = tables["phone_identity_tombstones"]
    assert "active_phone_key" in tombstones.c
    assert {constraint.name for constraint in tombstones.constraints} >= {"uq_phone_tombstones_active_phone"}
    versions = tables["sla_policy_versions"]
    assert {
        "accept_minutes",
        "respond_minutes",
        "complete_minutes",
        "warning_lead_minutes",
        "escalation_minutes",
        "change_reason",
        "effective_from",
        "effective_to",
        "created_by_user_id",
    } <= set(versions.c.keys())
    assert "policy_version_id" in Base.metadata.tables["work_order_sla_applications"].c
