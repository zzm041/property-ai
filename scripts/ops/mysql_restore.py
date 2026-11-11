"""Restore a logical dump into a disposable MySQL database inside Docker."""

from __future__ import annotations

import subprocess
from pathlib import Path
from mysql_tool_common import (
    SYSTEM_DATABASES, SafeArgumentParser, add_credentials, credential_environment, docker_mysql_command,
)

FORBIDDEN_DBS = {"property_ai"}


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description="Restore a trusted dump into a disposable Docker MySQL database")
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="root")
    add_credentials(parser)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--create-database", action="store_true")
    parser.add_argument(
        "--allow-development-target",
        action="store_true",
        help="Required if --database is property_ai (development).",
    )
    args = parser.parse_args(argv)

    try:
        environment = credential_environment(args)
    except ValueError as exc:
        print(str(exc))
        return 2
    if args.database.lower() in SYSTEM_DATABASES:
        print('Refusing to restore into a MySQL system database.')
        return 2
    if args.database.lower() in FORBIDDEN_DBS and not args.allow_development_target:
        print(
            "Refusing to restore into development database property_ai. "
            "Use a disposable database (property_ai_restore_drill / staging) "
            "or pass --allow-development-target."
        )
        return 2

    dump = Path(args.dump)
    try:
        sql = dump.read_bytes()
    except OSError:
        print('Cannot read the trusted dump file; no database command executed.')
        return 2
    if args.create_database:
        try:
            prepared = subprocess.run(
                docker_mysql_command(args, 'mysql') + ['-e', (
                    f'CREATE DATABASE IF NOT EXISTS `{args.database}` '
                    'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')],
                capture_output=True, check=False, env=environment, timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            print('Database preparation failed or timed out. Verify target state before retrying.')
            return 1
        if prepared.returncode != 0:
            print('Database preparation failed; diagnostic content withheld.')
            return prepared.returncode

    try:
        restored = subprocess.run(
            docker_mysql_command(args, 'mysql', stdin=True) + [args.database],
            input=sql, capture_output=True, check=False, env=environment, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        print('Restore failed or timed out; target may be partially changed. Do not automatically retry.')
        return 1
    if restored.returncode != 0:
        print('Restore failed; target may be partially changed. Diagnostic content withheld.')
        return restored.returncode
    print(f"Restored {dump} into {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
