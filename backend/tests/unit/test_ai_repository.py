"""Dispatch-history repository boundaries used by automatic assignment."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.repository.ai import AiRepository


def _rows(values):
    result = MagicMock()
    result.all.return_value = values
    return result


def test_capability_queries_require_active_catalog_rows() -> None:
    session = MagicMock()
    session.execute.side_effect = (
        _rows([("MAINTENANCE",)]),
        _rows([("ELECTRICAL",)]),
    )
    repository = AiRepository(session)

    assert repository.list_employee_duties(7) == frozenset({"MAINTENANCE"})
    assert repository.list_employee_skills(7) == frozenset({"ELECTRICAL"})

    duty_statement = session.execute.call_args_list[0].args[0]
    skill_statement = session.execute.call_args_list[1].args[0]
    assert "duties.status" in str(duty_statement)
    assert "skills.status" in str(skill_statement)
    assert "ACTIVE" in duty_statement.compile().params.values()
    assert "ACTIVE" in skill_statement.compile().params.values()


def test_completion_history_is_attributed_to_employee_process_receipts() -> None:
    session = MagicMock()
    assigned_at = datetime(2026, 9, 20, tzinfo=UTC)
    session.execute.side_effect = (
        _rows([(assigned_at, assigned_at + timedelta(seconds=30))]),
        _rows([(101, True), (102, False), (103, None)]),
    )
    ratings = MagicMock()
    ratings.all.return_value = (5, 3)
    session.scalars.return_value = ratings
    repository = AiRepository(session)

    response, completion, rating = repository.get_dispatch_history(
        employee_id=7,
        tenant_id=1,
        property_id=1,
    )

    assert response == 30
    assert completion == pytest.approx(1 / 3)
    assert rating == 4
    completion_statement = session.execute.call_args_list[1].args[0]
    statement_text = str(completion_statement)
    assert "work_order_processes" in statement_text
    assert "work_order_receipts" in statement_text
    assert "work_orders.status" not in statement_text


def test_same_building_history_counts_resolved_employee_processes() -> None:
    session = MagicMock()
    session.execute.return_value = _rows([(21,), (22,)])
    repository = AiRepository(session)

    assert repository.count_same_building_history(
        employee_id=7,
        building_id=3,
        tenant_id=1,
        property_id=1,
    ) == 2

    statement = session.execute.call_args.args[0]
    statement_text = str(statement)
    assert "work_order_processes" in statement_text
    assert "work_order_receipts" in statement_text
    assert "assignee_id" in statement_text
    assert "resolved" in statement_text


def test_same_building_history_without_building_skips_query() -> None:
    session = MagicMock()

    assert AiRepository(session).count_same_building_history(
        employee_id=7,
        building_id=None,
        tenant_id=1,
        property_id=1,
    ) == 0
    session.execute.assert_not_called()
