"""Seed a fully synthetic demo dataset (PA-005 / D5).

本脚本只写演示库，并且只在两个守卫同时满足时才允许写入：

* 开关：环境变量 ``DEMO_SEED_PROFILE`` 必须是 ``synthetic``（默认关闭，不设即不写）；
* 目标库：连接串里的库名必须正好是 ``property_ai_demo_synth``，否则 ``sys.exit(1)``。

数据全部虚构：小区名、地址、姓名、手机号均为编造。手机号统一落在
``138-0000-00xx`` 不可路由段，不会指向真实号码。脚本不创建任何工单
（0 预置工单），真实库 ``property_ai_delivery_check`` 不删不改。

用法::

    # 只打印计划，不连库、不写（默认）
    python backend/scripts/seed_demo_synthetic.py --plan

    # 真正写入演示库（需要 DEMO_SEED_PROFILE=synthetic 且库名正确）
    DEMO_SEED_PROFILE=synthetic DATABASE_URL=mysql+pymysql://.../property_ai_demo_synth \
        python backend/scripts/seed_demo_synthetic.py --apply

小区名的现实性核对（1 号 D5 要求"不动手就撞名"）：落名之前检索过
「拾翠里」「松间雅苑」，未命中住宅小区；同批候选「锦澜华庭」（天津）、
「云栖里」（上海碧桂园）、「望江雅苑」（金华）、「凤栖云筑」（乌鲁木齐）
均已被占用，故弃用。演示数据中小区/住户/联系方式均为虚构，如有同名纯属巧合。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REQUIRED_DATABASE = "property_ai_demo_synth"
REQUIRED_PROFILE = "synthetic"
DEMO_PASSWORD = "shicuili-2026"  # 仅演示库使用，非生产口令
PHONE_PREFIX = "+861380000"
START_PHONE_SERIAL = 12  # 138-0000-0012 起，避免与测试固定号 13800000000 冲突


@dataclass(frozen=True)
class SitePlan:
    name: str
    address: str
    buildings: tuple[str, ...] = ("1栋", "2栋")
    units: tuple[str, ...] = ("1单元", "2单元")
    rooms_per_unit: int = 4


SITES: tuple[SitePlan, ...] = (
    SitePlan(name="拾翠里", address="上海市闵行区溪柳路 18 弄"),
    SitePlan(name="松间雅苑", address="上海市嘉定区松隐路 66 号"),
)

DEPARTMENTS = (("ENG", "工程部"), ("CS", "客服部"))
DUTIES = (("MAINTENANCE", "维修"), ("CLEANING", "保洁"), ("SUPERVISION", "主管"))
SKILLS = (("PLUMBING", "水暖"), ("ELECTRICAL", "电工"), ("HVAC", "空调"), ("CLEANING", "清洁"))
CATEGORIES = ("PLUMBING", "ELECTRICAL", "HVAC", "CLEANING", "SECURITY", "OTHER")

# SLA 只给演示库一组可解释的默认值；生产口径以 docs/ 与库内配置为准。
SLA_BY_PRIORITY = {
    "HIGH": {"accept": 10, "respond": 30, "complete": 480, "warning": 120, "escalation": 60},
    "MEDIUM": {"accept": 20, "respond": 120, "complete": 1440, "warning": 240, "escalation": 120},
    "LOW": {"accept": 30, "respond": 480, "complete": 4320, "warning": 1080, "escalation": 240},
}

RESIDENT_NAMES = (
    "陈知远", "林砚秋", "周景行", "何嘉禾", "许清和", "沈书言",
    "顾长明", "苏若宁", "叶承宇", "温以宁", "谢知微", "白鹭洲",
)
STAFF_NAMES = ("任向东", "邱怀民", "郑晓峰", "石广川", "邵德水", "钟立言")
ADMIN_NAMES = ("柳青禾", "骆云开")


@dataclass
class Plan:
    tenants: int = 1
    properties: int = 0
    buildings: int = 0
    units: int = 0
    houses: int = 0
    users: int = 0
    user_phones: int = 0
    user_passwords: int = 0
    user_roles: int = 0
    departments: int = 0
    duties: int = 0
    skills: int = 0
    employees: int = 0
    employee_properties: int = 0
    employee_duties: int = 0
    employee_skills: int = 0
    resident_house_relations: int = 0
    sla_policies: int = 0
    sla_policy_versions: int = 0
    work_orders: int = 0
    phones: list[str] = field(default_factory=list)

    def bump(self, **kwargs: int) -> None:
        for key, value in kwargs.items():
            setattr(self, key, getattr(self, key) + value)


def build_plan() -> Plan:
    plan = Plan()
    serial = START_PHONE_SERIAL
    for site in SITES:
        plan.properties += 1
        plan.buildings += len(site.buildings)
        plan.units += len(site.buildings) * len(site.units)
        houses_per_site = len(site.buildings) * len(site.units) * site.rooms_per_unit
        plan.houses += houses_per_site
        # 每小区：6 户业主 + 1 物业主管 + 1 管理员 + 2 员工
        people = 6 + 1 + 1 + 2
        plan.users += people
        plan.user_phones += people
        plan.user_passwords += people
        plan.user_roles += people
        plan.employees += 3
        plan.employee_properties += 3
        plan.employee_duties += 3
        plan.employee_skills += 3
        plan.resident_house_relations += 6
        plan.departments += len(DEPARTMENTS)
        plan.sla_policies += len(CATEGORIES) * len(SLA_BY_PRIORITY)
        plan.sla_policy_versions += len(CATEGORIES) * len(SLA_BY_PRIORITY)
        for index in range(people):
            plan.phones.append(f"{PHONE_PREFIX}{serial + index:04d}")
        serial += people
    plan.duties = len(DUTIES)
    plan.skills = len(SKILLS)
    return plan


def guard(profile: str | None, database: str | None) -> str:
    """返回放行原因；不满足守卫时直接退出。"""
    if profile != REQUIRED_PROFILE:
        print(
            f"[skip] DEMO_SEED_PROFILE={profile!r}，合成演示开关未打开，不写任何数据。"
            f"需要 DEMO_SEED_PROFILE={REQUIRED_PROFILE}。",
            file=sys.stderr,
        )
        sys.exit(0)
    if database != REQUIRED_DATABASE:
        print(
            f"[abort] 目标库是 {database!r}，合成种子只允许写 {REQUIRED_DATABASE!r}。",
            file=sys.stderr,
        )
        sys.exit(1)
    return f"profile={profile} database={database}"


def print_plan(plan: Plan) -> None:
    print("== 合成演示数据计划（0 预置工单）==")
    for site in SITES:
        print(f"  小区: {site.name} / {site.address}")
    rows = [
        ("tenants", plan.tenants), ("properties", plan.properties),
        ("buildings", plan.buildings), ("units", plan.units), ("houses", plan.houses),
        ("users", plan.users), ("user_phones", plan.user_phones),
        ("user_passwords", plan.user_passwords), ("user_roles", plan.user_roles),
        ("departments", plan.departments), ("duties", plan.duties), ("skills", plan.skills),
        ("employees", plan.employees), ("employee_properties", plan.employee_properties),
        ("employee_duties", plan.employee_duties), ("employee_skills", plan.employee_skills),
        ("resident_house_relations", plan.resident_house_relations),
        ("sla_policies", plan.sla_policies), ("sla_policy_versions", plan.sla_policy_versions),
        ("work_orders", plan.work_orders),
    ]
    for name, count in rows:
        print(f"  {name:26s}{count}")
    print(f"  手机号段: {plan.phones[0]} … {plan.phones[-1]}（共 {len(plan.phones)} 个不可路由号）")


def apply_plan(plan: Plan) -> int:
    from app.core.config import get_settings
    from app.core.database import create_database_engine, get_session_factory
    from app.core.passwords import hash_password
    from app.model.work_order import SlaPolicy, SlaPolicyVersion
    from app.model.employee import (
        Department, Duty, Employee, EmployeeDuty, EmployeeProperty, EmployeeSkill, Skill,
    )
    from app.model.identity import Role, User, UserPassword, UserPhone, UserRole
    from app.model.property import Building, House, Property, Tenant, Unit
    from app.model.resident import ResidentHouseRelation

    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = get_session_factory(settings.database_url)
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        tenant_name = f"{SITES[0].name}物业服务中心"
        existing = session.query(Tenant).filter(Tenant.name == tenant_name).one_or_none()
        if existing is not None:
            print(f"[abort] 演示库中已存在租户 {tenant_name!r}（id={existing.id}），"
                  "为避免重复写，本次不执行。清库请单独批准。", file=sys.stderr)
            return 2

        roles = {role.code: role for role in session.query(Role).all()}
        missing = {"RESIDENT", "STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"} - set(roles)
        if missing:
            print(f"[abort] roles 表缺少 {sorted(missing)}，先跑迁移再 seed。", file=sys.stderr)
            return 3

        tenant = Tenant(name=tenant_name, status="ACTIVE", contact=None)
        session.add(tenant)
        session.flush()

        duties = {}
        for code, name in DUTIES:
            duty = Duty(tenant_id=tenant.id, code=code, name=name, status="ACTIVE")
            session.add(duty)
            duties[code] = duty
        skills = {}
        for code, name in SKILLS:
            skill = Skill(tenant_id=tenant.id, code=code, name=name, status="ACTIVE")
            session.add(skill)
            skills[code] = skill
        session.flush()

        serial = START_PHONE_SERIAL
        resident_cursor = 0
        staff_cursor = 0

        def add_person(display_name: str, role_code: str, *, scope_type: str,
                       tenant_id: int | None, property_id: int | None) -> User:
            nonlocal serial
            user = User(status="ACTIVE", display_name=display_name, profile_completed_at=now)
            session.add(user)
            session.flush()
            phone = f"{PHONE_PREFIX}{serial:04d}"
            serial += 1
            session.add(UserPhone(
                user_id=user.id, phone_e164=phone, status="ACTIVE",
                verified_at=now, ended_at=None, active_phone_key=1,
            ))
            session.add(UserPassword(
                user_id=user.id, password_hash=hash_password(DEMO_PASSWORD),
                password_algo="argon2id",
            ))
            session.add(UserRole(
                user_id=user.id, role_id=roles[role_code].id, scope_type=scope_type,
                tenant_id=tenant_id, property_id=property_id,
                system_scope_marker=1 if scope_type == "SYSTEM" else None,
                tenant_scope_key=tenant_id if scope_type == "TENANT" else None,
                property_scope_key=property_id if scope_type == "PROPERTY" else None,
            ))
            return user

        for admin_cursor, site in enumerate(SITES):
            property_row = Property(
                tenant_id=tenant.id, name=site.name, address=site.address,
                timezone="Asia/Shanghai",
            )
            session.add(property_row)
            session.flush()

            departments = {}
            for code, name in DEPARTMENTS:
                dept = Department(
                    tenant_id=tenant.id, property_id=property_row.id,
                    code=f"{code}-{property_row.id}", name=name, status="ACTIVE",
                )
                session.add(dept)
                departments[code] = dept
            session.flush()

            house_ids: list[int] = []
            for building_code in site.buildings:
                building = Building(
                    tenant_id=tenant.id, property_id=property_row.id,
                    code=building_code, name=f"{site.name}{building_code}",
                )
                session.add(building)
                session.flush()
                for unit_code in site.units:
                    unit = Unit(
                        tenant_id=tenant.id, property_id=property_row.id,
                        building_id=building.id, code=unit_code,
                        name=f"{building_code}{unit_code}",
                    )
                    session.add(unit)
                    session.flush()
                    for index in range(site.rooms_per_unit):
                        floor = 3 + index * 3
                        house = House(
                            tenant_id=tenant.id, property_id=property_row.id,
                            unit_id=unit.id, room_no=f"{floor:02d}0{index + 1}",
                            floor=floor, status="ACTIVE",
                        )
                        session.add(house)
                        session.flush()
                        house_ids.append(house.id)

            for house_id in house_ids[:6]:
                name = RESIDENT_NAMES[resident_cursor % len(RESIDENT_NAMES)]
                resident_cursor += 1
                resident = add_person(
                    name, "RESIDENT", scope_type="PROPERTY",
                    tenant_id=tenant.id, property_id=property_row.id,
                )
                session.add(ResidentHouseRelation(
                    user_id=resident.id, tenant_id=tenant.id, property_id=property_row.id,
                    house_id=house_id, relation_type="OWNER", status="ACTIVE",
                    valid_from=now, valid_to=None, bind_request_id=None,
                    active_house_key=house_id,
                ))

            admin_user = add_person(
                ADMIN_NAMES[admin_cursor % len(ADMIN_NAMES)], "PROPERTY_ADMIN",
                scope_type="PROPERTY", tenant_id=tenant.id, property_id=property_row.id,
            )

            for duty_code, skill_codes in (
                ("SUPERVISION", ("PLUMBING", "ELECTRICAL")),
                ("MAINTENANCE", ("PLUMBING", "HVAC")),
                ("CLEANING", ("CLEANING",)),
            ):
                name = STAFF_NAMES[staff_cursor % len(STAFF_NAMES)]
                staff_cursor += 1
                role_code = "SUPERVISOR" if duty_code == "SUPERVISION" else "STAFF"
                staff_user = add_person(
                    name, role_code, scope_type="PROPERTY",
                    tenant_id=tenant.id, property_id=property_row.id,
                )
                employee = Employee(
                    user_id=staff_user.id, tenant_id=tenant.id,
                    employee_no=f"SCL-{property_row.id}-{duty_code[:3]}",
                    department_id=departments["ENG" if duty_code != "CLEANING" else "CS"].id,
                    status="ACTIVE", max_capacity=5,
                )
                session.add(employee)
                session.flush()
                session.add(EmployeeProperty(
                    employee_id=employee.id, tenant_id=tenant.id, property_id=property_row.id,
                    status="ACTIVE", valid_from=now, valid_to=None,
                    active_property_key=property_row.id,
                ))
                session.add(EmployeeDuty(
                    employee_id=employee.id, tenant_id=tenant.id,
                    duty_id=duties[duty_code].id,
                ))
                for skill_code in skill_codes:
                    session.add(EmployeeSkill(
                        employee_id=employee.id, tenant_id=tenant.id,
                        skill_id=skills[skill_code].id,
                    ))

            for category in CATEGORIES:
                for priority, window in SLA_BY_PRIORITY.items():
                    policy = SlaPolicy(
                        tenant_id=tenant.id, property_id=property_row.id,
                        category=category, priority=priority,
                        respond_minutes=window["respond"], complete_minutes=window["complete"],
                        status="ACTIVE", property_scope_key=property_row.id,
                    )
                    session.add(policy)
                    session.flush()
                    session.add(SlaPolicyVersion(
                        policy_id=policy.id, version_no=1,
                        accept_minutes=window["accept"], respond_minutes=window["respond"],
                        complete_minutes=window["complete"],
                        warning_lead_minutes=window["warning"],
                        escalation_minutes=window["escalation"], status="ACTIVE",
                        change_reason="合成演示库初始 SLA 配置",
                        created_by_user_id=admin_user.id,
                        effective_from=now, effective_to=None,
                    ))

        add_person(
            "演示租户管理员", "TENANT_ADMIN", scope_type="TENANT",
            tenant_id=tenant.id, property_id=None,
        )
        session.commit()

        counts = {
            "tenants": session.query(Tenant).count(),
            "properties": session.query(Property).count(),
            "buildings": session.query(Building).count(),
            "units": session.query(Unit).count(),
            "houses": session.query(House).count(),
            "users": session.query(User).count(),
            "employees": session.query(Employee).count(),
            "resident_house_relations": session.query(ResidentHouseRelation).count(),
            "sla_policies": session.query(SlaPolicy).count(),
            "work_orders": 0,
        }
    engine.dispose()

    print(f"== 当前指向合成库：{REQUIRED_DATABASE} ==")
    for name, count in counts.items():
        print(f"  {name:26s}{count}")
    print(f"  演示账号口令: {DEMO_PASSWORD}（仅演示库）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写入纯合成演示数据（PA-005 / D5）")
    parser.add_argument("--apply", action="store_true", help="真正写入演示库（默认只打印计划）")
    parser.add_argument("--plan", action="store_true", help="只打印计划，不连库不写")
    args = parser.parse_args(argv)

    import os
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    settings = get_settings()
    database = make_url(settings.active_database_url).database
    reason = guard(os.environ.get("DEMO_SEED_PROFILE"), database)
    plan = build_plan()

    if not args.apply:
        print(f"[plan] 开关与库名守卫通过: {reason}")
        print_plan(plan)
        print("[plan] 未写入任何数据。加 --apply 才会落库。")
        return 0

    print(f"[apply] 守卫通过: {reason}")
    print_plan(plan)
    return apply_plan(plan)


if __name__ == "__main__":
    raise SystemExit(main())
