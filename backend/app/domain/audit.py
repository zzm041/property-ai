"""audit moved into app.domain.governance; this module only re-exports it (compatibility layer, removed in phase two)."""

from app.domain.governance import *  # noqa: F401,F403
from app.domain.governance import __all__ as __all__  # noqa: F401
