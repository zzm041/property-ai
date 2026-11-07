"""Default-deny permission and organization-scope authorization."""

from app.core.errors import AppError, IamErrorCode
from app.domain.iam import OrganizationScope, Principal


class RbacService:
    @staticmethod
    def require_permission(
        principal: Principal,
        permission_code: str,
        target_scope: OrganizationScope,
    ) -> None:
        if not principal.has_permission(permission_code, target_scope):
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
