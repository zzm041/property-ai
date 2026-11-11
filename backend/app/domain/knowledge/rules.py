"""knowledge domain rules, moved from the flat app.domain modules (pure move)."""

import hashlib
import re

from .models import (
    KnowledgeVisibility,
    TextChunk,
)

KNOWLEDGE_READ = "knowledge:read"


KNOWLEDGE_MANAGE = "knowledge:manage"


# F1 / t177: the delivery RAG adapter is a frozen artifact (deploy/rag-runtime.ps1
# pins its source commit and sha256). In scope=rag_only it authorizes exactly one
# X-Prompt-Version value and rejects every other one with
# RAG_ONLY_NO_AUTO_AUTHORIZATION, so the app must ask for that value instead of
# whatever text revision happens to be newest in the repository.
RAG_ANSWER_AUTHORIZED_PROMPT_VERSION = "rag-answer-v2"

# v3 and v4 answer texts are written and kept, but they are NOT authorized by the
# frozen adapter yet. Enabling one needs deploy/ai-runtime-prompts.json re-frozen
# with that version and the adapter pin (rag-runtime.ps1 $ExpectedAdapterSha256 /
# $SourceCommit) recomputed. Never relabel a v4 answer as v2 to get past the check:
# that would make the audit trail lie about which prompt produced the answer.
RAG_ANSWER_PROMPT_VERSIONS = (
    RAG_ANSWER_AUTHORIZED_PROMPT_VERSION,
    "rag-answer-v3",
    "rag-answer-v4",
)

RAG_ANSWER_PROMPT_VERSION = RAG_ANSWER_AUTHORIZED_PROMPT_VERSION


DEFAULT_CHUNK_CHARS = 400


DEFAULT_CHUNK_OVERLAP = 40


DEFAULT_RETRIEVAL_TOP_K = 8


DEFAULT_RERANK_TOP_K = 4


DEFAULT_MIN_RELEVANCE = 0.12


def hash_content(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chunk_text(
    text: str,
    *,
    title: str,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[TextChunk, ...]:
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return ()
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        start = 0
        while start < len(paragraph):
            end = min(len(paragraph), start + max_chars)
            pieces.append(paragraph[start:end])
            if end >= len(paragraph):
                break
            start = max(end - overlap, start + 1)
    chunks: list[TextChunk] = []
    for index, piece in enumerate(pieces, start=1):
        locator = f"{title}#chunk-{index}"
        chunks.append(
            TextChunk(
                chunk_no=index,
                text=piece,
                source_locator=locator,
                content_hash=hash_content(piece),
            )
        )
    return tuple(chunks)


def visibilities_for_principal(
    *,
    is_resident: bool,
    is_staff: bool,
    is_manager: bool,
    is_tenant_admin: bool,
    is_system_admin: bool = False,
) -> frozenset[KnowledgeVisibility]:
    values: set[KnowledgeVisibility] = set()
    if is_resident or is_staff or is_manager or is_tenant_admin or is_system_admin:
        values.add(KnowledgeVisibility.RESIDENT)
    if is_staff or is_manager or is_tenant_admin or is_system_admin:
        values.add(KnowledgeVisibility.STAFF_ONLY)
    if is_manager or is_tenant_admin or is_system_admin:
        values.add(KnowledgeVisibility.PROPERTY)
    if is_tenant_admin or is_system_admin:
        values.add(KnowledgeVisibility.TENANT)
    if is_system_admin:
        values.add(KnowledgeVisibility.SYSTEM)
    return frozenset(values)


def milvus_property_id(property_id: int | None) -> int:
    return 0 if property_id is None else property_id