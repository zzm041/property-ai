"""Object storage Provider. Development never treats local paths as protocol."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import hmac
import time
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.core.logging import get_logger
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str
    scanner_status: str
    upload_url: str | None = None
    upload_expires_in: int | None = None


class StorageProvider:
    def register_object(
        self,
        *,
        mime_type: str,
        size_bytes: int,
        filename: str | None = None,
        tenant_id: int | None = None,
        property_id: int | None = None,
    ) -> StoredObject:
        raise NotImplementedError

    def put_object(self, storage_key: str, content: bytes, mime_type: str) -> None:
        raise NotImplementedError

    def get_object(self, storage_key: str) -> bytes:
        raise NotImplementedError

    def ensure_bucket(self) -> None:
        return None

    def presign_put(
        self,
        storage_key: str,
        mime_type: str,
        expires_in: int = 300,
    ) -> str | None:
        del storage_key, mime_type, expires_in
        return None


_DEV_OBJECTS: dict[str, bytes] = {}


class DevelopmentStorageProvider(StorageProvider):
    """Opaque development keys. Not a filesystem path and not a public URL.

    Optional persist_dir keeps bytes across process restart without exposing
    the directory as the protocol key.
    """

    def __init__(self, persist_dir: str | None = None) -> None:
        self._persist_dir = Path(persist_dir) if persist_dir else None
        if self._persist_dir is not None:
            self._persist_dir.mkdir(parents=True, exist_ok=True)

    def _persist_path(self, storage_key: str) -> Path | None:
        if self._persist_dir is None:
            return None
        digest = sha256(storage_key.encode("utf-8")).hexdigest()
        return self._persist_dir / digest[:2] / digest

    def register_object(
        self,
        *,
        mime_type: str,
        size_bytes: int,
        filename: str | None = None,
        tenant_id: int | None = None,
        property_id: int | None = None,
    ) -> StoredObject:
        del mime_type, size_bytes, filename
        tenant = tenant_id or 0
        prop = property_id or 0
        return StoredObject(
            storage_key=f"dev-object:{tenant}:{prop}:{uuid4()}",
            scanner_status="skipped_dev",
        )

    def put_object(self, storage_key: str, content: bytes, mime_type: str) -> None:
        del mime_type
        path = self._persist_path(storage_key)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
            try:
                temporary.write_bytes(content)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        _DEV_OBJECTS[storage_key] = content

    def get_object(self, storage_key: str) -> bytes:
        cached = _DEV_OBJECTS.get(storage_key)
        if cached is not None:
            return cached
        path = self._persist_path(storage_key)
        if path is not None and path.is_file():
            data = path.read_bytes()
            _DEV_OBJECTS[storage_key] = data
            return data
        raise ProviderInvalidOutputError("Object was not found")


class ProductionStorageProvider(StorageProvider):
    """S3/MinIO-compatible adapter. Secrets stay in settings, never in API responses."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        presign_ttl_seconds: int = 300,
        client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self.region = region
        self.presign_ttl_seconds = presign_ttl_seconds
        self._client = client

    def register_object(
        self,
        *,
        mime_type: str,
        size_bytes: int,
        filename: str | None = None,
        tenant_id: int | None = None,
        property_id: int | None = None,
    ) -> StoredObject:
        del filename, size_bytes
        tenant = tenant_id or 0
        prop = property_id or 0
        key = f"t{tenant}/p{prop}/{uuid4()}"
        upload_url = self.presign_put(
            key,
            mime_type,
            expires_in=self.presign_ttl_seconds,
        )
        return StoredObject(
            storage_key=key,
            scanner_status="pending",
            upload_url=upload_url,
            upload_expires_in=self.presign_ttl_seconds,
        )

    def put_object(self, storage_key: str, content: bytes, mime_type: str) -> None:
        self._request("PUT", storage_key, content=content, content_type=mime_type)

    def get_object(self, storage_key: str) -> bytes:
        return self._request("GET", storage_key)

    def ensure_bucket(self) -> None:
        """Idempotent bucket create for MinIO/S3-compatible rehearsal."""

        self._request("PUT", "", content=b"", content_type="application/octet-stream")

    def presign_put(
        self,
        storage_key: str,
        mime_type: str,
        expires_in: int = 300,
    ) -> str | None:
        if not self.endpoint or not self._access_key or not self._secret_key:
            return None
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        credential = (
            f"{self._access_key}/{datestamp}/{self.region}/s3/aws4_request"
        )
        canonical_resource = f"/{self.bucket}/{storage_key}"
        query = (
            f"X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Credential={quote(credential, safe='')}"
            f"&X-Amz-Date={amz_date}"
            f"&X-Amz-Expires={max(1, min(expires_in, 600))}"
            f"&X-Amz-SignedHeaders=host"
        )
        canonical_request = (
            f"PUT\n{canonical_resource}\n{query}\n"
            f"host:{self._host()}\n\nhost\nUNSIGNED-PAYLOAD"
        )
        string_to_sign = (
            "AWS4-HMAC-SHA256\n"
            f"{amz_date}\n"
            f"{datestamp}/{self.region}/s3/aws4_request\n"
            f"{sha256(canonical_request.encode()).hexdigest()}"
        )
        signature = _sign(self._secret_key, datestamp, self.region, string_to_sign)
        return f"{self.endpoint}{canonical_resource}?{query}&X-Amz-Signature={signature}"

    def _request(
        self,
        method: str,
        storage_key: str,
        *,
        content: bytes = b"",
        content_type: str = "application/octet-stream",
    ) -> bytes:
        if not self.endpoint or not self._access_key:
            raise ProviderUnavailableError("Object storage is unavailable")
        url = f"{self.endpoint}/{self.bucket}/{storage_key}".rstrip("/")
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = sha256(content).hexdigest()
        canonical_resource = f"/{self.bucket}/{storage_key}".rstrip("/")
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{self._host()}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
        canonical_request = (
            f"{method}\n{canonical_resource}\n\n{canonical_headers}\n"
            f"{signed_headers}\n{payload_hash}"
        )
        string_to_sign = (
            "AWS4-HMAC-SHA256\n"
            f"{amz_date}\n"
            f"{datestamp}/{self.region}/s3/aws4_request\n"
            f"{sha256(canonical_request.encode()).hexdigest()}"
        )
        signature = _sign(self._secret_key, datestamp, self.region, string_to_sign)
        credential = f"{self._access_key}/{datestamp}/{self.region}/s3/aws4_request"
        headers = {
            "Host": self._host(),
            "Content-Type": content_type,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={credential}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }
        own_client = self._client is None
        http = self._client or httpx.Client(timeout=10.0)
        started_at = time.perf_counter()
        operation = "ensure_bucket" if not storage_key else method.lower()
        try:
            response = http.request(method, url, headers=headers, content=content)
        except httpx.TimeoutException as exc:
            logger.warning(
                "provider_failed",
                provider="storage",
                operation=operation,
                error_code="AI.TIMEOUT",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            raise ProviderTimeoutError("Object storage timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "provider_failed",
                provider="storage",
                operation=operation,
                error_code="AI.UNAVAILABLE",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            raise ProviderUnavailableError("Object storage is unavailable") from exc
        finally:
            if own_client:
                http.close()
        if not storage_key and response.status_code in {200, 201, 204, 409}:
            return b""
        if response.status_code == 404:
            logger.warning(
                "provider_failed",
                provider="storage",
                operation=operation,
                error_code="AI.INVALID_OUTPUT",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            raise ProviderInvalidOutputError("Object was not found")
        if response.status_code >= 400:
            logger.warning(
                "provider_failed",
                provider="storage",
                operation=operation,
                error_code="AI.UNAVAILABLE",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            raise ProviderUnavailableError("Object storage is unavailable")
        logger.info(
            "provider_succeeded",
            provider="storage",
            operation=operation,
            latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return response.content

    def _host(self) -> str:
        return self.endpoint.split("://", 1)[-1]


def _sign(secret: str, datestamp: str, region: str, string_to_sign: str) -> str:
    def keyed(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode(), sha256).digest()

    date_key = keyed(f"AWS4{secret}".encode(), datestamp)
    region_key = keyed(date_key, region)
    service_key = keyed(region_key, "s3")
    signing_key = keyed(service_key, "aws4_request")
    return hmac.new(signing_key, string_to_sign.encode(), sha256).hexdigest()
