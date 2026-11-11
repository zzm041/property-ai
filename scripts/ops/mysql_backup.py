"""Logical MySQL backup via docker exec."""

from __future__ import annotations

import subprocess
from pathlib import Path
from mysql_tool_common import SafeArgumentParser, add_credentials, credential_environment, docker_mysql_command


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description="mysqldump a database from a Docker MySQL container")
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="root")
    add_credentials(parser)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        environment = credential_environment(args)
    except ValueError as exc:
        print(str(exc))
        return 2
    out = Path(args.out)
    if out.exists() or out.is_symlink():
        print('Refusing to overwrite an existing backup path.')
        return 2
    try:
        result = subprocess.run(
            docker_mysql_command(args, 'mysqldump') + [
                '--single-transaction', '--routines', '--triggers', args.database],
            capture_output=True, check=False, env=environment, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        print('Backup command failed or timed out; no dump written. Details withheld.')
        return 1
    if result.returncode != 0:
        print('Backup command failed; diagnostic content withheld.')
        return result.returncode
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('xb') as output:
            output.write(result.stdout)
    except OSError:
        print('Backup file could not be completed; an existing or partial file is retained.')
        return 1
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
