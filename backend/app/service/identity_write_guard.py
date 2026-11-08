"""Shared serialization guard for writes that create current user ownership.

Account merge keeps the source row for historical foreign keys.  A foreign-key
check therefore cannot stop a request that authenticated before the merge from
writing new current ownership to a now-MERGED user.  Mutable identity writers
must lock and refresh the User row inside their business transaction before
they lock or create dependent rows.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, IamErrorCode
from app.model.identity import User


def lock_active_identity(session: Session, user_id: int) -> User:
    user = session.scalar(
        select(User)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if user is None or user.status != "ACTIVE":
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Identity is no longer active",
            status_code=403,
        )
    return user


def lock_active_identities(session: Session, user_ids: set[int]) -> tuple[User, ...]:
    return tuple(lock_active_identity(session, user_id) for user_id in sorted(user_ids))
