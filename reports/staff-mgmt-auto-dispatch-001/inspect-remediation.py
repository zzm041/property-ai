"""Read-only redacted persistence evidence for staff remediation round 1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


def _rows(connection, statement: str, parameters: dict[str, object]):
    return [
        dict(row)
        for row in connection.execute(text(statement), parameters).mappings()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        employees = _rows(
            connection,
            "SELECT e.id, e.employee_no, e.status, e.department_id, d.name AS department_name, "
            "e.max_capacity FROM employees e LEFT JOIN departments d ON d.id = e.department_id "
            "WHERE e.id IN (9, 10) ORDER BY e.id",
            {},
        )
        assignments = _rows(
            connection,
            "SELECT id, employee_id, property_id, status, valid_from, valid_to "
            "FROM employee_properties WHERE employee_id IN (9, 10) ORDER BY employee_id, id",
            {},
        )
        duties = _rows(
            connection,
            "SELECT ed.employee_id, d.code, d.name FROM employee_duties ed "
            "JOIN duties d ON d.id = ed.duty_id WHERE ed.employee_id IN (9, 10) "
            "ORDER BY ed.employee_id, d.id",
            {},
        )
        skills = _rows(
            connection,
            "SELECT es.employee_id, s.code, s.name FROM employee_skills es "
            "JOIN skills s ON s.id = es.skill_id WHERE es.employee_id IN (9, 10) "
            "ORDER BY es.employee_id, s.id",
            {},
        )
        roster_count = connection.execute(
            text(
                "SELECT COUNT(DISTINCT e.id) FROM employees e "
                "JOIN employee_properties ep ON ep.employee_id = e.id "
                "AND ep.tenant_id = e.tenant_id "
                "WHERE e.tenant_id = 6 AND ep.property_id = 6"
            )
        ).scalar_one()
        audits = _rows(
            connection,
            "SELECT action, resource_type, resource_id, request_id, details_json "
            "FROM audit_logs WHERE (resource_type = 'EMPLOYEE' AND resource_id = 10) "
            "OR (resource_type = 'EMPLOYEE_PROPERTY' AND resource_id IN ("
            "SELECT id FROM employee_properties WHERE employee_id IN (9, 10))) "
            "ORDER BY id",
            {},
        )
    engine.dispose()
    evidence = {
        "task_id": "STAFF-MGMT-AUTO-DISPATCH-001",
        "review_round": 1,
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
        },
        "roster_distinct_employee_count_for_property_6": int(roster_count),
        "employees": employees,
        "assignments": assignments,
        "duties": duties,
        "skills": skills,
        "audits": audits,
        "boundaries": {
            "read_only": True,
            "business_state_changed": False,
            "phones_or_credentials_emitted": False,
        },
        "redaction": "No phone, token, credential, resident name, or work-order text is emitted.",
    }
    output = Path(args.output)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
