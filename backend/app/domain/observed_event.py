"""Compatibility shim: the observed_event domain module now lives in app.domain.ai.

Kept for compatibility; removed in the second phase.
"""
from app.domain.ai import *  # noqa: F401,F403
from app.domain.ai import __all__ as __all__  # noqa: F401
