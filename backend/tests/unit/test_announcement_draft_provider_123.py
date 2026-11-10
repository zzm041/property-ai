"""PA-123: an unusable draft provider is reported as itself, not as a bland notice.

Before the fix `create_draft(ai_draft=True)` wrapped the provider call in
`except (ValidationError, ValueError, Exception)`, so a dead adapter produced the
generic "Property staff are investigating" text and looked like a normal draft.
Narrowed behaviour:

* provider failure -> `ANN.DRAFT_PROVIDER_FAILED` (503) + `announcement_draft_provider_failed`;
* the model answering something unusable -> fall back to the neutral notice (kept);
* anything else is no longer swallowed at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.errors import AppError
from app.domain.ai import ANNOUNCEMENT_DRAFT
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.provider.errors import ProviderUnavailableError
from app.service.announcement import AnnouncementService


def _principal() -> Principal:
    return Principal(
        user_id=7,
        status="ACTIVE",
        display_name="Manager",
        grants=(
            RoleGrant(
                role_code="PROPERTY_ADMIN",
                scope=OrganizationScope(
                    ScopeType.PROPERTY, tenant_id=1, property_id=10
                ),
                permissions=frozenset({ANNOUNCEMENT_DRAFT}),
            ),
        ),
    )


def _service(llm) -> AnnouncementService:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    service = AnnouncementService(
        session, settings=MagicMock(), llm=llm, clock=lambda: None
    )
    service._work_orders = MagicMock()
    service._work_orders.get_property.return_value = MagicMock(id=10, tenant_id=1)
    service._audit = MagicMock()
    return service


def _create(service: AnnouncementService):
    return service.create_draft(
        actor=_principal(),
        property_id=10,
        request_id="req-pa123",
        ai_draft=True,
    )


def test_provider_failure_is_reported_instead_of_becoming_generic_text() -> None:
    llm = MagicMock()
    llm.structured_completion.side_effect = ProviderUnavailableError("LLM is unavailable")
    service = _service(llm)

    with patch("app.service.announcement.begin_owned_transaction"), patch(
        "app.service.announcement.logger"
    ) as log:
        with pytest.raises(AppError) as captured:
            _create(service)

    assert captured.value.code == "ANN.DRAFT_PROVIDER_FAILED"
    assert captured.value.status_code == 503
    events = [call.args[0] for call in log.error.call_args_list]
    assert "announcement_draft_provider_failed" in events


def test_unexpected_exceptions_are_no_longer_swallowed() -> None:
    llm = MagicMock()
    llm.structured_completion.side_effect = RuntimeError("boom")
    service = _service(llm)

    with patch("app.service.announcement.begin_owned_transaction"):
        with pytest.raises(RuntimeError, match="boom"):
            _create(service)
