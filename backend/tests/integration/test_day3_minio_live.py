"""Live ProductionStorageProvider against Compose MinIO. Skips if unreachable."""

from hashlib import sha256
from uuid import uuid4

import pytest

from app.provider.errors import ProviderInvalidOutputError
from tests.integration.live_stack import (
    production_storage_provider,
    require_minio_endpoint,
)


@pytest.fixture(scope="module")
def minio_endpoint() -> str:
    return require_minio_endpoint()


def test_production_storage_put_get_presign_and_isolation(minio_endpoint: str) -> None:
    bucket = "property-ai-day3-live"
    # PA-213: sign with the live stack credentials (env or deploy/.env.delivery) instead
    # of the hardcoded minioadmin/minioadmin sample pair. The sample pair only works on a
    # Compose started with the sample defaults, so on the delivery stack every request was
    # signed with credentials the server rejects and ensure_bucket raised
    # ProviderUnavailableError ("Object storage is unavailable") while MinIO was healthy.
    provider = production_storage_provider(bucket=bucket, presign_ttl_seconds=120)
    provider.ensure_bucket()
    first = provider.register_object(
        mime_type="image/jpeg",
        size_bytes=12,
        tenant_id=9,
        property_id=8,
    )
    second = provider.register_object(
        mime_type="image/png",
        size_bytes=8,
        tenant_id=3,
        property_id=4,
    )
    assert first.storage_key.startswith("t9/p8/")
    assert second.storage_key.startswith("t3/p4/")
    assert first.storage_key != second.storage_key
    assert first.upload_url is not None
    assert first.upload_url.startswith(minio_endpoint)
    assert "X-Amz-Signature=" in first.upload_url
    assert "X-Amz-Security-Token=" not in first.upload_url
    assert "secret_key=" not in first.upload_url.casefold()
    payload = b"day3-live-bytes"
    digest = sha256(payload).hexdigest()
    provider.put_object(first.storage_key, payload, "image/jpeg")
    fetched = provider.get_object(first.storage_key)
    assert fetched == payload
    assert sha256(fetched).hexdigest() == digest
    provider.put_object(second.storage_key, b"other", "image/png")
    assert provider.get_object(first.storage_key) == payload
    with pytest.raises(ProviderInvalidOutputError):
        provider.get_object(f"t9/p8/{uuid4()}")
