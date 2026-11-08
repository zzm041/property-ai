"""Explicit Development WorkOrder fixture CLI. Separate from IAM Seed."""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.errors import AppError
from app.service.dev_work_order_fixture import run_development_work_order_fixture


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        result = run_development_work_order_fixture(get_settings())
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(
        "Development work-order fixture completed "
        f"house_id={result.house_id} relation_id={result.relation_id} "
        f"sla_policies={result.sla_policy_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
