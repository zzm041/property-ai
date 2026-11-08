from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.service.auto_dispatch import AutoDispatchService


@pytest.mark.parametrize('status', ['REJECTED', 'REASSIGNED'])
@pytest.mark.parametrize('elapsed,excluded', [(599, True), (601, False)])
def test_default_cooldown_excludes_only_first_ten_minutes(monkeypatch, status, elapsed, excluded):
    monkeypatch.delenv('DISPATCH_REJECT_COOLDOWN_SECONDS', raising=False)
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    monkeypatch.setattr('app.service.auto_dispatch.utc_now', lambda: now)
    service = AutoDispatchService.__new__(AutoDispatchService)
    service._settings = Settings(_env_file=None)
    service._work_orders = SimpleNamespace()
    assignment = SimpleNamespace(employee_id=4, status=status, ended_at=now-timedelta(seconds=elapsed), timeout_at=now)
    result = service._recently_rejected_employee_ids(SimpleNamespace(id=23), assignments=(assignment,))
    assert (4 in result) is excluded


def test_explicit_cooldown_override_remains_supported(monkeypatch):
    monkeypatch.setenv('DISPATCH_REJECT_COOLDOWN_SECONDS', '900')
    assert Settings(_env_file=None).dispatch_reject_cooldown_seconds == 900
