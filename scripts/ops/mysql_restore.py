"""Restore a logical dump into a disposable MySQL database inside Docker."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

FORBIDDEN_DBS = {"property_ai"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore mysqldump into a Docker MySQL database")
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", required=True)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--create-database", action="store_true")
    parser.add_argument(
        "--allow-development-target",
        action="store_true",
        help="Required if --database is property_ai (development).",
    )
    args = parser.parse_args(argv)

    if args.database in FORBIDDEN_DBS and not args.allow_development_target:
        print(
            "Refusing to restore into development database property_ai. "
            "Use a disposable database (property_ai_restore_drill / staging) "
            "or pass --allow-development-target."
        )
        return 2

    dump = Path(args.dump)
    sql = dump.read_text(encoding="utf-8")
    if args.create_database:
        prepared = subprocess.run(
            [
                "docker",
                "exec",
                args.container,
                "mysql",
                f"-u{args.user}",
                f"-p{args.password}",
                "-e",
                (
                    f"CREATE DATABASE IF NOT EXISTS `{args.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if prepared.returncode != 0:
            print(prepared.stderr)
            return prepared.returncode

    restored = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            args.container,
            "mysql",
            f"-u{args.user}",
            f"-p{args.password}",
            args.database,
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if restored.returncode != 0:
        print(restored.stderr)
        return restored.returncode
    print(f"Restored {dump} into {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
