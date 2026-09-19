from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schema.work_order import StaffReceiptRequest


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_receipt_fee_fields_are_consistent() -> None:
    no_fee = StaffReceiptRequest.model_validate(
        {
            "problem_type": "ELECTRICAL",
            "method": "Inspected and replaced the loose terminal",
            "resolved": True,
        }
    )
    recorded = StaffReceiptRequest.model_validate(
        {
            "problem_type": "ELECTRICAL",
            "method": "Inspected and replaced the loose terminal",
            "fee_flag": "RECORDED",
            "fee_amount": "12.50",
            "resolved": True,
        }
    )

    assert no_fee.fee_flag == "NONE"
    assert no_fee.fee_amount is None
    assert recorded.fee_amount == Decimal("12.50")

    invalid_bodies = (
        {
            "problem_type": "ELECTRICAL",
            "method": "Replace terminal",
            "fee_flag": "RECORDED",
            "resolved": True,
        },
        {
            "problem_type": "ELECTRICAL",
            "method": "Replace terminal",
            "fee_amount": "12.50",
            "resolved": True,
        },
        {
            "problem_type": "ELECTRICAL",
            "method": "Replace terminal",
            "fee_flag": "RECORDED",
            "fee_amount": "-1",
            "resolved": True,
        },
        {
            "problem_type": "ELECTRICAL",
            "method": "Replace terminal",
            "fee_flag": "UNSPECIFIED",
            "resolved": True,
        },
    )
    for body in invalid_bodies:
        with pytest.raises(ValidationError):
            StaffReceiptRequest.model_validate(body)


def test_staff_page_submits_complete_receipt_and_versions_mutations() -> None:
    source = (
        REPO_ROOT / "miniprogram/pages/staff-orders/detail.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/staff-orders/detail.wxml"
    ).read_text(encoding="utf-8")

    for field in (
        "problem_type",
        "method",
        "materials",
        "fee_flag",
        "fee_amount",
        "resolved",
        "follow_up",
        "file_ids",
        "version",
    ):
        assert field in source
    assert 'problem_type: "repair"' not in source
    assert 'method: "on_site"' not in source
    assert "处理回执" in template
    assert "处理方式与过程" in template
    assert "产生费用" in template


def test_resident_page_shows_receipt_and_versions_confirmation() -> None:
    source = (
        REPO_ROOT / "miniprogram/pages/work-orders/detail.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/work-orders/detail.wxml"
    ).read_text(encoding="utf-8")

    assert "latestReceipt" in source
    assert "confirmWorkOrder(this.id, { version: this.data.workOrder.version })" in source
    assert "处理回执" in template
    assert "latestReceipt.method" in template
    assert "latestReceipt.fee_amount" in template
