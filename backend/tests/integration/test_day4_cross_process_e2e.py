"""Cross-process HTTP/Nginx/Worker E2E. Skips unless DAY4_BASE_URL is set.

This module must not use TestClient, eager Celery, or in-process Service calls
for the verified path. Fake providers are labeled as Fake.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BASE = os.environ.get("DAY4_BASE_URL", "").rstrip("/")
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "day4_e2e.py"


@pytest.mark.skipif(not BASE, reason="DAY4_BASE_URL is not set")
def test_cross_process_http_nginx_worker_e2e() -> None:
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DAY4_CROSS_PROCESS_E2E=PASS" in result.stdout
    assert "Fake providers" in result.stdout
    assert "not live vendor" in result.stdout
