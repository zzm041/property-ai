"""Real Milvus 2.5 metadata-filtered search. Embedding may be fake."""

from uuid import uuid4

from app.core.config import Settings
from app.provider.embedding import DevelopmentEmbeddingProvider
from app.provider.vector import MilvusKnowledgeStore


def test_milvus_metadata_filter_isolates_tenant_and_property() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        milvus_collection=f"t8_milvus_{uuid4().hex[:8]}",
        embedding_dimensions=8,
    )
    store = MilvusKnowledgeStore(settings)
    embedder = DevelopmentEmbeddingProvider(dimensions=8)
    left, right, other = embedder.embed(
        (
            "装修施工时间为工作日八点到六点",
            "装修时间工作日",
            "电梯年检计划",
        )
    )
    store.upsert(
        chunk_ids=(101, 202, 303),
        tenant_ids=(1, 1, 2),
        property_ids=(11, 22, 11),
        visibilities=("RESIDENT", "RESIDENT", "RESIDENT"),
        document_ids=(1, 2, 3),
        document_versions=(1, 1, 1),
        vectors=(left, right, other),
        dimensions=8,
    )
    hits = store.search(
        vector=left,
        expr='tenant_id == 1 && property_id in [11, 0] && visibility in ["RESIDENT"]',
        top_k=8,
        dimensions=8,
    )
    ids = {item["chunk_id"] for item in hits}
    assert 101 in ids
    assert 202 not in ids
    assert 303 not in ids
    store.delete_ids((101,), dimensions=8)
    after_delete = store.search(
        vector=left,
        expr="tenant_id == 1 && property_id == 11",
        top_k=8,
        dimensions=8,
    )
    assert all(item["chunk_id"] != 101 for item in after_delete)
    store.drop_collection()
