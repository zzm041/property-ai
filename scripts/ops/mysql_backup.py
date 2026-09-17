"""Logical MySQL backup via docker exec."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mysqldump a database from a Docker MySQL container")
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    result = subprocess.run(
        [
            "docker",
            "exec",
            args.container,
            "mysqldump",
            "--single-transaction",
            "--routines",
            "--triggers",
            f"-u{args.user}",
            f"-p{args.password}",
            args.database,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr)
        return result.returncode
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.stdout, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
