"""Object storage Provider. Development never treats local paths as protocol."""

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str
    scanner_status: str


class StorageProvider:
    def register_object(
        self,
        *,
        mime_type: str,
        size_bytes: int,
        filename: str | None = None,
    ) -> StoredObject:
        raise NotImplementedError


class DevelopmentStorageProvider(StorageProvider):
    """Opaque development keys. Not a filesystem path and not a public URL."""

    def register_object(
        self,
        *,
        mime_type: str,
        size_bytes: int,
        filename: str | None = None,
    ) -> StoredObject:
        del mime_type, size_bytes, filename
        return StoredObject(
            storage_key=f"dev-object:{uuid4()}",
            scanner_status="skipped_dev",
        )
