"""Explicit Development Seed CLI. Never invoked by app startup or migrations."""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.errors import AppError
from app.service.dev_seed import run_development_seed


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        run_development_seed(get_settings())
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print("Development seed completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
