"""Explicit Development house fixture CLI. Separate from IAM Seed."""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.errors import AppError
from app.service.dev_house_fixture import run_development_house_fixture


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        result = run_development_house_fixture(get_settings())
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(
        "Development house fixture completed "
        f"house_id={result.house_id} property_id={result.property_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
