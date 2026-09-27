from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ops" / "auth001_retry_3317.py"


def _module():
    spec = importlib.util.spec_from_file_location("auth001_retry_3317", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retry_tool_is_bound_to_the_reviewed_3317_assets() -> None:
    module = _module()
    assert module.CONTAINER == "property-ai-auth001-retry-3317"
    assert module.VOLUME == "property-ai-auth001-retry-3317-data"
    assert module.HOST == "127.0.0.1"
    assert module.PORT == 3317
    assert module.DATABASE == "property_ai_auth001_test"
    assert module.REVISION == "20260925_0012_task_intents"
    assert module.REVISION_0014 == "20260926_0014_identity_merge_sla_versions"
    assert module.DUMP_BYTES == 749_523
    assert module.DUMP_SHA256 == "c5bf89eeefb272ac7313c59169ddc2e2807ffb854e9db566ce02bff5bf94322f"
    assert module.STATE == ROOT / "tmp" / "auth001-retry-3317"


def test_version_compatibility_ddl_only_widens_the_guarded_column() -> None:
    module = _module()
    column = {
        "data_type": "varchar",
        "length": 32,
        "nullable": "NO",
        "column_key": "PRI",
        "character_set": "utf8mb3",
        "collation": "utf8mb3_general_ci",
    }
    assert module._compatibility_ddl(column) == (
        "ALTER TABLE `alembic_version` MODIFY COLUMN `version_num` VARCHAR(64) "
        "CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL"
    )
    for key, value in (
        ("data_type", "text"),
        ("nullable", "YES"),
        ("column_key", ""),
        ("character_set", "latin1"),
        ("collation", "utf8mb4_0900_ai_ci"),
    ):
        invalid = dict(column)
        invalid[key] = value
        with pytest.raises(RuntimeError, match="column guards"):
            module._compatibility_ddl(invalid)


def test_clean_baseline_guard_rejects_partial_or_drifted_state() -> None:
    module = _module()
    clean = {
        "revision": module.REVISION,
        "table_count": 58,
        "new_0014_tables": [],
        "representative_counts": module.EXPECTED_COUNTS,
        "phone_uniques": module.PHONE_UNIQUES,
    }
    module._assert_clean_0012(clean)
    for key, value in (
        ("revision", "20260926_0014_identity_merge_sla_versions"),
        ("table_count", 64),
        ("new_0014_tables", ["account_merges"]),
        ("representative_counts", {**module.EXPECTED_COUNTS, "users": 16}),
        ("phone_uniques", ["uq_user_phones_active_e164"]),
    ):
        drifted = dict(clean)
        drifted[key] = value
        with pytest.raises(RuntimeError):
            module._assert_clean_0012(drifted)


def test_recorded_runtime_must_match_actual_fixed_container_assets() -> None:
    module = _module()
    manifest = {
        "container_id": "a" * 64,
    }
    inspected = {
        "Id": "a" * 64,
        "Name": f"/{module.CONTAINER}",
        "Config": {"Image": module.IMAGE},
        "State": {"Running": True},
        "Mounts": [
            {
                "Type": "volume",
                "Name": module.VOLUME,
                "Destination": "/var/lib/mysql",
            }
        ],
        "NetworkSettings": {
            "Ports": {
                "3306/tcp": [{"HostIp": module.HOST, "HostPort": str(module.PORT)}]
            }
        },
    }
    module._validate_runtime_inspect(manifest, inspected)
    drift_cases = (
        ("Id", "b" * 64),
        ("Name", "/unrelated"),
        ("Config", {"Image": "mysql:latest"}),
        ("State", {"Running": False}),
        ("Mounts", []),
        ("NetworkSettings", {"Ports": {"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3317"}]}}),
    )
    for key, value in drift_cases:
        changed = dict(inspected)
        changed[key] = value
        with pytest.raises(RuntimeError, match="fixed asset"):
            module._validate_runtime_inspect(manifest, changed)


def test_version_compatibility_evidence_is_immutable_after_apply(tmp_path, monkeypatch) -> None:
    module = _module()
    evidence = tmp_path / "compatibility.json"
    evidence.write_text(
        '{"target":"127.0.0.1:3317/property_ai_auth001_test","applied":true,'
        '"revision_unchanged":"20260925_0012_task_intents",'
        '"after":{"data_type":"varchar","length":64,"nullable":"NO",'
        '"column_key":"PRI","character_set":"utf8mb3","collation":"utf8mb3_general_ci"}}',
        encoding="utf-8",
    )
    metadata = {
        "revision": module.REVISION,
        "table_count": 58,
        "new_0014_tables": [],
        "representative_counts": module.EXPECTED_COUNTS,
        "phone_uniques": module.PHONE_UNIQUES,
        "server_uuid": "fixed",
        "alembic_version_column": {
            "data_type": "varchar",
            "length": 64,
            "nullable": "NO",
            "column_key": "PRI",
            "character_set": "utf8mb3",
            "collation": "utf8mb3_general_ci",
        },
    }
    monkeypatch.setattr(module, "COMPATIBILITY_EVIDENCE", evidence)
    monkeypatch.setattr(module, "_require_recorded_runtime", lambda: {})
    monkeypatch.setattr(module, "_metadata", lambda: metadata)
    before = evidence.read_bytes()
    result = module.apply_version_compatibility()
    assert result["original_evidence_preserved"] is True
    assert evidence.read_bytes() == before


def test_post0014_backup_run_id_is_bounded_before_runtime_access(monkeypatch) -> None:
    module = _module()
    touched = False

    def unexpected_runtime():
        nonlocal touched
        touched = True

    monkeypatch.setattr(module, "_require_recorded_runtime", unexpected_runtime)
    for invalid in ("", "baseline-20260928", "post0014-../escape", "post0014-with space"):
        with pytest.raises(RuntimeError, match="run id"):
            module.backup_post0014(invalid)
    assert touched is False
