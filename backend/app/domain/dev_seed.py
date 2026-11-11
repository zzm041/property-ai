"""dev_seed moved into app.domain.dev; this module only re-exports it (compatibility layer, removed in phase two)."""

from app.domain.dev import *  # noqa: F401,F403
from app.domain.dev import __all__ as __all__  # noqa: F401
