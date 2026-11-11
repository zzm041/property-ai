#!/usr/bin/env python3
"""t204: seed the three E2E personas so "dispatch → accept → finish → review" runs.

The delivery database has residents and a few synthetic employees, but a fresh
checkout cannot run the supervisor flow at all: nobody is on duty today, the
staff user has no attendance row for today, and the supervisor account is a
seeded `__DEV_SUPERVISOR__` placeholder whose password only the dev-seed path
knows. The symptom the harness sees is `filter_reason=NOT_ON_DUTY` /
`NOT_CHECKED_IN` on dispatch and `IAM.FORBIDDEN` on the supervisor pages.

This script makes the three personas real, idempotently, by walking *exactly*
the gates `AssignService._eligibility_error` (backend/app/service/work_order/
assign.py:752-833) walks:

1. `employees.status = ACTIVE`, `user.status = ACTIVE`;
2. a dispatch role (`DISPATCH_STAFF_ROLES` in app/domain/work_order/employee.py:37);
3. `employee_properties` ACTIVE row whose window covers now (`PROPERTY_NOT_EFFECTIVE`);
4. `employee_daily_statuses` for **the property's business date** — note this is
   `current_business_date(property.timezone)`, not the database's `curdate()`:
   at 08:00 Asia/Shanghai the UTC clock still says yesterday, so a row keyed on
   `curdate()` reads as "no row" and the employee looks off duty (t203 lost an
   hour to this);
5. `attendance_records` for the same business date with a non-null `check_in_at`;
6. duty/skill match against the analysis (`MAINTENANCE` duty + `MAINTENANCE`
   skill for plumbing/electrical/hvac work) — `capability_filter_reason` in
   app/domain/ai/rules.py:1288;
7. capacity: `employee_global_occupancy` below `max_capacity`, **and for ordinary
   maintenance work a load of zero** (`FilterReason.BUSY`, assign.py:829). The
   600 s `dispatch_reject_cooldown_seconds` only blacklists an employee who
   already rejected *this same* work order, so a first dispatch is never affected.

Nothing here changes product code: it writes test data only.

Idempotence: every write is "insert if absent, else leave alone" (passwords are
re-hashed only when `verify_password` says the stored hash is not the wanted
one, so repeated runs do not churn the row). The one deliberate bulk write is
parking *other* employees at the property `OFF_DUTY` for today, which makes the
seeded staffer the only eligible candidate — disable it with
`--keep-others-on-duty` when you want the real ranking to pick.

Usage::

    # 1. seed (repeatable)
    python scripts/ops/seed_e2e_personas.py \
        --database-url 'mysql+pymysql://user:pw@127.0.0.1:3327/property_ai_delivery_check?charset=utf8mb4'

    # 2. read-only audit of what a running stack would see right now
    python scripts/ops/seed_e2e_personas.py --check --database-url '...'

    # 3. prove the chain end to end (creates one real work order)
    python scripts/ops/seed_e2e_personas.py --verify --api-base http://127.0.0.1:8093/api/v1 \
        --database-url '...'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.passwords import hash_password, verify_password  # noqa: E402
from app.core.time import current_business_date, utc_now  # noqa: E402
from app.domain.identity.rules import normalize_phone  # noqa: E402

DEFAULT_DSN_ENV = "DATABASE_URL"
DEFAULT_PASSWORD = "DevPass2026"

DEFAULT_TENANT_ID = 5
DEFAULT_PROPERTY_ID = 5
DEFAULT_RESIDENT_USER_ID = 6
DEFAULT_STAFF_USER_ID = 4
DEFAULT_SUPERVISOR_USER_ID = 22

RESIDENT_PHONE = "+8613842395192"
STAFF_PHONE = "+8613842395189"
SUPERVISOR_PHONE = "+8613910000001"

DUTY_CODE = "MAINTENANCE"
SKILL_CODE = "MAINTENANCE"

# The supervisor queue and the manual dispatch endpoint both accept SUPERVISOR
# (role 3) and PROPERTY_ADMIN (role 4); SUPERVISOR is the "物业主管" persona and
# is what the miniprogram's supervisor page is written for. Since F-4 / t210 the
# supervisor also carries announcement:publish, so one login can draft, submit
# and publish; --supervisor-role still lets a drill pin PROPERTY_ADMIN instead.
DEFAULT_SUPERVISOR_ROLES = ("SUPERVISOR",)


@dataclass
class Change:
    target: str
    action: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"target": self.target, "action": self.action, "detail": self.detail}


@dataclass
class SeedReport:
    tenant_id: int
    property_id: int
    business_date: str
    changes: list[Change] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def record(self, target: str, action: str, detail: str = "") -> None:
        self.changes.append(Change(target=target, action=action, detail=detail))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "property_id": self.property_id,
            "business_date": self.business_date,
            "dry_run": self.dry_run,
            "changes": [change.as_dict() for change in self.changes],
            "facts": self.facts,
        }


def _parse_e164(raw: str) -> str:
    phone = normalize_phone(raw)
    if phone is None:
        raise SystemExit(f"not a usable phone number: {raw!r}")
    return phone


class PersonaSeeder:
    """Seed the E2E personas inside one transaction owned by the caller."""

    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: int,
        property_id: int,
        password: str,
        supervisor_roles: tuple[str, ...],
        park_others: bool,
        dry_run: bool,
    ) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self._property_id = property_id
        self._password = password
        self._supervisor_roles = supervisor_roles
        self._park_others = park_others
        self._dry_run = dry_run

    # -- small helpers ---------------------------------------------------
    def _scalar(self, sql: str, **params: Any) -> Any:
        return self._connection.execute(text(sql), params).scalar()

    def _rows(self, sql: str, **params: Any) -> list[Any]:
        return list(self._connection.execute(text(sql), params).all())

    def _execute(self, sql: str, **params: Any) -> None:
        if self._dry_run:
            return
        self._connection.execute(text(sql), params)

    # -- entry point -----------------------------------------------------
    def seed(self) -> SeedReport:
        property_record = self._resolve_property()
        timezone_name = str(property_record.timezone)
        business_date = current_business_date(timezone_name)
        report = SeedReport(
            tenant_id=self._tenant_id,
            property_id=self._property_id,
            business_date=business_date.isoformat(),
            dry_run=self._dry_run,
        )
        roles = self._ensure_roles(report)
        for role_code in self._supervisor_roles:
            self._ensure_role_grant(
                report,
                role_code=role_code,
                permission_code="announcement:publish",
            )
        resident = self._ensure_person(
            report,
            user_id=DEFAULT_RESIDENT_USER_ID,
            phone=RESIDENT_PHONE,
            display_name="E2E 住户",
            role_codes=("RESIDENT",),
            roles=roles,
        )
        staff = self._ensure_person(
            report,
            user_id=DEFAULT_STAFF_USER_ID,
            phone=STAFF_PHONE,
            display_name="E2E 维修工",
            role_codes=("STAFF",),
            roles=roles,
        )
        supervisor = self._ensure_person(
            report,
            user_id=DEFAULT_SUPERVISOR_USER_ID,
            phone=SUPERVISOR_PHONE,
            display_name="E2E 物业主管",
            role_codes=self._supervisor_roles,
            roles=roles,
        )
        employee_id = self._ensure_employee(report, user_id=staff, business_date=business_date)
        self._ensure_capability(report, employee_id=employee_id)
        self._ensure_attendance(report, employee_id=employee_id, business_date=business_date)
        self._ensure_daily_status(
            report,
            employee_id=employee_id,
            business_date=business_date,
            on_duty="ON_DUTY",
        )
        if self._park_others:
            self._park_other_employees(
                report,
                keep_employee_id=employee_id,
                business_date=business_date,
            )
        report.facts.update(
            {
                "resident_user_id": resident,
                "staff_user_id": staff,
                "staff_employee_id": employee_id,
                "supervisor_user_id": supervisor,
                "supervisor_roles": list(self._supervisor_roles),
                "password_set_for": [resident, staff, supervisor],
                "eligibility": self._eligibility_facts(employee_id, business_date),
                "house_ids": self._house_ids(resident),
            }
        )
        return report

    # -- reading ---------------------------------------------------------
    def _resolve_property(self) -> Any:
        property_record = self._connection.execute(
            text(
                "select id, tenant_id, timezone from properties"
                " where id = :pid and tenant_id = :tid"
            ),
            {"pid": self._property_id, "tid": self._tenant_id},
        ).first()
        if property_record is None:
            raise SystemExit(
                f"property {self._property_id} does not belong to tenant {self._tenant_id}"
            )
        tenant_status = self._scalar(
            "select status from tenants where id = :tid",
            tid=self._tenant_id,
        )
        if tenant_status != "ACTIVE":
            raise SystemExit(f"tenant {self._tenant_id} is {tenant_status!r}, not ACTIVE")
        return property_record

    def _ensure_roles(self, report: SeedReport) -> dict[str, int]:
        wanted = {"RESIDENT", "STAFF", *self._supervisor_roles}
        roles = {
            str(code): int(role_id)
            for role_id, code in self._rows("select id, code from roles")
            if str(code) in wanted
        }
        missing = sorted(wanted - set(roles))
        if missing:
            raise SystemExit(f"roles missing from the database: {', '.join(missing)}")
        report.facts["role_ids"] = roles
        return roles

    def _ensure_role_grant(
        self,
        report: SeedReport,
        *,
        role_code: str,
        permission_code: str,
    ) -> None:
        """Make sure a role actually carries a permission (F-4 / t210).

        Binding a user to SUPERVISOR is not enough: the announcement review button
        and the publish endpoint both read `announcement:publish` off the role's
        permission set. A database migrated before 0019-style seeds can carry the
        role without the grant, and then the announcement chain dead-ends in
        PENDING_REVIEW. Insert the grant when it is missing, leave it alone when it
        is already there, so the seeder stays idempotent.
        """
        present = self._scalar(
            "select count(*) from role_permissions rp"
            " join roles r on r.id = rp.role_id"
            " join permissions p on p.id = rp.permission_id"
            " where r.code = :role and p.code = :permission",
            role=role_code,
            permission=permission_code,
        )
        if present:
            report.record(
                "role_permissions",
                f"{role_code}:{permission_code} unchanged",
                "grant already present",
            )
            return
        if self._dry_run:
            report.record(
                "role_permissions",
                f"{role_code}:{permission_code} planned",
                "grant would be inserted",
            )
            return
        self._execute(
            "insert into role_permissions (role_id, permission_id, created_at)"
            " select r.id, p.id, current_timestamp(6) from roles r, permissions p"
            " where r.code = :role and p.code = :permission",
            role=role_code,
            permission=permission_code,
        )
        report.record(
            "role_permissions",
            f"{role_code}:{permission_code} inserted",
            "grant was missing",
        )

    def _house_ids(self, resident_user_id: int) -> list[int]:
        return [
            int(house_id)
            for (house_id,) in self._rows(
                "select distinct house_id from resident_house_relations"
                " where user_id = :uid and tenant_id = :tid and property_id = :pid"
                " and status = 'ACTIVE' and valid_to is null"
                " order by house_id",
                uid=resident_user_id,
                tid=self._tenant_id,
                pid=self._property_id,
            )
        ]

    def _eligibility_facts(self, employee_id: int, business_date: date) -> dict[str, Any]:
        row = self._connection.execute(
            text(
                "select e.id as employee_id, e.status as employee_status,"
                " e.max_capacity, u.id as user_id, u.status as user_status,"
                " (select count(*) from employee_properties ep"
                "   where ep.employee_id = e.id and ep.property_id = :pid"
                "   and ep.status = 'ACTIVE' and ep.valid_to is null"
                "   and ep.valid_from <= :now) as property_active,"
                " (select count(*) from employee_daily_statuses s"
                "   where s.employee_id = e.id and s.property_id = :pid"
                "   and s.business_date = :day and s.on_duty = 'ON_DUTY') as on_duty,"
                " (select count(*) from attendance_records a"
                "   where a.employee_id = e.id and a.property_id = :pid"
                "   and a.business_date = :day and a.check_in_at is not null) as checked_in,"
                " (select group_concat(d.code order by d.code)"
                "   from employee_duties ed join duties d on d.id = ed.duty_id"
                "   where ed.employee_id = e.id) as duties,"
                " (select group_concat(s.code order by s.code)"
                "   from employee_skills es join skills s on s.id = es.skill_id"
                "   where es.employee_id = e.id) as skills,"
                " (select count(*) from work_order_assignments wa"
                "   where wa.employee_id = e.id and wa.status = 'ACTIVE'"
                "   and wa.ended_at is null) as active_load,"
                " (select count(*) from user_roles ur join roles r on r.id = ur.role_id"
                "   where ur.user_id = e.user_id and ur.property_id = :pid"
                "   and r.code in ('STAFF', 'SUPERVISOR', 'PROPERTY_ADMIN', 'TENANT_ADMIN'))"
                "   as dispatch_roles"
                " from employees e join users u on u.id = e.user_id"
                " where e.id = :eid"
            ),
            {
                "eid": employee_id,
                "pid": self._property_id,
                "day": business_date,
                "now": utc_now(),
            },
        ).first()
        if row is None:
            return {"employee_id": employee_id, "missing": True}
        facts = dict(row._mapping)
        facts["business_date"] = business_date.isoformat()
        facts["busy_rule_satisfied"] = int(facts.get("active_load") or 0) == 0
        return facts

    # -- writing ---------------------------------------------------------
    def _ensure_person(
        self,
        report: SeedReport,
        *,
        user_id: int,
        phone: str,
        display_name: str,
        role_codes: tuple[str, ...],
        roles: dict[str, int],
    ) -> int:
        phone = _parse_e164(phone)
        resolved = self._resolve_user(user_id=user_id, phone=phone, report=report)
        if resolved is None:
            self._execute(
                "insert into users (id, status, display_name) values (:uid, 'ACTIVE', :name)",
                uid=user_id,
                name=display_name,
            )
            report.record(f"users#{user_id}", "created", display_name)
            resolved = user_id
        else:
            self._ensure_user_active(resolved, report)
        self._ensure_phone(resolved, phone, report)
        self._ensure_password(resolved, report)
        for code in role_codes:
            self._ensure_user_role(resolved, roles[code], code, report)
        return resolved

    def _resolve_user(
        self,
        *,
        user_id: int,
        phone: str,
        report: SeedReport,
    ) -> int | None:
        owner = self._scalar(
            "select user_id from user_phones"
            " where phone_e164 = :phone and status = 'ACTIVE' and ended_at is null",
            phone=phone,
        )
        exists = self._scalar("select id from users where id = :uid", uid=user_id)
        if owner is not None and int(owner) != user_id:
            raise SystemExit(
                f"phone {phone} already belongs to user {int(owner)};"
                f" expected user {user_id} (refusing to move a phone between users)"
            )
        if exists is None and owner is not None:
            report.record(f"users#{user_id}", "reused", f"resolved by phone to user {int(owner)}")
            return int(owner)
        return None if exists is None else user_id

    def _ensure_user_active(self, user_id: int, report: SeedReport) -> None:
        status = self._scalar("select status from users where id = :uid", uid=user_id)
        if status == "ACTIVE":
            report.record(f"users#{user_id}", "unchanged", "status ACTIVE")
            return
        self._execute(
            "update users set status = 'ACTIVE' where id = :uid",
            uid=user_id,
        )
        report.record(f"users#{user_id}", "reactivated", f"was {status!r}")

    def _ensure_phone(self, user_id: int, phone: str, report: SeedReport) -> None:
        existing = self._scalar(
            "select id from user_phones"
            " where user_id = :uid and phone_e164 = :phone"
            " and status = 'ACTIVE' and ended_at is null",
            uid=user_id,
            phone=phone,
        )
        if existing is not None:
            report.record(f"user_phones#{user_id}", "unchanged", phone)
            return
        stale = self._scalar(
            "select id from user_phones where user_id = :uid and ended_at is null",
            uid=user_id,
        )
        if stale is not None:
            # uq_user_phones_active_user allows one live phone per user.
            self._execute(
                "update user_phones set status = 'ENDED', ended_at = :now"
                " where user_id = :uid and ended_at is null",
                uid=user_id,
                now=utc_now(),
            )
            report.record(f"user_phones#{user_id}", "ended_previous", phone)
        self._execute(
            "insert into user_phones (user_id, phone_e164, status, verified_at)"
            " values (:uid, :phone, 'ACTIVE', :now)",
            uid=user_id,
            phone=phone,
            now=utc_now(),
        )
        report.record(f"user_phones#{user_id}", "created", phone)

    def _ensure_password(self, user_id: int, report: SeedReport) -> None:
        stored = self._scalar(
            "select password_hash from user_passwords where user_id = :uid",
            uid=user_id,
        )
        if stored is not None:
            try:
                matches = verify_password(stored, self._password)
            except Exception:  # noqa: BLE001 - any stored format we cannot read is "not ours"
                matches = False
            if matches:
                report.record(f"user_passwords#{user_id}", "unchanged", "argon2id")
                return
            self._execute(
                "update user_passwords set password_hash = :hash, password_algo = 'argon2id'"
                " where user_id = :uid",
                uid=user_id,
                hash=hash_password(self._password),
            )
            report.record(f"user_passwords#{user_id}", "updated", "argon2id")
            return
        self._execute(
            "insert into user_passwords (user_id, password_hash, password_algo)"
            " values (:uid, :hash, 'argon2id')",
            uid=user_id,
            hash=hash_password(self._password),
        )
        report.record(f"user_passwords#{user_id}", "created", "argon2id")

    def _ensure_user_role(
        self,
        user_id: int,
        role_id: int,
        role_code: str,
        report: SeedReport,
    ) -> None:
        existing = self._scalar(
            "select id from user_roles where user_id = :uid and role_id = :rid"
            " and scope_type = 'PROPERTY' and tenant_id = :tid and property_id = :pid",
            uid=user_id,
            rid=role_id,
            tid=self._tenant_id,
            pid=self._property_id,
        )
        if existing is not None:
            report.record(f"user_roles#{user_id}", "unchanged", role_code)
            return
        self._execute(
            "insert into user_roles (user_id, role_id, scope_type, tenant_id, property_id)"
            " values (:uid, :rid, 'PROPERTY', :tid, :pid)",
            uid=user_id,
            rid=role_id,
            tid=self._tenant_id,
            pid=self._property_id,
        )
        report.record(f"user_roles#{user_id}", "created", role_code)

    def _ensure_employee(
        self,
        report: SeedReport,
        *,
        user_id: int,
        business_date: date,
    ) -> int:
        existing = self._connection.execute(
            text(
                "select id, status from employees"
                " where user_id = :uid and tenant_id = :tid"
            ),
            {"uid": user_id, "tid": self._tenant_id},
        ).first()
        if existing is None:
            employee_no = f"E2E-P{user_id}"
            self._execute(
                "insert into employees (user_id, tenant_id, employee_no, status, max_capacity)"
                " values (:uid, :tid, :no, 'ACTIVE', 3)",
                uid=user_id,
                tid=self._tenant_id,
                no=employee_no,
            )
            employee_id = int(
                self._scalar(
                    "select id from employees where user_id = :uid and tenant_id = :tid",
                    uid=user_id,
                    tid=self._tenant_id,
                )
            )
            report.record(f"employees#{employee_id}", "created", employee_no)
        else:
            employee_id = int(existing.id)
            if str(existing.status) != "ACTIVE":
                self._execute(
                    "update employees set status = 'ACTIVE' where id = :eid",
                    eid=employee_id,
                )
                report.record(f"employees#{employee_id}", "reactivated", str(existing.status))
            else:
                report.record(f"employees#{employee_id}", "unchanged", "ACTIVE")
        self._ensure_employee_property(report, employee_id=employee_id)
        return employee_id

    def _ensure_employee_property(self, report: SeedReport, *, employee_id: int) -> None:
        active = self._scalar(
            "select id from employee_properties where employee_id = :eid"
            " and property_id = :pid and tenant_id = :tid"
            " and status = 'ACTIVE' and valid_to is null",
            eid=employee_id,
            pid=self._property_id,
            tid=self._tenant_id,
        )
        if active is not None:
            report.record(f"employee_properties#{employee_id}", "unchanged", "ACTIVE")
            return
        self._execute(
            "insert into employee_properties"
            " (employee_id, tenant_id, property_id, status, valid_from)"
            " values (:eid, :tid, :pid, 'ACTIVE', :now)",
            eid=employee_id,
            tid=self._tenant_id,
            pid=self._property_id,
            now=utc_now(),
        )
        report.record(f"employee_properties#{employee_id}", "created", "ACTIVE")

    def _ensure_capability(self, report: SeedReport, *, employee_id: int) -> None:
        duty_id = self._ensure_code_row(
            report,
            table="duties",
            code=DUTY_CODE,
            name="维修（E2E）",
        )
        skill_id = self._ensure_code_row(
            report,
            table="skills",
            code=SKILL_CODE,
            name="维修（E2E）",
        )
        self._ensure_link(
            report,
            table="employee_duties",
            column="duty_id",
            employee_id=employee_id,
            target_id=duty_id,
            label=f"duty {DUTY_CODE}",
        )
        self._ensure_link(
            report,
            table="employee_skills",
            column="skill_id",
            employee_id=employee_id,
            target_id=skill_id,
            label=f"skill {SKILL_CODE}",
        )

    def _ensure_code_row(
        self,
        report: SeedReport,
        *,
        table: str,
        code: str,
        name: str,
    ) -> int:
        existing = self._scalar(
            f"select id from {table} where tenant_id = :tid and code = :code",
            tid=self._tenant_id,
            code=code,
        )
        if existing is not None:
            report.record(f"{table}#{int(existing)}", "unchanged", code)
            return int(existing)
        self._execute(
            f"insert into {table} (tenant_id, code, name, status)"
            " values (:tid, :code, :name, 'ACTIVE')",
            tid=self._tenant_id,
            code=code,
            name=name,
        )
        new_id = int(
            self._scalar(
                f"select id from {table} where tenant_id = :tid and code = :code",
                tid=self._tenant_id,
                code=code,
            )
        )
        report.record(f"{table}#{new_id}", "created", code)
        return new_id

    def _ensure_link(
        self,
        report: SeedReport,
        *,
        table: str,
        column: str,
        employee_id: int,
        target_id: int,
        label: str,
    ) -> None:
        existing = self._scalar(
            f"select id from {table}"
            f" where employee_id = :eid and {column} = :target",
            eid=employee_id,
            target=target_id,
        )
        if existing is not None:
            report.record(f"{table}#{employee_id}", "unchanged", label)
            return
        self._execute(
            f"insert into {table} (employee_id, tenant_id, {column})"
            " values (:eid, :tid, :target)",
            eid=employee_id,
            tid=self._tenant_id,
            target=target_id,
        )
        report.record(f"{table}#{employee_id}", "created", label)

    def _ensure_attendance(
        self,
        report: SeedReport,
        *,
        employee_id: int,
        business_date: date,
    ) -> None:
        existing = self._connection.execute(
            text(
                "select id, check_in_at, check_out_at from attendance_records"
                " where employee_id = :eid and property_id = :pid and business_date = :day"
            ),
            {"eid": employee_id, "pid": self._property_id, "day": business_date},
        ).first()
        if existing is not None and existing.check_in_at is not None:
            if existing.check_out_at is None:
                report.record(f"attendance_records#{employee_id}", "unchanged", "checked in")
                return
            self._execute(
                "update attendance_records set check_out_at = null where id = :id",
                id=int(existing.id),
            )
            report.record(f"attendance_records#{employee_id}", "reopened", "check_out cleared")
            return
        if existing is None:
            self._execute(
                "insert into attendance_records"
                " (employee_id, tenant_id, property_id, business_date, check_in_at, source)"
                " values (:eid, :tid, :pid, :day, :now, 'INTERNAL')",
                eid=employee_id,
                tid=self._tenant_id,
                pid=self._property_id,
                day=business_date,
                now=utc_now(),
            )
            report.record(f"attendance_records#{employee_id}", "created", "check_in INTERNAL")
            return
        self._execute(
            "update attendance_records set check_in_at = :now, source = 'INTERNAL' where id = :id",
            id=int(existing.id),
            now=utc_now(),
        )
        report.record(f"attendance_records#{employee_id}", "updated", "check_in set")

    def _ensure_daily_status(
        self,
        report: SeedReport,
        *,
        employee_id: int,
        business_date: date,
        on_duty: str,
    ) -> None:
        load = int(
            self._scalar(
                "select count(*) from work_order_assignments"
                " where employee_id = :eid and status = 'ACTIVE' and ended_at is null",
                eid=employee_id,
            )
            or 0
        )
        existing = self._connection.execute(
            text(
                "select id, on_duty, current_load from employee_daily_statuses"
                " where employee_id = :eid and property_id = :pid and business_date = :day"
            ),
            {"eid": employee_id, "pid": self._property_id, "day": business_date},
        ).first()
        if existing is None:
            self._execute(
                "insert into employee_daily_statuses"
                " (employee_id, tenant_id, property_id, business_date, on_duty, current_load)"
                " values (:eid, :tid, :pid, :day, :state, :load)",
                eid=employee_id,
                tid=self._tenant_id,
                pid=self._property_id,
                day=business_date,
                state=on_duty,
                load=load,
            )
            report.record(f"employee_daily_statuses#{employee_id}", "created", f"{on_duty} load={load}")
            return
        if str(existing.on_duty) == on_duty and int(existing.current_load or 0) == load:
            report.record(f"employee_daily_statuses#{employee_id}", "unchanged", f"{on_duty} load={load}")
            return
        self._execute(
            "update employee_daily_statuses set on_duty = :state, current_load = :load"
            " where id = :id",
            id=int(existing.id),
            state=on_duty,
            load=load,
        )
        report.record(
            f"employee_daily_statuses#{employee_id}",
            "updated",
            f"{existing.on_duty}/{existing.current_load} -> {on_duty}/{load}",
        )

    def _park_other_employees(
        self,
        report: SeedReport,
        *,
        keep_employee_id: int,
        business_date: date,
    ) -> None:
        rows = self._rows(
            "select employee_id, on_duty from employee_daily_statuses"
            " where tenant_id = :tid and property_id = :pid and business_date = :day"
            " and employee_id <> :keep",
            tid=self._tenant_id,
            pid=self._property_id,
            day=business_date,
            keep=keep_employee_id,
        )
        parked = [int(employee_id) for employee_id, state in rows if str(state) != "OFF_DUTY"]
        if parked:
            self._execute(
                "update employee_daily_statuses set on_duty = 'OFF_DUTY', current_load = 0"
                " where tenant_id = :tid and property_id = :pid and business_date = :day"
                " and employee_id <> :keep",
                tid=self._tenant_id,
                pid=self._property_id,
                day=business_date,
                keep=keep_employee_id,
            )
        report.record(
            "employee_daily_statuses",
            "parked_others" if parked else "no_other_on_duty",
            f"business_date={business_date} keep={keep_employee_id}"
            f" parked={sorted(parked)}",
        )


# ---------------------------------------------------------------------------
# HTTP verification (creates one real work order through the public API)
# ---------------------------------------------------------------------------


def _http(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any]]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a JSON body
        raw = exc.read()
        status = exc.code
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    return status, parsed


def _login(api_base: str, phone: str, password: str) -> str:
    status, body = _http(
        "POST",
        f"{api_base}/auth/password/login",
        body={"phone": phone, "password": password},
    )
    if status != 200:
        raise SystemExit(f"login failed for {phone}: HTTP {status} {json.dumps(body, ensure_ascii=False)[:400]}")
    data = body.get("data") or {}
    token = data.get("access_token")
    if not token:
        raise SystemExit(f"login for {phone} returned no access_token: {json.dumps(body, ensure_ascii=False)[:400]}")
    return str(token)


def _wait_analysis_verdict(dsn: str, work_order_id: int, *, timeout_seconds: float) -> dict[str, Any]:
    engine = create_engine(dsn)
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    try:
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "select id, status, category, priority, severity, risk,"
                        " required_role, required_skill, confidence, error_code"
                        " from work_order_analyses where work_order_id = :wid"
                        " order by id desc limit 1"
                    ),
                    {"wid": work_order_id},
                ).first()
            if row is not None:
                last = dict(row._mapping)
                if str(last.get("status")) in {"COMPLETED", "FAILED"}:
                    return last
            time.sleep(1.5)
    finally:
        engine.dispose()
    return last


def _dispatch_state(dsn: str, work_order_id: int) -> dict[str, Any]:
    engine = create_engine(dsn)
    try:
        with engine.connect() as connection:
            order = connection.execute(
                text("select id, status, version, tenant_id, property_id from work_orders where id = :wid"),
                {"wid": work_order_id},
            ).first()
            assignment = connection.execute(
                text(
                    "select id, employee_id, status, assigned_source from work_order_assignments"
                    " where work_order_id = :wid order by id desc limit 1"
                ),
                {"wid": work_order_id},
            ).first()
    finally:
        engine.dispose()
    return {
        "work_order": dict(order._mapping) if order is not None else {},
        "assignment": dict(assignment._mapping) if assignment is not None else {},
    }


def _employee_id_of(dsn: str, user_id: int, tenant_id: int) -> int:
    engine = create_engine(dsn)
    try:
        with engine.connect() as connection:
            employee_id = connection.execute(
                text("select id from employees where user_id = :uid and tenant_id = :tid"),
                {"uid": user_id, "tid": tenant_id},
            ).scalar()
    finally:
        engine.dispose()
    if employee_id is None:
        raise SystemExit(f"no employee row for user {user_id} in tenant {tenant_id}")
    return int(employee_id)


def _latest_analysis_id(dsn: str, work_order_id: int) -> int | None:
    engine = create_engine(dsn)
    try:
        with engine.connect() as connection:
            analysis_id = connection.execute(
                text(
                    "select id from work_order_analyses where work_order_id = :wid"
                    " order by id desc limit 1"
                ),
                {"wid": work_order_id},
            ).scalar()
    finally:
        engine.dispose()
    return None if analysis_id is None else int(analysis_id)


def verify_chain(
    *,
    dsn: str,
    api_base: str,
    report: dict[str, Any],
    password: str,
    analysis_timeout_seconds: float,
) -> dict[str, Any]:
    facts = report["facts"]
    tenant_id = int(report["tenant_id"])
    property_id = int(report["property_id"])
    staff_user_id = int(facts["staff_user_id"])
    staff_employee_id = int(facts["staff_employee_id"])
    houses = facts.get("house_ids") or []
    if not houses:
        raise SystemExit(
            "the resident has no ACTIVE house relation; cannot create a work order"
        )
    api_base = api_base.rstrip("/")
    result: dict[str, Any] = {"api_base": api_base, "steps": {}}

    resident_token = _login(api_base, RESIDENT_PHONE, password)
    result["steps"]["resident_login"] = {"status": "ok", "user_id": int(facts["resident_user_id"])}
    supervisor_phone = SUPERVISOR_PHONE
    supervisor_token = _login(api_base, supervisor_phone, password)
    result["steps"]["supervisor_login"] = {"status": "ok", "user_id": int(facts["supervisor_user_id"])}

    house_id = int(houses[0])
    text_body = (
        "E2E t204 派单链路：厨房水槽下方的排水管接口渗水，地面有一摊积水，需要维修。"
        f"（自动化验证 {utc_now().isoformat(timespec='seconds')}）"
    )
    status, body = _http(
        "POST",
        f"{api_base}/work-orders",
        token=resident_token,
        body={"house_id": house_id, "contact_phone": RESIDENT_PHONE, "text": text_body},
        headers={"Idempotency-Key": f"t204-{int(time.time() * 1000)}"},
    )
    if status != 200:
        raise SystemExit(
            f"create work order failed: HTTP {status} {json.dumps(body, ensure_ascii=False)[:500]}"
        )
    work_order_id = int(body["data"]["id"])
    result["steps"]["create_work_order"] = {
        "work_order_id": work_order_id,
        "status": body["data"].get("status"),
        "house_id": house_id,
    }

    analysis = _wait_analysis_verdict(dsn, work_order_id, timeout_seconds=analysis_timeout_seconds)
    result["steps"]["analysis"] = {
        "analysis_id": _latest_analysis_id(dsn, work_order_id),
        "status": analysis.get("status"),
        "category": analysis.get("category"),
        "priority": analysis.get("priority"),
        "required_skill": analysis.get("required_skill"),
        "error_code": analysis.get("error_code"),
    }
    if str(analysis.get("status")) != "COMPLETED":
        raise SystemExit(
            "analysis did not reach COMPLETED, cannot verify dispatch:"
            f" {json.dumps(result['steps']['analysis'], ensure_ascii=False)}"
        )

    state = _dispatch_state(dsn, work_order_id)
    result["steps"]["dispatch_before"] = state
    assignment = state.get("assignment") or {}
    if not assignment:
        order = state.get("work_order") or {}
        status, body = _http(
            "POST",
            f"{api_base}/supervisor/work-orders/{work_order_id}/assign",
            token=supervisor_token,
            body={
                "employee_id": staff_employee_id,
                "reason": "t204 E2E 派单验证",
                "version": order.get("version"),
                "tenant_id": tenant_id,
                "property_id": property_id,
            },
        )
        result["steps"]["manual_dispatch"] = {
            "http_status": status,
            "error_code": (body.get("error") or {}).get("code") if isinstance(body, dict) else None,
            "reason": (body.get("error") or {}).get("details") if isinstance(body, dict) else None,
        }
        if status != 200:
            raise SystemExit(
                "manual dispatch failed:"
                f" HTTP {status} {json.dumps(body, ensure_ascii=False)[:500]}"
            )
        state = _dispatch_state(dsn, work_order_id)
        assignment = state.get("assignment") or {}

    result["steps"]["dispatch_after"] = state
    assigned_employee_id = int(assignment.get("employee_id") or 0)
    result["steps"]["assignee"] = {
        "employee_id": assigned_employee_id,
        "expected_employee_id": staff_employee_id,
        "expected_user_id": staff_user_id,
        "matches_staff": assigned_employee_id == staff_employee_id,
        "assigned_source": assignment.get("assigned_source"),
        "assignment_id": assignment.get("id"),
        "assignment_status": assignment.get("status"),
    }

    status, body = _http(
        "GET",
        f"{api_base}/supervisor/incidents?property_id={property_id}",
        token=supervisor_token,
    )
    result["steps"]["supervisor_incidents"] = {
        "http_status": status,
        "items": len(((body or {}).get("data") or {}).get("items") or [])
        if isinstance(body, dict)
        else None,
        "error_code": (body.get("error") or {}).get("code") if isinstance(body, dict) else None,
    }
    status_queue, body_queue = _http(
        "GET",
        f"{api_base}/supervisor/work-orders?property_id={property_id}&limit=5",
        token=supervisor_token,
    )
    result["steps"]["supervisor_queue"] = {
        "http_status": status_queue,
        "error_code": (body_queue.get("error") or {}).get("code")
        if isinstance(body_queue, dict)
        else None,
    }
    if assigned_employee_id != staff_employee_id:
        raise SystemExit(
            "dispatch went to the wrong employee:"
            f" {json.dumps(result['steps']['assignee'], ensure_ascii=False)}"
        )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_dsn(raw: str | None) -> str:
    dsn = raw or os.environ.get(DEFAULT_DSN_ENV)
    if not dsn:
        raise SystemExit(
            f"no database url: pass --database-url or set {DEFAULT_DSN_ENV}"
        )
    return dsn


def _parse_roles(raw: str) -> tuple[str, ...]:
    roles = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    if not roles:
        raise SystemExit("--supervisor-role needs at least one role code")
    return roles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", default=None, help=f"DSN (default: ${DEFAULT_DSN_ENV})")
    parser.add_argument("--tenant-id", type=int, default=DEFAULT_TENANT_ID)
    parser.add_argument("--property-id", type=int, default=DEFAULT_PROPERTY_ID)
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="password for all three personas")
    parser.add_argument(
        "--supervisor-role",
        default=",".join(DEFAULT_SUPERVISOR_ROLES),
        help="comma separated role codes for the supervisor persona"
        " (SUPERVISOR for the queue; add PROPERTY_ADMIN when the scenario needs announcement:publish)",
    )
    parser.add_argument(
        "--keep-others-on-duty",
        action="store_true",
        help="do not park other employees OFF_DUTY (keeps the real ranking in play)",
    )
    parser.add_argument("--check", action="store_true", help="read-only: report the current state")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--verify", action="store_true", help="create one work order and prove the chain")
    parser.add_argument("--api-base", default="http://127.0.0.1:8093/api/v1")
    parser.add_argument("--analysis-timeout", type=float, default=300.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dsn = _resolve_dsn(args.database_url)
    engine = create_engine(dsn, future=True)
    report: SeedReport
    try:
        with engine.begin() as connection:
            seeder = PersonaSeeder(
                connection,
                tenant_id=args.tenant_id,
                property_id=args.property_id,
                password=args.password,
                supervisor_roles=_parse_roles(args.supervisor_role),
                park_others=not args.keep_others_on_duty and not args.check,
                dry_run=args.check,
            )
            report = seeder.seed()
    finally:
        engine.dispose()

    payload = report.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"tenant={payload['tenant_id']} property={payload['property_id']}"
            f" business_date={payload['business_date']} dry_run={payload['dry_run']}"
        )
        for change in payload["changes"]:
            print(f"  {change['action']:>16}  {change['target']}  {change['detail']}")
        print("eligibility:", json.dumps(payload["facts"]["eligibility"], ensure_ascii=False))

    if args.verify:
        verification = verify_chain(
            dsn=dsn,
            api_base=args.api_base,
            report=payload,
            password=args.password,
            analysis_timeout_seconds=args.analysis_timeout,
        )
        if args.json:
            print(json.dumps({"verification": verification}, ensure_ascii=False, indent=2))
        else:
            steps = verification["steps"]
            print("verify:")
            print("  work_order:", steps["create_work_order"])
            print("  analysis:  ", steps["analysis"])
            print("  assignee:  ", steps["assignee"])
            print("  incidents: ", steps["supervisor_incidents"])
            print("  queue:     ", steps["supervisor_queue"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
