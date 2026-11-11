"""Version 1 API router."""

from fastapi import APIRouter

from app.api.v1.admin_announcements import router as admin_announcements_router
from app.api.v1.admin_employee import router as admin_employee_router
from app.api.v1.admin_knowledge import router as admin_knowledge_router
from app.api.v1.admin_operations import router as admin_operations_router
from app.api.v1.admin_property import router as admin_property_router
from app.api.v1.admin_resident import router as admin_resident_router
from app.api.v1.admin_sla import router as admin_sla_router
from app.api.v1.announcements import router as announcements_router
from app.api.v1.assistant import router as assistant_router
from app.api.v1.auth import router as development_auth_router
from app.api.v1.credential_auth import router as credential_auth_router
from app.api.v1.employee_claim import router as employee_claim_router
from app.api.v1.files import router as files_router
from app.api.v1.health import router as health_router
from app.api.v1.me import router as me_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.property_catalog import router as property_catalog_router
from app.api.v1.resident import router as resident_router
from app.api.v1.staff_attendance import router as staff_attendance_router
from app.api.v1.staff_work_orders import router as staff_work_orders_router
from app.api.v1.supervisor_ai import router as supervisor_ai_router
from app.api.v1.task_intents import router as task_intents_router
from app.api.v1.supervisor_incidents import router as supervisor_incidents_router
from app.api.v1.supervisor_staff import router as supervisor_staff_router
from app.api.v1.supervisor_work_orders import router as supervisor_work_orders_router
from app.api.v1.system_organization import router as system_organization_router
from app.api.v1.wechat_auth import router as wechat_auth_router
from app.api.v1.identity_recovery import router as identity_recovery_router
from app.api.v1.work_orders import router as work_orders_router
from app.core.config import Settings


def build_api_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(system_organization_router)
    router.include_router(me_router)
    router.include_router(credential_auth_router)
    router.include_router(property_catalog_router)
    router.include_router(resident_router)
    router.include_router(admin_resident_router)
    router.include_router(admin_property_router)
    router.include_router(admin_employee_router)
    router.include_router(staff_attendance_router)
    router.include_router(employee_claim_router)
    router.include_router(supervisor_staff_router)
    router.include_router(files_router)
    router.include_router(work_orders_router)
    router.include_router(staff_work_orders_router)
    router.include_router(supervisor_work_orders_router)
    router.include_router(supervisor_ai_router)
    router.include_router(task_intents_router)
    router.include_router(supervisor_incidents_router)
    router.include_router(admin_announcements_router)
    router.include_router(announcements_router)
    router.include_router(admin_knowledge_router)
    router.include_router(admin_operations_router)
    router.include_router(admin_sla_router)
    router.include_router(assistant_router)
    router.include_router(notifications_router)
    router.include_router(wechat_auth_router)
    router.include_router(identity_recovery_router)
    if settings.dev_auth_enabled:
        router.include_router(development_auth_router)
    return router
