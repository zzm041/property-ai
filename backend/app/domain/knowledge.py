"""Knowledge visibility, chunking, and RAG ACL. Vectors are not the permission source."""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

KNOWLEDGE_READ = "knowledge:read"
KNOWLEDGE_MANAGE = "knowledge:manage"
RAG_ANSWER_PROMPT_VERSION = "rag-answer-v4"
DEFAULT_CHUNK_CHARS = 400
DEFAULT_CHUNK_OVERLAP = 40
DEFAULT_RETRIEVAL_TOP_K = 8
DEFAULT_RERANK_TOP_K = 4
DEFAULT_MIN_RELEVANCE = 0.12
INSUFFICIENT_KNOWLEDGE = "insufficient_knowledge"
KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"


class KnowledgeVisibility(StrEnum):
    SYSTEM = "SYSTEM"
    TENANT = "TENANT"
    PROPERTY = "PROPERTY"
    STAFF_ONLY = "STAFF_ONLY"
    RESIDENT = "RESIDENT"


class KnowledgeDocumentStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class KnowledgeVersionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class KnowledgeIngestionStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class KnowledgeDocumentType(StrEnum):
    FAQ = "FAQ"
    GUIDE = "GUIDE"
    RULE = "RULE"
    SOP = "SOP"
    NOTICE = "NOTICE"


class RagAnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_KNOWLEDGE = INSUFFICIENT_KNOWLEDGE
    UNAVAILABLE = KNOWLEDGE_UNAVAILABLE


class RagAnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str = Field(min_length=1, max_length=64)
    answer: str = Field(default="", max_length=4000)
    citation_chunk_ids: tuple[Annotated[StrictInt, Field(gt=0)], ...] = ()


@dataclass(frozen=True, slots=True)
class TextChunk:
    chunk_no: int
    text: str
    source_locator: str
    content_hash: str


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
