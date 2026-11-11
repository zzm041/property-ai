"""Shared Task 1 test setup."""

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# `scripts/ops/local_ai_http.py` is a Windows-targeted operator tool: it takes the shared
# model lock through `msvcrt.locking`, so importing it fails outright on POSIX. Some unit
# tests import it only for structural assertions and never lock anything, while CI runs
# that subset on Linux. Provide an inert stand-in rather than deleting or skipping them.
if sys.platform != "win32" and "msvcrt" not in sys.modules:
    _msvcrt = types.ModuleType("msvcrt")
    _msvcrt.LK_NBLCK = 0  # type: ignore[attr-defined]
    _msvcrt.LK_UNLCK = 1  # type: ignore[attr-defined]
    _msvcrt.locking = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sys.modules["msvcrt"] = _msvcrt

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
