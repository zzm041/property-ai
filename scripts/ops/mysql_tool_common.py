"""Small credential and identifier boundary for local MySQL operator tools."""
from __future__ import annotations

import argparse
import os
import re

SYSTEM_DATABASES = frozenset({'mysql', 'sys', 'information_schema', 'performance_schema'})


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default error may echo an accidentally supplied credential.
        self.exit(2, 'Invalid arguments; use --help. Argument values were withheld.\n')


def add_credentials(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--password-env', default='PROPERTY_AI_MYSQL_PASSWORD',
                        help='Name of the environment variable containing the password.')
    parser.add_argument('--password', help=argparse.SUPPRESS)


def credential_environment(args: argparse.Namespace) -> dict[str, str]:
    if args.password is not None:
        raise ValueError('The --password option is disabled. Use --password-env instead.')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.password_env):
        raise ValueError('Invalid password environment variable name.')
    password = os.environ.get(args.password_env)
    if not password or '\x00' in password:
        raise ValueError('Password environment variable is missing or invalid.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', args.container):
        raise ValueError('Invalid container name or ID.')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', args.database):
        raise ValueError('Invalid database identifier.')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}', args.user):
        raise ValueError('Invalid database user.')
    # Docker resolves the value from this child's environment; no value in argv.
    return dict(os.environ, MYSQL_PWD=password)


def docker_mysql_command(args: argparse.Namespace, executable: str, *, stdin: bool = False) -> list[str]:
    return ['docker', 'exec', '-e', 'MYSQL_PWD', *(['-i'] if stdin else []),
            args.container, executable, '--user='+args.user]
