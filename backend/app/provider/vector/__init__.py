"""Milvus collection wrapper. Metadata filter is the first ACL layer."""

from collections.abc import Sequence
from typing import Any
import json

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from app.core.config import Settings
from app.provider.errors import ProviderUnavailableError


class MilvusKnowledgeStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._alias = "property_ai"
        self._connected = False

    @property
    def collection_name(self) -> str:
        return self._settings.milvus_collection

    def connect(self) -> None:
        if self._connected:
            return
        try:
            if connections.has_connection(self._alias):
                self._connected = True
                return
            connections.connect(
                alias=self._alias,
                uri=self._settings.milvus_uri,
                timeout=self._settings.milvus_timeout_seconds,
            )
            self._connected = True
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError("Milvus is unavailable") from exc

    def ensure_collection(self, dimensions: int) -> Collection:
        self.connect()
        if utility.has_collection(self.collection_name, using=self._alias):
            collection = Collection(self.collection_name, using=self._alias)
            self._validate_collection(collection, dimensions)
            collection.load()
            return collection
        schema = CollectionSchema(
            description=self._model_metadata(),
            fields=[
                FieldSchema(name="chunk_id", dtype=DataType.INT64, is_primary=True, auto_id=False),
                FieldSchema(name="tenant_id", dtype=DataType.INT64),
                FieldSchema(name="property_id", dtype=DataType.INT64),
                FieldSchema(name="visibility", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="document_id", dtype=DataType.INT64),
                FieldSchema(name="document_version", dtype=DataType.INT64),
                FieldSchema(
                    name="vector",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=dimensions,
                ),
            ]
        )
        collection = Collection(
            name=self.collection_name,
            schema=schema,
            using=self._alias,
        )
        collection.create_index(
            field_name="vector",
            index_params={
                "index_type": "FLAT",
                "metric_type": self._settings.milvus_metric_type,
                "params": {},
            },
        )
        collection.load()
        return collection

    def _model_metadata(self) -> str:
        if not self._settings.embedding_revision:
            return ''  # Legacy collections retain their existing schema/metadata.
        return json.dumps({'contract':'embedding-v1','model':self._settings.embedding_model,
            'revision':self._settings.embedding_revision,'normalization':self._settings.embedding_normalization},sort_keys=True)

    def _validate_collection(self, collection: Collection, dimensions: int) -> None:
        vectors=[f for f in collection.schema.fields if f.name=='vector']
        if len(vectors)!=1 or vectors[0].params.get('dim')!=dimensions:
            raise ProviderUnavailableError('Milvus vector dimension mismatch; no mutation performed')
        indexes=[i.params for i in collection.indexes if i.field_name=='vector']
        if len(indexes)!=1 or indexes[0].get('metric_type')!=self._settings.milvus_metric_type:
            raise ProviderUnavailableError('Milvus metric mismatch; no mutation performed')
        metadata=self._model_metadata()
        if metadata and collection.schema.description!=metadata:
            raise ProviderUnavailableError('Milvus model metadata mismatch; no mutation performed')

    def upsert(
        self,
        *,
        chunk_ids: Sequence[int],
        tenant_ids: Sequence[int],
        property_ids: Sequence[int],
        visibilities: Sequence[str],
        document_ids: Sequence[int],
        document_versions: Sequence[int],
        vectors: Sequence[Sequence[float]],
        dimensions: int,
    ) -> None:
        collection = self.ensure_collection(dimensions)
        try:
            collection.upsert(
                [
                    list(chunk_ids),
                    list(tenant_ids),
                    list(property_ids),
                    list(visibilities),
                    list(document_ids),
                    list(document_versions),
                    [list(item) for item in vectors],
                ]
            )
            collection.flush()
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError("Milvus upsert failed") from exc

    def search(
        self,
        *,
        vector: Sequence[float],
        expr: str,
        top_k: int,
        dimensions: int,
    ) -> tuple[dict[str, Any], ...]:
        collection = self.ensure_collection(dimensions)
        try:
            results = collection.search(
                data=[list(vector)],
                anns_field="vector",
                param={"metric_type": self._settings.milvus_metric_type, "params": {}},
                limit=top_k,
                expr=expr,
                output_fields=[
                    "chunk_id",
                    "tenant_id",
                    "property_id",
                    "visibility",
                    "document_id",
                    "document_version",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError("Milvus search failed") from exc
        hits: list[dict[str, Any]] = []
        for hit in results[0]:
            entity = hit.entity
            hits.append(
                {
                    "chunk_id": int(entity.get("chunk_id")),
                    "tenant_id": int(entity.get("tenant_id")),
                    "property_id": int(entity.get("property_id")),
                    "visibility": str(entity.get("visibility")),
                    "document_id": int(entity.get("document_id")),
                    "document_version": int(entity.get("document_version")),
                    "score": float(hit.score),
                }
            )
        return tuple(hits)

    def delete_ids(self, chunk_ids: Sequence[int], dimensions: int) -> None:
        if not chunk_ids:
            return
        collection = self.ensure_collection(dimensions)
        id_list = ", ".join(str(item) for item in chunk_ids)
        collection.delete(expr=f"chunk_id in [{id_list}]")
        collection.flush()

    def drop_collection(self) -> None:
        self.connect()
        if utility.has_collection(self.collection_name, using=self._alias):
            utility.drop_collection(self.collection_name, using=self._alias)
