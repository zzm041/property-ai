"""Knowledge visibility, chunking, and RAG ACL. Vectors are not the permission source.

The historical flat modules re-export this package as compatibility shims.

This domain has no errors of its own: everything uses the app.core.errors families
instead of defining a parallel hierarchy.
"""

from .models import (
    INSUFFICIENT_KNOWLEDGE,
    KNOWLEDGE_UNAVAILABLE,
    KnowledgeDocumentStatus,
    KnowledgeDocumentType,
    KnowledgeIngestionStatus,
    KnowledgeVersionStatus,
    KnowledgeVisibility,
    RagAnswerOutput,
    RagAnswerStatus,
    TextChunk,
)
from .rules import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_MIN_RELEVANCE,
    DEFAULT_RERANK_TOP_K,
    DEFAULT_RETRIEVAL_TOP_K,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    RAG_ANSWER_AUTHORIZED_PROMPT_VERSION,
    RAG_ANSWER_PROMPT_VERSION,
    RAG_ANSWER_PROMPT_VERSIONS,
    chunk_text,
    hash_content,
    milvus_property_id,
    visibilities_for_principal,
)

__all__ = [
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_MIN_RELEVANCE",
    "DEFAULT_RERANK_TOP_K",
    "DEFAULT_RETRIEVAL_TOP_K",
    "INSUFFICIENT_KNOWLEDGE",
    "KNOWLEDGE_MANAGE",
    "KNOWLEDGE_READ",
    "KNOWLEDGE_UNAVAILABLE",
    "KnowledgeDocumentStatus",
    "KnowledgeDocumentType",
    "KnowledgeIngestionStatus",
    "KnowledgeVersionStatus",
    "KnowledgeVisibility",
    "RAG_ANSWER_AUTHORIZED_PROMPT_VERSION",
    "RAG_ANSWER_PROMPT_VERSION",
    "RAG_ANSWER_PROMPT_VERSIONS",
    "RagAnswerOutput",
    "RagAnswerStatus",
    "TextChunk",
    "chunk_text",
    "hash_content",
    "milvus_property_id",
    "visibilities_for_principal",
]
