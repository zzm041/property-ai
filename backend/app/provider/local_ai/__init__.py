"""Local AI adapter (package restructure phase 1: split, no behaviour change)."""
from .runtime_control import *  # noqa: F401,F403
from .http_api import main, serve  # noqa: F401
