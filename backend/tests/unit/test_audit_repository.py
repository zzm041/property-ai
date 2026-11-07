"""Append-only Audit Repository interface and transaction behavior tests."""

from unittest.mock import MagicMock

import pytest

from app.model.identity import AuditLog
from app.repository.audit import AuditRepository


def test_append_adds_and_flushes_without_transaction_control() -> None:
    session = MagicMock()
    repository = AuditRepository(session)
    record = AuditLog(
        action="DEV_AUTH_TOKEN_ISSUED",
        resource_type="USER",
        resource_id=1,
        request_id="repository-append",
        details_json={"auth_method": "development", "token_type": "access"},
    )

    result = repository.append(record)

    assert result is record
    session.add.assert_called_once_with(record)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_repository_exposes_no_normal_update_delete_or_unscoped_get_all() -> None:
    forbidden_methods = {
        "update",
        "delete",
        "remove",
        "save",
        "save_changes",
        "get_all",
        "list_all",
    }

    assert forbidden_methods.isdisjoint(dir(AuditRepository))


@pytest.mark.parametrize(
    ("after_id", "limit"),
    [(-1, 100), (True, 100), (0, 0), (0, 501), (0, True)],
)
def test_scoped_reads_reject_invalid_pagination(
    after_id: int,
    limit: int,
) -> None:
    repository = AuditRepository(MagicMock())

    with pytest.raises(ValueError):
        repository.list_system_scope(after_id=after_id, limit=limit)
