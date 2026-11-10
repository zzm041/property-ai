"""Compatibility layer for app.domain.phone — delete in phase 2b.

Content moved to app.domain.identity (models/rules); this module only re-exports.
"""
from app.domain.identity import *  # noqa: F401,F403
from app.domain.identity import __all__ as __all__  # noqa: F401
