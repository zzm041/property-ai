"""knowledge moved into app.domain.knowledge; this module only re-exports it (compatibility layer, removed in phase two)."""

from app.domain.knowledge import *  # noqa: F401,F403
from app.domain.knowledge import __all__ as __all__  # noqa: F401
