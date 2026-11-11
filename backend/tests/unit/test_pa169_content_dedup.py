"""PA-169 round 3 (t150): the duplicate window is scoped and the fingerprint sticks.

Two behaviours, both about content identity at the create boundary:

* the repository finds an earlier report by (house, reporter, fingerprint, window)
* the service answers with that report instead of inserting a second one, and a
  fresh order stores the fingerprint so the next attempt can find it
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from app.domain.ai import content_fingerprint, loose_content_fingerprint
from app.repository.work_order import WorkOrderRepository
from app.service import work_order as wo
from app.service.work_order import DUPLICATE_WINDOW_MINUTES, WorkOrderService

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
REPORT_TEXT = "3号楼2单元楼道灯不亮"
FINGERPRINT = content_fingerprint(REPORT_TEXT)
LOOSE_FINGERPRINT = loose_content_fingerprint(REPORT_TEXT)


# --------------------------------------------------------------- repository
def test_the_duplicate_lookup_is_one_indexed_scoped_read():
    statements = []

    class _Session:
        def scalar(self, statement):
            statements.append(statement)
            return None

    repository = WorkOrderRepository(_Session())

    assert (
        repository.find_duplicate_content(
            house_id=1,
            reporter_user_id=6,
            content_fingerprint=FINGERPRINT,
            content_fingerprint_loose=LOOSE_FINGERPRINT,
            since=NOW - timedelta(minutes=DUPLICATE_WINDOW_MINUTES),
        )
        is None
    )

    compiled = str(statements[0].compile(compile_kwargs={"literal_binds": False}))
    assert "LIMIT" in compiled.upper()
    assert "work_orders.house_id =" in compiled
    # Two residents reporting the same thing are two reports.
    assert "work_orders.reporter_user_id =" in compiled
    assert "work_orders.content_fingerprint =" in compiled
    assert "work_orders.created_at >=" in compiled


# ------------------------------------------------------------------- service
def _service(*, duplicate=None, fingerprint_of=None):
    service = object.__new__(WorkOrderService)
    service._session = MagicMock()
    service._settings = NS()
    service._clock = lambda: NOW
    service._last_create_deduplicated = False
    service._reload_actor = MagicMock(return_value=NS(user_id=6))
    service._request_hash = MagicMock(return_value="request-hash")
    service._optional_text = lambda text: (text or "").strip() or None
    service._parse_enum = MagicMock(side_effect=lambda raw, enum, default, _name: default)
    service._sla_versioning_available = MagicMock(return_value=False)
    service._attach_media = MagicMock()
    service._write_log = MagicMock()
    house = NS(id=1, tenant_id=5, property_id=5)
    service._residents = MagicMock()
    service._residents.find_active_relation.return_value = house
    service._residents.get_active_house_for_user.return_value = house
    captured = {"order": None, "seen": {}}

    def _find_duplicate_content(**kwargs):
        captured["seen"] = kwargs
        return duplicate

    service._repository = MagicMock()
    service._repository.get_idempotency.return_value = None
    service._repository.find_duplicate_content.side_effect = _find_duplicate_content
    service._repository.find_sla_policy.return_value = None
    service._repository.get_file.side_effect = lambda file_id: NS(content_hash=f"h{file_id}")

    def _add_work_order(record):
        captured["order"] = record
        return record

    service._repository.add_work_order.side_effect = _add_work_order
    service._append_audit = MagicMock()
    service._notify_users = MagicMock()
    return service, captured


def _create(service, *, text=REPORT_TEXT):
    return service._create_work_order(
        actor=NS(user_id=6),
        house_id=1,
        request_id="req-150",
        idempotency_key="idem-150",
        contact_phone="+8613800000006",
        text=text,
        file_ids=(),
        category=None,
        priority=None,
        severity=None,
        claimed_tenant_id=None,
        claimed_property_id=None,
    )


def test_the_same_report_within_ten_minutes_returns_the_existing_order(monkeypatch):
    existing = NS(id=39, version=2)
    service, captured = _service(duplicate=existing)
    monkeypatch.setattr(
        "app.service.identity.identity_write_guard.lock_active_identity", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission", lambda *a, **kw: None
    )

    record, created = _create(service)

    assert (record, created) == (existing, False)
    assert service.last_create_deduplicated is True
    # The window is measured from the injected clock, not from wall time.
    assert captured["seen"]["since"] == NOW - timedelta(minutes=DUPLICATE_WINDOW_MINUTES)
    assert captured["seen"]["content_fingerprint"] == FINGERPRINT
    assert captured["seen"]["house_id"] == 1
    assert captured["seen"]["reporter_user_id"] == 6
    # Nothing was inserted behind the duplicate answer.
    service._repository.add_work_order.assert_not_called()


def test_a_new_report_stores_its_fingerprint_for_the_next_attempt(monkeypatch):
    service, captured = _service(duplicate=None)
    monkeypatch.setattr(
        "app.service.identity.identity_write_guard.lock_active_identity", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission", lambda *a, **kw: None
    )

    _record, created = _create(service)

    assert created is True
    assert service.last_create_deduplicated is False
    assert captured["order"].content_fingerprint == FINGERPRINT


def test_the_fingerprint_covers_attached_media_by_content(monkeypatch):
    service, captured = _service(duplicate=None)
    monkeypatch.setattr(
        "app.service.identity.identity_write_guard.lock_active_identity", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission", lambda *a, **kw: None
    )

    service._create_work_order(
        actor=NS(user_id=6),
        house_id=1,
        request_id="req-150",
        idempotency_key="idem-150",
        contact_phone="+8613800000006",
        text=REPORT_TEXT,
        file_ids=(7, 8),
        category=None,
        priority=None,
        severity=None,
        claimed_tenant_id=None,
        claimed_property_id=None,
    )

    # File order must not matter; the digests are sorted inside the contract.
    assert captured["order"].content_fingerprint == content_fingerprint(
        REPORT_TEXT, ["h7", "h8"]
    )
    assert captured["order"].content_fingerprint == content_fingerprint(
        REPORT_TEXT, ["h8", "h7"]
    )


def test_the_window_constant_is_ten_minutes():
    assert wo.DUPLICATE_WINDOW_MINUTES == 10


# --------------------------------------------- t154: the two-tier design
def test_punctuation_does_not_hide_a_repeat(monkeypatch):
    monkeypatch.setattr(
        "app.service.identity.identity_write_guard.lock_active_identity", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission", lambda *a, **kw: None
    )
    service, captured = _service(duplicate=NS(id=88))

    duplicate, created = _create(service, text="厨房水管，漏水")

    assert (duplicate.id, created) == (88, False)
    # The strict form keeps the comma, the loose form is the one a differing
    # comma still satisfies - both are handed to the single lookup.
    assert captured["seen"]["content_fingerprint"] == content_fingerprint("厨房水管，漏水")
    assert captured["seen"]["content_fingerprint_loose"] == loose_content_fingerprint(
        "厨房水管漏水"
    )
    assert captured["seen"]["content_fingerprint"] != content_fingerprint("厨房水管漏水")

    service_2, captured_2 = _service(duplicate=None)
    _create(service_2, text="厨房水管，漏水")
    assert captured_2["order"].content_fingerprint == content_fingerprint("厨房水管，漏水")
    assert (
        captured_2["order"].content_fingerprint_loose
        == loose_content_fingerprint("厨房水管漏水")
    )


def test_the_quarantine_keeps_the_strict_form_so_opposites_stay_apart():
    from app.domain.ai import content_fingerprint as strict

    assert strict("不需要维修") != strict("不需要，维修")
    assert loose_content_fingerprint("不需要维修") == loose_content_fingerprint(
        "不需要，维修"
    )
    # Isolation reads the strict column only.
    import inspect

    from app.service.analysis import orchestrator as orch

    source = inspect.getsource(orch.AnalysisService._quarantine_for)
    assert "loose" not in source


def test_the_negation_table_is_gone():
    import inspect

    from app.domain import ai as ai_domain

    source = inspect.getsource(ai_domain.rules) if hasattr(ai_domain, "rules") else ""
    if not source:
        from app.domain.ai import rules as rules_module

        source = inspect.getsource(rules_module)
    assert "_FINGERPRINT_NEGATIONS" not in source
    assert "_starts_with_negation" not in source
    assert "_FINGERPRINT_TOKEN_BOUNDARY" in source


def test_the_queue_endpoint_publishes_system_note():
    from app.main import app

    spec = app.openapi()
    operation = spec["paths"]["/api/v1/supervisor/work-orders"]["get"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "SuccessEnvelope" in str(response.get("$ref", ""))
    assert "system_note" in spec["components"]["schemas"]["WorkOrderData"]["properties"]
