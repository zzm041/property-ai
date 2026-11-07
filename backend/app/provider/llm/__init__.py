"""LLM Provider. Production must not import vendor SDKs in services."""

from collections.abc import Mapping
from typing import Protocol

import re

import httpx

from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.knowledge import RAG_ANSWER_PROMPT_VERSION
from app.domain.observed_event import work_order_analysis_response_format
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.http import parse_json_object, request_json
from app.provider.usage import StructuredCompletion, response_token_usage

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
    evidence = tuple(
        (int(chunk_id), excerpt.strip())
        for chunk_id, excerpt in re.findall(
            r"chunk-id:(\d+)\s+locator:[^\n]*\n(.*?)(?=\nchunk-id:\d+\s+locator:|\n\nQuestion:|\Z)",
            prompt,
            flags=re.DOTALL,
        )
        if excerpt.strip()
    )
    if not evidence:
        return {
            "status": "insufficient_knowledge",
            "answer": "",
            "citation_chunk_ids": [],
        }
    return {
        "status": "answered",
        "answer": f"根据物业知识库，{evidence[0][1]}",
        "citation_chunk_ids": [chunk_id for chunk_id, _ in evidence[:2]],
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
        disable_thinking: bool = False,
        max_output_tokens: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client
        self.model_version = model or self.model_version
        self._disable_thinking = disable_thinking
        self._max_output_tokens = max_output_tokens

    def structured_completion(
        self,
        *,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, object]:
        if not self.base_url or not self._api_key:
            raise ProviderUnavailableError("LLM is unavailable")
        payload = request_json(
            method="POST",
            url=f"{self.base_url}/chat/completions",
            timeout_seconds=self.timeout_seconds,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-Prompt-Version": prompt_version,
            },
            json_body={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": (
                    work_order_analysis_response_format()
                    if prompt_version == WORK_ORDER_ANALYSIS_PROMPT_VERSION
                    else {"type": "json_object"}
                ),
                "temperature": 0,
                **({"reasoning_effort": "none"} if self._disable_thinking else {}),
                **({"max_tokens": self._max_output_tokens} if self._max_output_tokens is not None else {}),
            },
            client=self._client,
            provider="llm",
            operation="structured_completion",
        )
        try:
            if payload['choices'][0].get('finish_reason') not in (None, 'stop'):
                raise ProviderInvalidOutputError('LLM response was not complete')
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderInvalidOutputError("LLM returned invalid JSON") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderInvalidOutputError("LLM returned an empty response")
        return StructuredCompletion(
            parse_json_object(content), token_usage=response_token_usage(payload)
        )
