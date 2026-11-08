"""Explicit Development employee fixture CLI. Separate from IAM Seed."""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.errors import AppError
from app.service.dev_employee_fixture import run_development_employee_fixture


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        result = run_development_employee_fixture(get_settings())
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(
        "Development employee fixture completed "
        f"employee_id={result.employee_id} property_id={result.property_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
