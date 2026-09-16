"""FastAPI database session dependency bound to request Settings."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory


def get_db_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[Session]:
    session = get_session_factory(settings.active_database_url)()
    try:
        yield session
    finally:
        session.close()
