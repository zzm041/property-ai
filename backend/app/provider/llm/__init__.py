"""LLM Provider. Production must not import vendor SDKs in services."""

from collections.abc import Mapping
from typing import Protocol

import re

from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.knowledge import RAG_ANSWER_PROMPT_VERSION
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

SUCCESS_ANALYSIS = {
    "category": "electrical",
    "subcategory": "outlet_spark",
    "severity": "high",
    "priority": "p2",
    "summary": "Kitchen electrical outlet sparking",
    "risk": "electric",
    "required_role": "STAFF",
    "required_skill": "electrician",
    "confidence": 0.86,
}

LOW_CONFIDENCE_ANALYSIS = {
    **SUCCESS_ANALYSIS,
    "category": "other",
    "confidence": 0.21,
    "summary": "Unclear resident report",
}

INCIDENT_SUMMARY_SUCCESS = {
    "summary": "Repeated electrical complaints in the same building",
    "affected_scope": "Building 1",
    "common_symptom": "outlet sparking",
    "suggested_handling_note": "Inspect riser and shared circuit",
}

ANNOUNCEMENT_DRAFT_SUCCESS = {
    "title": "Electrical inspection in progress",
    "content": "Property staff are inspecting reported outlet issues. Keep the area clear.",
}


class LLMProvider(Protocol):
    model_version: str

    def structured_completion(
        self,
        *,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, object]:
        """Return a JSON-compatible mapping. Never return prose as control."""


class DevelopmentLLMProvider:
    """Deterministic fake. Behavior is selected by settings, not the network."""

    model_version = "dev-llm-v1"

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    def structured_completion(
        self,
        *,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, object]:
        if self.behavior == "timeout":
            raise ProviderTimeoutError("LLM timed out")
        if self.behavior == "unavailable":
            raise ProviderUnavailableError("LLM is unavailable")
        if self.behavior == "invalid":
            return {"category": "???", "confidence": "hot"}
        if self.behavior == "low_confidence":
            return dict(LOW_CONFIDENCE_ANALYSIS)
        if prompt_version == WORK_ORDER_ANALYSIS_PROMPT_VERSION:
            return dict(SUCCESS_ANALYSIS)
        if prompt_version == RAG_ANSWER_PROMPT_VERSION:
            return _rag_answer(prompt)
        if "incident" in prompt_version:
            return dict(INCIDENT_SUMMARY_SUCCESS)
        return dict(ANNOUNCEMENT_DRAFT_SUCCESS)


def _rag_answer(prompt: str) -> dict[str, object]:
    ids = tuple(
        int(item)
        for item in re.findall(r"chunk-id:(\d+)", prompt)
    )
    if not ids:
        return {
            "status": "insufficient_knowledge",
            "answer": "",
            "citation_chunk_ids": [],
        }
    question = prompt.rsplit("Question:", 1)[-1].strip()
    if "忽略" in question or "ignore" in question.casefold():
        answer = "装修施工时间为工作日 08:00-18:00，周末及法定节假日不得施工。"
        return {
            "status": "answered",
            "answer": answer,
            "citation_chunk_ids": list(ids[:2]),
        }
    return {
        "status": "answered",
        "answer": "根据物业知识库，装修施工时间为工作日 08:00-18:00。",
        "citation_chunk_ids": list(ids[:2]),
    }
