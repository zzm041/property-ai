"""Explicit, source-bound installation into a customer-owned EMPTY MySQL schema.

Planning is offline. Apply is never invoked by application/service startup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.cli.bootstrap_system_admin import _bootstrap
from app.cli.customer_install_authority import CustomerInstallAuthority
from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.passwords import require_new_password
from app.domain.phone import normalize_phone


BACKEND = Path(__file__).resolve().parents[2]
PRE_BOOTSTRAP = "20260925_0012_task_intents"
IDENTITY = "20260926_0014_identity_merge_sla_versions"
HEAD = "20261111_0018_content_fingerprint_loose"
PROTECTED = frozenset({
    "mysql", "sys", "information_schema", "performance_schema", "property_ai",
    "property_ai_test", "property_ai_staging", "property_ai_day4", "property_ai_auth001_test",
    "property_ai_delivery_check", "property_ai_delivery_media_cycle_check", "property_ai_restore_drill",
})


def source_digest() -> str:
    digest = hashlib.sha256()
    files = sorted([*BACKEND.joinpath("app").rglob("*.py"), *BACKEND.joinpath("alembic").rglob("*.py")])
    for path in files:
        digest.update(path.relative_to(BACKEND).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return config


def build_plan(settings) -> dict:
    url = make_url(settings.active_database_url)
    if (settings.app_env not in {"production", "staging"} or url.drivername != "mysql+pymysql"
            or not url.host or not url.database or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", url.database)
            or url.database.casefold() in PROTECTED
            or set(url.query) - {"charset", "ssl_ca", "ssl_cert", "ssl_key", "ssl_check_hostname"}):
        raise ValueError("CUSTOMER_INSTALL_TARGET")
    scripts = ScriptDirectory.from_config(_config())
    if scripts.get_heads() != [HEAD]:
        raise ValueError("CUSTOMER_INSTALL_HEAD")
    return {
        "format": "property-ai-customer-empty-install-v1",
        "target": f"{url.host}:{url.port or 3306}/{url.database}",
        "app_env": settings.app_env,
        "head": HEAD,
        "source_sha256": source_digest(),
        "steps": ["verify_empty_and_lock", PRE_BOOTSTRAP, "bootstrap_system_admin", IDENTITY, HEAD],
    }


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def require_empty_database(connection) -> None:
    for object_type in ("TABLES", "ROUTINES", "TRIGGERS", "EVENTS"):
        schema_column = {"TABLES": "TABLE_SCHEMA", "ROUTINES": "ROUTINE_SCHEMA", "TRIGGERS": "TRIGGER_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[object_type]
        count = connection.execute(text(
            f"SELECT COUNT(*) FROM information_schema.{object_type} WHERE {schema_column}=DATABASE()"
        )).scalar_one()
        if count != 0:
            raise ValueError("CUSTOMER_INSTALL_NOT_EMPTY")
    connection.commit()


def _credentials() -> tuple[str, str]:
    try:
        phone = normalize_phone(os.environ.get("BOOTSTRAP_ADMIN_PHONE", ""))
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        require_new_password(password)
        if not phone or not os.environ.get("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Initial System Administrator").strip():
            raise ValueError
        return phone, password
    except Exception as exc:
        raise ValueError("CUSTOMER_INSTALL_ADMIN") from exc


def apply_plan(settings, plan: dict, approved_digest: str) -> None:
    if plan != build_plan(settings):
        raise ValueError("CUSTOMER_INSTALL_PLAN")
    if approved_digest != plan_digest(plan):
        raise ValueError("CUSTOMER_INSTALL_APPROVAL")
    phone, password = _credentials()
    engine = create_database_engine(settings.active_database_url)
    lock_name = "property-ai-install-" + hashlib.sha256(make_url(settings.active_database_url).database.encode()).hexdigest()[:40]
    try:
        with engine.connect() as connection:
            locked = connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}).scalar_one()
            connection.commit()
            if locked != 1:
                raise ValueError("CUSTOMER_INSTALL_BUSY")
            authority = None
            try:
                if connection.execute(text("SELECT DATABASE()")).scalar_one() != make_url(settings.active_database_url).database:
                    raise ValueError("CUSTOMER_INSTALL_TARGET")
                connection.commit()
                require_empty_database(connection)
                config = _config()
                scripts = ScriptDirectory.from_config(config)
                revisions = frozenset(item.revision for item in scripts.walk_revisions())
                authority = CustomerInstallAuthority(connection, plan["target"], revisions)
                config.attributes.update(connection=connection, customer_install_authority=authority)
                # Alembic bookkeeping only: historical revision identifiers exceed
                # Alembic's default VARCHAR(32); business schema still uses migrations.
                connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL PRIMARY KEY)"))
                connection.commit()
                command.upgrade(config, PRE_BOOTSTRAP)
                connection.commit()
                _bootstrap(settings.active_database_url, phone, password, connection=connection)
                connection.commit()
                command.upgrade(config, IDENTITY)
                connection.commit()
                command.upgrade(config, HEAD)
                actual = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                if actual != HEAD:
                    raise ValueError("CUSTOMER_INSTALL_HEAD")
                connection.commit()
            finally:
                if authority is not None:
                    authority.active = False
                if not connection.invalidated:
                    connection.rollback()
                    connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
                    connection.commit()
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approve-plan", default="")
    args = parser.parse_args()
    try:
        settings = get_settings()
        if args.action == "plan":
            plan = build_plan(settings)
            with args.plan.open("x", encoding="utf-8") as output:
                output.write(json.dumps(plan, indent=2, ensure_ascii=True) + "\n")
            print("CUSTOMER_INSTALL_PLAN " + plan_digest(plan))
        else:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            apply_plan(settings, plan, args.approve_plan)
            print("CUSTOMER_INSTALL_COMPLETE " + HEAD)
        return 0
    except Exception as exc:
        # Driver/config errors may contain connection secrets; emit fixed codes only.
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"CUSTOMER_INSTALL_[A-Z_]+", str(exc)) else "CUSTOMER_INSTALL_FAILED"
        print(code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
