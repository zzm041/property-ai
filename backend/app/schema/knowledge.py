"""Knowledge, assistant, WeChat, and notification API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WeChatLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)


class CreateKnowledgeDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    document_type: str = Field(min_length=1, max_length=32)
    visibility: str = Field(min_length=1, max_length=32)
    source_text: str = Field(min_length=1, max_length=20000)
    tenant_id: int | None = Field(default=None, gt=0)


class CreateKnowledgeVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=20000)
    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class ArchiveKnowledgeDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class AssistantQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    property_id: int | None = Field(default=None, gt=0)
    tenant_id: int | None = Field(default=None, gt=0)


class KnowledgeCitationData(BaseModel):
    document_id: int
    title: str
    version: int
    source_locator: str
    excerpt: str


class AssistantAnswerData(BaseModel):
    status: str
    answer: str
    citations: list[KnowledgeCitationData]


class KnowledgeDocumentData(BaseModel):
    id: int
    tenant_id: int
    property_id: int | None
    title: str
    document_type: str
    visibility: str
    status: str
    current_version: int | None
    created_at: datetime


class KnowledgeIngestionData(BaseModel):
    document_id: int
    version: int
    job_status: str
    content_hash: str


class KnowledgeVersionData(BaseModel):
    version: int
    status: str
    content_hash: str
    created_at: datetime


class KnowledgeIngestionJobData(BaseModel):
    version: int
    status: str
    error_code: str | None
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None


class KnowledgeDocumentDetailData(KnowledgeDocumentData):
    versions: list[KnowledgeVersionData]
    ingestion_jobs: list[KnowledgeIngestionJobData]


class KnowledgeDocumentListData(BaseModel):
    items: list[KnowledgeDocumentDetailData]
    next_cursor: int | None = None


class NotificationData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    title: str
    body: str
    resource_type: str
    resource_id: int
    read_at: datetime | None
    created_at: datetime


class NotificationListData(BaseModel):
    items: list[NotificationData]
    unread_count: int
    next_cursor: int | None = None


class UnreadCountData(BaseModel):
    unread_count: int


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccessTokenWithMethodData(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int
    auth_method: Literal["wechat"]
    refresh_token: str | None = None
