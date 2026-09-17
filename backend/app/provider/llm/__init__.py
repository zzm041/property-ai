"""LLM Provider. Production must not import vendor SDKs in services."""

from collections.abc import Mapping
from typing import Protocol

import re

import httpx

from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.knowledge import RAG_ANSWER_PROMPT_VERSION
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.http import parse_json_object, request_json

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


class QwenLLMProvider:
    """OpenAI-compatible Qwen chat completions. Services never import vendor SDKs."""

    model_version = "qwen"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client
        self.model_version = model or self.model_version

    def structured_completion(
        self,
        *,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, object]:
        del prompt_version
        if not self.base_url or not self._api_key:
            raise ProviderUnavailableError("LLM is unavailable")
        payload = request_json(
            method="POST",
            url=f"{self.base_url}/chat/completions",
            timeout_seconds=self.timeout_seconds,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json_body={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            client=self._client,
            provider="llm",
            operation="structured_completion",
        )
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderInvalidOutputError("LLM returned invalid JSON") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderInvalidOutputError("LLM returned an empty response")
        return parse_json_object(content)
