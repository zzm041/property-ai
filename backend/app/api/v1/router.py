"""Version 1 API router."""

from fastapi import APIRouter

from app.api.v1.admin_employee import router as admin_employee_router
from app.api.v1.admin_resident import router as admin_resident_router
from app.api.v1.auth import router as development_auth_router
from app.api.v1.files import router as files_router
from app.api.v1.health import router as health_router
from app.api.v1.me import router as me_router
from app.api.v1.resident import router as resident_router
from app.api.v1.staff_attendance import router as staff_attendance_router
from app.api.v1.staff_work_orders import router as staff_work_orders_router
from app.api.v1.supervisor_staff import router as supervisor_staff_router
from app.api.v1.supervisor_work_orders import router as supervisor_work_orders_router
from app.api.v1.work_orders import router as work_orders_router
from app.core.config import Settings


def build_api_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(me_router)
    router.include_router(resident_router)
    router.include_router(admin_resident_router)
    router.include_router(admin_employee_router)
    router.include_router(staff_attendance_router)
    router.include_router(supervisor_staff_router)
    router.include_router(files_router)
    router.include_router(work_orders_router)
    router.include_router(staff_work_orders_router)
    router.include_router(supervisor_work_orders_router)
    if settings.dev_auth_enabled:
        router.include_router(development_auth_router)
    return router
