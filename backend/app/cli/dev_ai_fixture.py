"""Explicit Development Task 7 fixture CLI."""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.errors import AppError
from app.service.dev_ai_fixture import run_development_ai_fixture


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        result = run_development_ai_fixture(get_settings())
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(
        "Development AI fixture completed "
        f"employee_id={result.employee_id} plumbing_skill_id={result.plumbing_skill_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
