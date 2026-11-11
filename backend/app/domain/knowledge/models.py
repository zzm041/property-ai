"""knowledge domain models, moved from the flat app.domain modules (pure move)."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictInt

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


INSUFFICIENT_KNOWLEDGE = "insufficient_knowledge"


KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"


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