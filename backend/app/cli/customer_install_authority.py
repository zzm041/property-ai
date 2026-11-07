"""In-process permission limited to one approved empty-install connection.

No environment variable alone enables this branch in historical migrations.
The installer validates its plan and empty database before attaching authority
to Alembic Config. Normal Alembic/delivery commands retain their old guards.
"""
from dataclasses import dataclass
from typing import Any

from alembic import context


@dataclass
class CustomerInstallAuthority:
    connection: Any
    target: str
    revisions: frozenset[str]
    active: bool = True


def permits_customer_install(bind, revision: str) -> bool:
    config = getattr(context, "config", None)
    authority = getattr(config, "attributes", {}).get("customer_install_authority")
    if not isinstance(authority, CustomerInstallAuthority):
        return False
    url = bind.engine.url
    target = f"{url.host}:{url.port or 3306}/{url.database}"
    return (
        authority.active
        and authority.connection is bind
        and url.drivername == "mysql+pymysql"
        and authority.target == target
        and revision in authority.revisions
    )
