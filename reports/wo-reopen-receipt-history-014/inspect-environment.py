"""Read-only AUTH001 presence check. Never initializes or migrates a schema."""
import json
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from auth001_isolated_runtime import load_source_settings, isolated_database_url
from app.core.database import assert_isolated_auth_database

settings = load_source_settings()
url = isolated_database_url(settings)
assert_isolated_auth_database(settings, database_url=url)
parsed = make_url(url)
result = {
    "checked_at_utc": datetime.now(UTC).isoformat(),
    "target": f"{parsed.host}:{parsed.port}/{parsed.database}",
    "mode": "read-only; no initialization, migration, seed or business writes",
}
engine = create_engine(url)
try:
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        result["revision"] = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        result["users_count"] = connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        result["work_orders_count"] = connection.execute(text("SELECT COUNT(*) FROM work_orders")).scalar_one()
        result["status"] = "PRESENT"
except OperationalError as exc:
    number = exc.orig.args[0] if exc.orig.args else None
    result["status"] = "AUTH001_MISSING" if number == 1049 else "CONNECTION_OR_SCHEMA_BLOCKED"
    result["mysql_error_number"] = number
finally:
    engine.dispose()
print(json.dumps(result, indent=2))
