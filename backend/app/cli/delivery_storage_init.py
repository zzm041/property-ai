"""Create the delivery object-storage bucket under an exact target guard."""

from __future__ import annotations

import os

from app.core.config import get_settings
from app.provider.factory import build_storage_provider


EXPECTED_ENDPOINT = "http://minio:9000"
EXPECTED_BUCKET = "property-ai-delivery-check"


def _settings():
    settings = get_settings()
    if (
        os.environ.get("PROPERTY_AI_DELIVERY_STORAGE_INIT") != "approved"
        or settings.app_env != "staging"
        or settings.storage_provider != "production"
        or settings.storage_endpoint.rstrip("/") != EXPECTED_ENDPOINT
        or settings.storage_bucket != EXPECTED_BUCKET
    ):
        raise RuntimeError(
            "delivery storage init refused outside the exact MinIO target"
        )
    return settings


def main() -> int:
    provider = build_storage_provider(_settings())
    provider.ensure_bucket()
    print(f"delivery storage bucket ready: {EXPECTED_BUCKET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
