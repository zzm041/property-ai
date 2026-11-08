"""No-Docker unit guards for the approved exact0012 isolated restore drill."""
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "scripts/ops/auth001_backup_restore_027.py"
spec = importlib.util.spec_from_file_location("restore027", SOURCE)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


@pytest.mark.parametrize("url,env", [
    ("mysql+pymysql://u:p@127.0.0.1:3307/property_ai_test", "test"),
    ("mysql+pymysql://u:p@127.0.0.1:3306/property_ai_auth001_test", "test"),
    ("mysql+pymysql://u:p@localhost:3307/property_ai_auth001_test", "test"),
    ("mysql+pymysql://u:p@127.0.0.1:3307/property_ai_auth001_test", "production"),
])
def test_wrong_source_is_rejected(url, env):
    with pytest.raises(ValueError):
        restore.validate_source_url(url, env)


def test_exact_source_and_new_destination(tmp_path):
    restore.validate_source_url(
        "mysql+pymysql://u:p@127.0.0.1:3307/property_ai_auth001_test", "test"
    )
    target = restore.destination("restore027-12345678", root=tmp_path)
    assert target.parent == tmp_path.resolve()
    target.mkdir()
    with pytest.raises(ValueError):
        restore.destination("restore027-12345678", root=tmp_path)
    assert restore.existing_destination("restore027-12345678", root=tmp_path) == target


@pytest.mark.parametrize("run_id", ["../x", "restore027-a", "rt026-12345678"])
def test_run_marker_cannot_escape_or_reuse_other_task(run_id, tmp_path):
    with pytest.raises(ValueError):
        restore.destination(run_id, root=tmp_path)


def test_dump_is_table_scoped_without_database_switch_or_global_objects():
    command = restore.dump_command()[-1]
    assert command.endswith("--user=root property_ai_auth001_test")
    assert "--databases" not in command and "--no-create-db" in command
    assert "--skip-add-drop-table" in command and "--skip-triggers" in command
    assert "$MYSQL_ROOT_PASSWORD" in command
    restore.reject_unsafe_dump(b"CREATE TABLE `x` (`id` int);\n-- Dump completed\n")
    for unsafe in (
        b"CREATE DATABASE bad;\nCREATE TABLE x(id int);\n-- Dump completed\n",
        b"USE property_ai_auth001_test;\nCREATE TABLE x(id int);\n-- Dump completed\n",
        b"CREATE TRIGGER bad BEFORE INSERT ON x FOR EACH ROW SET @x=1;\nCREATE TABLE x(id int);\n-- Dump completed\n",
    ):
        with pytest.raises(ValueError):
            restore.reject_unsafe_dump(unsafe)


def test_create_command_names_only_the_approved_target():
    command = restore.docker_mysql_script(
        "CREATE DATABASE `property_ai_auth001_restore_20260925` CHARACTER SET utf8mb4"
    )
    assert command[:3] == ["docker", "exec", "property-ai-mysql-test-1"]
    assert "property_ai_auth001_restore_20260925" in command[-1]
    assert "property_ai_auth001_test" not in command[-1]
    assert "--execute='CREATE DATABASE `property_ai_auth001_restore_20260925`" in command[-1]


def test_container_metadata_refuses_any_third_schema():
    with pytest.raises(ValueError):
        restore._metadata_via_container("property_ai_test")


def test_resume_never_substitutes_verification_snapshot_for_missing_baseline():
    source = SOURCE.read_text(encoding="utf-8")
    assert "source_before = source_after" not in source
    assert '"UNAVAILABLE_FOR_LEGACY_RESUME"' in source
    assert '"verification_time_source": source_after' in source
