"""PA-167: the stored analysis keeps the dispatch skill code, and an unmapped
label is announced instead of being carried through silently."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.domain.ai import (
    SKILL_RESOLUTION_ALIASED,
    SKILL_RESOLUTION_CANONICAL,
    SKILL_RESOLUTION_UNMAPPED,
    SKILL_VALUE_NOT_NORMALIZED,
    WorkOrderAnalysisOutput,
    map_analysis_output,
    normalize_skill,
)
from app.model.ai import WorkOrderAnalysis
from app.service.analysis import AnalysisService


def output(**kw):
    return WorkOrderAnalysisOutput(
        **(
            dict(
                category="electrical",
                subcategory="插座冒火花",
                severity="high",
                priority="p2",
                summary="厨房插座冒火花，已断电，需要电工上门。",
                risk="electric",
                required_role="STAFF",
                required_skill="electrician",
                confidence=0.93,
            )
            | kw
        )
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("electrician", "ELECTRICAL"),
        ("ELECTRICIAN", "ELECTRICAL"),
        ("plumber", "PLUMBING"),
        ("hvac", "HVAC"),
        ("maintenance", "MAINTENANCE"),
    ],
)
def test_alias_resolution_maps_trade_labels_to_dispatch_codes(raw, expected):
    code, resolution = normalize_skill(raw)
    assert code == expected
    assert resolution == SKILL_RESOLUTION_ALIASED


@pytest.mark.parametrize("raw", ["ELECTRICAL", "PLUMBING", "MAINTENANCE", "OTHER", "other"])
def test_canonical_and_case_variants_are_recognised_without_rewriting(raw):
    code, resolution = normalize_skill(raw)
    assert code == raw.strip().upper()
    assert resolution == SKILL_RESOLUTION_CANONICAL


def test_unmapped_label_keeps_the_restriction_and_is_marked():
    code, resolution = normalize_skill("HAZMAT_LICENSE")
    assert code == "HAZMAT_LICENSE"
    assert resolution == SKILL_RESOLUTION_UNMAPPED
    # An unknown skill must never be softened into the unconstrained marker.
    assert code != "OTHER"


def test_whitespace_and_mixed_case_resolve_to_the_same_code():
    assert normalize_skill("  Electrician  ")[0] == "ELECTRICAL"
    assert normalize_skill("  ELECTRICAL  ")[1] == SKILL_RESOLUTION_CANONICAL


def test_stored_row_holds_the_code_dispatch_compares_against():
    mapped = map_analysis_output(output())
    assert mapped.required_skill == "ELECTRICAL"
    assert mapped.raw_required_skill == "electrician"
    assert mapped.skill_resolution == SKILL_RESOLUTION_ALIASED

    stored = WorkOrderAnalysis()
    AnalysisService._fill_analysis(stored, mapped)
    assert stored.required_skill == "ELECTRICAL"
    # The label is not lost: it travels on the mapped object and in the audit row.
    assert mapped.raw_required_skill == "electrician"


def test_stored_row_survives_remapping_unchanged():
    mapped = map_analysis_output(output())
    stored = WorkOrderAnalysis()
    AnalysisService._fill_analysis(stored, mapped)

    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None)
    reloaded = service._mapped_from_stored_analysis(stored)

    assert reloaded is not None
    assert reloaded.required_skill == mapped.required_skill == "ELECTRICAL"
    assert reloaded.required_duty == mapped.required_duty
    assert reloaded.low_confidence == mapped.low_confidence
    assert reloaded.skill_resolution == SKILL_RESOLUTION_CANONICAL


def test_unmapped_skill_is_written_once_in_the_analysis_audit():
    from app.service.audit import AuditService

    mapped = map_analysis_output(output(required_skill="HAZMAT_LICENSE"))
    service = AnalysisService.__new__(AnalysisService)
    repository = MagicMock()
    service._audit = AuditService(MagicMock(), repository=repository)
    service._append_success_audit(
        SimpleNamespace(id=2, tenant_id=3, property_id=4),
        SimpleNamespace(id=5, status="COMPLETED"),
        "pa167-audit",
        mapped,
    )
    details = repository.append.call_args.args[0].details_json
    assert details["skill_resolution"] == SKILL_RESOLUTION_UNMAPPED
    assert details["raw_required_skill"] == "HAZMAT_LICENSE"
    assert details["effective_required_skill"] == "HAZMAT_LICENSE"
    assert details["skill_mapping_applied"] is False


def test_aliased_skill_audit_records_both_vocabularies():
    from app.service.audit import AuditService

    mapped = map_analysis_output(output())
    service = AnalysisService.__new__(AnalysisService)
    repository = MagicMock()
    service._audit = AuditService(MagicMock(), repository=repository)
    service._append_success_audit(
        SimpleNamespace(id=2, tenant_id=3, property_id=4),
        SimpleNamespace(id=5, status="COMPLETED"),
        "pa167-audit-alias",
        mapped,
    )
    details = repository.append.call_args.args[0].details_json
    assert details["raw_required_skill"] == "electrician"
    assert details["effective_required_skill"] == "ELECTRICAL"
    assert details["skill_resolution"] == SKILL_RESOLUTION_ALIASED


def test_unmapped_payload_is_logged_and_still_accepted(monkeypatch):
    """Unknown labels are not silent, and not turned into a failed analysis."""
    from structlog.testing import capture_logs

    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="development")
    payload = output(required_skill="HAZMAT_LICENSE").model_dump()

    with capture_logs() as logs:
        mapped, error = service._validate_payload(payload, request_id="pa167-unmapped")

    assert error is None
    assert mapped is not None and mapped.required_skill == "HAZMAT_LICENSE"
    events = [item for item in logs if item["event"] == "analysis_skill_not_normalized"]
    assert len(events) == 1
    assert events[0]["reason_code"] == SKILL_VALUE_NOT_NORMALIZED
    assert events[0]["raw_required_skill"] == "HAZMAT_LICENSE"


def test_known_label_is_not_logged_as_unmapped():
    from structlog.testing import capture_logs

    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="development")

    with capture_logs() as logs:
        mapped, error = service._validate_payload(
            output().model_dump(), request_id="pa167-alias"
        )

    assert error is None and mapped.required_skill == "ELECTRICAL"
    assert [item for item in logs if item["event"] == "analysis_skill_not_normalized"] == []
