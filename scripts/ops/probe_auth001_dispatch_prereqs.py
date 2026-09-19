"""Count isolated AUTH001 dispatch prerequisites. Prints no phones or names."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(OPS))

from sqlalchemy import text

from auth001_isolated_runtime import build_isolated_settings
from app.core.database import create_database_engine, dispose_database_engines


def main() -> int:
    settings = build_isolated_settings(enable_enqueue=False)
    engine = create_database_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            def one(sql: str) -> object:
                return connection.execute(text(sql)).scalar()

            print("employees", one("SELECT COUNT(*) FROM employees"))
            print(
                "employee_user_bound",
                one("SELECT COUNT(*) FROM employees WHERE user_id IS NOT NULL"),
            )
            print("skills", one("SELECT COUNT(*) FROM skills"))
            print("employee_skills", one("SELECT COUNT(*) FROM employee_skills"))
            print("employee_duties", one("SELECT COUNT(*) FROM employee_duties"))
            print(
                "daily_rows",
                one("SELECT COUNT(*) FROM employee_daily_statuses"),
            )
            print(
                "relations",
                one("SELECT COUNT(*) FROM resident_house_relations"),
            )
            print("houses", one("SELECT COUNT(*) FROM houses"))
            print("work_orders", one("SELECT COUNT(*) FROM work_orders"))
            print(
                "assignments",
                one("SELECT COUNT(*) FROM work_order_assignments"),
            )
            print(
                "skill_codes",
                [row[0] for row in connection.execute(text("SELECT code FROM skills ORDER BY id"))],
            )
            print(
                "duty_codes",
                [row[0] for row in connection.execute(text("SELECT code FROM duties ORDER BY id"))],
            )
            print(
                "daily_by_status",
                [
                    (row[0], row[1])
                    for row in connection.execute(
                        text(
                            "SELECT on_duty, COUNT(*) FROM employee_daily_statuses "
                            "GROUP BY on_duty"
                        )
                    )
                ],
            )
            print(
                "electrical_skill_holders",
                one(
                    "SELECT COUNT(*) FROM employee_skills es "
                    "JOIN skills sk ON sk.id = es.skill_id "
                    "WHERE sk.code = 'ELECTRICAL'"
                ),
            )
            print(
                "maintenance_duty_holders",
                one(
                    "SELECT COUNT(*) FROM employee_duties ed "
                    "JOIN duties d ON d.id = ed.duty_id "
                    "WHERE d.code = 'MAINTENANCE'"
                ),
            )
            print(
                "approved_relations",
                one(
                    "SELECT COUNT(*) FROM resident_house_relations "
                    "WHERE BINARY status = 'APPROVED'"
                ),
            )
    finally:
        engine.dispose()
        dispose_database_engines()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
