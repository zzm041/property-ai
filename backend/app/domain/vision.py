"""Compatibility shim: the vision domain module now lives in app.domain.work_order.

Kept for compatibility; removed in the second phase.
"""
from app.domain.work_order import *  # noqa: F401,F403
from app.domain.work_order import __all__ as __all__  # noqa: F401
