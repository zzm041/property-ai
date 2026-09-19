"""Migration 0009 draft tests. Upgrade is not executed in this task."""

from pathlib import Path


def test_revision_does_not_occupy_0008_and_revises_0007() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/20260919_0009_official_auth_identity_dispatch.py"
    )
    text = path.read_text(encoding="utf-8")
    assert 'revision: str = "20260919_0009"' in text
    assert 'down_revision: str | None = "20260917_0007"' in text
    assert "0008" in text
    assert "user_phones" in text
    assert "employee_invites" in text
    assert "assigned_source" in text
