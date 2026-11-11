"""Authentication dependencies with isolated short-lived Auth Sessions."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.errors import AppError, AuthErrorCode
from app.domain.identity import Principal
from app.repository.identity import IdentityRepository
from app.service.identity.auth import AuthService

bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[AuthService]:
    session = get_session_factory(settings.active_database_url)()
    try:
        yield AuthService(session, IdentityRepository(session))
    finally:
        session.close()


def get_current_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise AppError(
            code=AuthErrorCode.MISSING_TOKEN,
            message="Bearer access token is required",
            status_code=401,
        )

    session = get_session_factory(settings.active_database_url)()
    try:
        return AuthService(
            session,
            IdentityRepository(session),
        ).authenticate_access_token(credentials.credentials, settings)
    finally:
        session.close()
