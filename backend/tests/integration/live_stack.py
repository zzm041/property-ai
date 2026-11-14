"""Live-stack helpers shared by integration suites that hit Compose services.

PA-213: test_day3_minio_live.py and test_day4_mysql_restore_relations.py used to
hardcode the Compose *sample* S3 pair (minioadmin/minioadmin). That pair only
authenticates against a stack started with the sample defaults. The delivery
stack takes its credentials from ${MINIO_ROOT_USER}/${MINIO_ROOT_PASSWORD}
(deploy/docker-compose.delivery.yml:99-100, 120-121) and the ignored
deploy/.env.delivery holds the rotated values, so the hardcoded pair produced a
signed-request 4xx: "ProviderUnavailableError: Object storage is unavailable"
(at app/provider/storage/__init__.py:325, the status>=300 branch). MinIO was up;
the request was simply signed with credentials the server does not accept.

Resolution order (first non-empty wins, per field):
  1. process env MINIO_ROOT_USER / MINIO_ROOT_PASSWORD
  2. deploy/.env.delivery MINIO_ROOT_USER / MINIO_ROOT_PASSWORD
  3. deploy/.env.delivery STORAGE_ACCESS_KEY / STORAGE_SECRET_KEY
     (delivery keeps these in sync with the root pair; they are what the API
     itself signs with, so they are the correct fallback.)

When nothing resolves the suite skips with the missing key names instead of
failing on a 403, because an unprovisioned .env.delivery is an environment gap,
not a storage defect.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DELIVERY_ENV_FILE = REPO_ROOT / "deploy" / ".env.delivery"
MINIO_HEALTH_PATH = "/minio/health/live"
MINIO_ENDPOINTS = ("http://127.0.0.1:9000", "http://127.0.0.1:9010")


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file without importing python-dotenv."""

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


@lru_cache(maxsize=1)
def delivery_env() -> dict[str, str]:
    return read_env_file(DELIVERY_ENV_FILE)


def _first_present(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def minio_credentials() -> tuple[str, str] | None:
    """Return (access_key, secret_key) for the running MinIO, or None."""

    env_file = delivery_env()
    access_key = _first_present(
        os.environ.get("MINIO_ROOT_USER"),
        env_file.get("MINIO_ROOT_USER"),
        env_file.get("STORAGE_ACCESS_KEY"),
    )
    secret_key = _first_present(
        os.environ.get("MINIO_ROOT_PASSWORD"),
        env_file.get("MINIO_ROOT_PASSWORD"),
        env_file.get("STORAGE_SECRET_KEY"),
    )
    if access_key is None or secret_key is None:
        return None
    return access_key, secret_key


def require_minio_credentials() -> tuple[str, str]:
    credentials = minio_credentials()
    if credentials is None:
        pytest.skip(
            "MinIO credentials unavailable: set MINIO_ROOT_USER/MINIO_ROOT_PASSWORD "
            f"or populate {DELIVERY_ENV_FILE}"
        )
    return credentials


def minio_endpoint() -> str | None:
    """Return the first MinIO endpoint that answers the health probe."""

    for endpoint in MINIO_ENDPOINTS:
        try:
            response = httpx.get(f"{endpoint}{MINIO_HEALTH_PATH}", timeout=2.0)
        except httpx.HTTPError:
            continue
        if response.status_code == 200:
            return endpoint
    return None


def require_minio_endpoint() -> str:
    endpoint = minio_endpoint()
    if endpoint is None:
        pytest.skip(
            "Compose MinIO is not reachable on " + " or ".join(MINIO_ENDPOINTS)
        )
    return endpoint


def production_storage_provider(*, bucket: str, presign_ttl_seconds: int = 120):
    """Build a ProductionStorageProvider signed with the live MinIO credentials."""

    from app.provider.storage import ProductionStorageProvider

    access_key, secret_key = require_minio_credentials()
    return ProductionStorageProvider(
        endpoint=require_minio_endpoint(),
        bucket=bucket,
        access_key=access_key,
        secret_key=secret_key,
        region="us-east-1",
        presign_ttl_seconds=presign_ttl_seconds,
    )
