"""One-time, fail-closed SYSTEM_ADMIN bootstrap for a new delivery database."""
from __future__ import annotations

import os
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import begin_owned_transaction, create_database_engine
from app.core.passwords import hash_password, require_new_password
from app.core.time import utc_now
from app.domain.dev_seed import SEED_PERMISSIONS, SEED_ROLE_PERMISSIONS, SEED_ROLES
from app.domain.phone import normalize_phone
from app.model.identity import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserPassword,
    UserPhone,
    UserRole,
)


def _require_delivery_target() -> tuple[str, str, str]:
    settings = get_settings()
    target = settings.active_database_url
    url = make_url(settings.active_database_url)
    actual = f"{url.host}:{url.port}/{url.database}"
    if (
        os.environ.get("PROPERTY_AI_DELIVERY_MIGRATION") != "approved"
        or os.environ.get("PROPERTY_AI_APPROVE_DELIVERY_TARGET") != actual
        or settings.app_env != "staging"
        or url.drivername != "mysql+pymysql"
        or url.host != "mysql"
        or url.port != 3306
        or url.database != "property_ai_delivery_check"
    ):
        raise RuntimeError("SYSTEM_ADMIN bootstrap refused outside the exact delivery database")
    phone = normalize_phone(os.environ.get("BOOTSTRAP_ADMIN_PHONE", ""))
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    display_name = os.environ.get("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Initial System Administrator").strip()
    if not phone or not display_name:
        raise RuntimeError("bootstrap administrator identity is incomplete")
    require_new_password(password)
    return target, phone, password


def _bootstrap(database_url: str, phone: str, password: str, *, connection=None) -> int:
    engine = create_database_engine(database_url) if connection is None else None
    try:
        with Session(bind=connection if connection is not None else engine, autoflush=False, expire_on_commit=False) as session:
            with begin_owned_transaction(session):
                revision = session.execute(text("SELECT version_num FROM alembic_version FOR UPDATE")).scalar_one()
                if revision != "20260925_0012_task_intents":
                    raise RuntimeError("SYSTEM_ADMIN bootstrap requires actual revision 0012")
                if session.scalar(select(func.count()).select_from(User)):
                    raise RuntimeError("delivery bootstrap requires an empty users table")

                roles: dict[str, Role] = {}
                for item in SEED_ROLES:
                    record = Role(code=item.code, name=item.name)
                    session.add(record)
                    session.flush()
                    roles[item.code] = record

                permissions: dict[str, Permission] = {}
                for item in SEED_PERMISSIONS:
                    record = Permission(code=item.code, name=item.name)
                    session.add(record)
                    session.flush()
                    permissions[item.code] = record
                for role_code, permission_codes in SEED_ROLE_PERMISSIONS.items():
                    for permission_code in permission_codes:
                        session.add(RolePermission(
                            role_id=roles[role_code].id,
                            permission_id=permissions[permission_code].id,
                        ))

                now = utc_now()
                user = User(
                    status="ACTIVE",
                    display_name=os.environ.get(
                        "BOOTSTRAP_ADMIN_DISPLAY_NAME", "Initial System Administrator"
                    ).strip(),
                    profile_completed_at=now,
                )
                session.add(user)
                session.flush()
                session.add_all([
                    UserPhone(
                        user_id=user.id,
                        phone_e164=phone,
                        status="ACTIVE",
                        verified_at=now,
                    ),
                    UserPassword(
                        user_id=user.id,
                        password_hash=hash_password(password),
                        password_algo="argon2id",
                    ),
                    UserRole(
                        user_id=user.id,
                        role_id=roles["SYSTEM_ADMIN"].id,
                        scope_type="SYSTEM",
                        tenant_id=None,
                        property_id=None,
                    ),
                    AuditLog(
                        actor_user_id=user.id,
                        tenant_id=None,
                        property_id=None,
                        action="SYSTEM_ADMIN_BOOTSTRAPPED",
                        resource_type="USER",
                        resource_id=user.id,
                        request_id=f"delivery-bootstrap-{uuid4().hex}",
                        details_json={"channel": "operator_secret", "initial": True},
                    ),
                ])
        print("delivery SYSTEM_ADMIN bootstrapped without emitting credentials")
        return 0
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    database_url, phone, password = _require_delivery_target()
    return _bootstrap(database_url, phone, password)


if __name__ == "__main__":
    raise SystemExit(main())
