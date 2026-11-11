"""t188: one shipped RAG prompt version, and the body travels with its label.

t187 found the previous fix had relabelled the text: `RAG_ANSWER_PROMPT_VERSION`
said `rag-answer-v2` while `_prompt()` served one body for three labels (v2/v3/v4),
so the audit row claimed a prompt that never ran. These tests pin the shape that
makes that impossible to repeat.
"""

import importlib.util
from pathlib import Path

import pytest

from app.core.config import Settings
from app.domain.knowledge import (
    RAG_ANSWER_AUTHORIZED_PROMPT_VERSION,
    RAG_ANSWER_PROMPT_BODIES,
    RAG_ANSWER_PROMPT_VERSION,
    RAG_ANSWER_PROMPT_VERSIONS,
    RAG_ANSWER_UNSHIPPED_PROMPT_VERSIONS,
    rag_answer_prompt_body,
)
from app.service.retrieval import EvidenceSnapshot, RetrievalService


ROOT = Path(__file__).resolve().parents[3]


def _chunks():
    return tuple(
        EvidenceSnapshot(
            id=cid,
            document_id=doc,
            tenant_id=1,
            property_id=1,
            visibility="RESIDENT",
            version=3,
            excerpt=text,
            source_locator=locator,
            title="合成规定",
        )
        for cid, doc, text, locator in (
            (97, 5, "登记时段为工作日。", "规定#chunk-1"),
            (4002, 9, "申请前须登记。", "流程#chunk-2"),
        )
    )


def test_exactly_one_version_ships():
    assert RAG_ANSWER_PROMPT_VERSIONS == ("rag-answer-v2",)
    assert tuple(RAG_ANSWER_PROMPT_BODIES) == RAG_ANSWER_PROMPT_VERSIONS
    assert RAG_ANSWER_PROMPT_VERSION == RAG_ANSWER_AUTHORIZED_PROMPT_VERSION


def test_drafts_are_named_but_carry_no_body_and_no_whitelist_entry():
    for draft in RAG_ANSWER_UNSHIPPED_PROMPT_VERSIONS:
        assert draft not in RAG_ANSWER_PROMPT_VERSIONS
        assert draft not in RAG_ANSWER_PROMPT_BODIES
        with pytest.raises(ValueError, match="ships no answer body"):
            rag_answer_prompt_body(draft)


def test_settings_only_accept_the_shipped_version():
    shipped = ("rag-answer-v2",)
    for version in shipped:
        settings = Settings(
            _env_file=None, app_env="test", rag_answer_prompt_version=version
        )
        assert settings.rag_answer_prompt_version == version

    for rejected in ("rag-answer-v3", "rag-answer-v4", "rag-answer-v999", ""):
        with pytest.raises(ValueError, match="the only version this build ships a body for"):
            Settings(_env_file=None, app_env="test", rag_answer_prompt_version=rejected)


def test_prompt_body_is_the_text_that_runs_not_a_lookalike():
    prompt = RetrievalService._prompt(
        "什么时候登记？", _chunks(), prompt_version=RAG_ANSWER_PROMPT_VERSION
    )
    body = RAG_ANSWER_PROMPT_BODIES[RAG_ANSWER_PROMPT_VERSION]

    # The version's body is the prompt prefix, character for character, so a label
    # can never describe text this build does not send.
    assert prompt.startswith(body + "\n\n")
    assert body in prompt
    assert "status, answer, citation_chunk_ids" in body
    assert "Allowed citation_chunk_ids: [97, 4002]" in prompt


def test_audit_hash_is_salted_so_short_questions_are_not_reversible():
    service = object.__new__(RetrievalService)
    service._settings = type("S", (), {"audit_hash_salt": _Secret("pepper")})()

    salted = service._audit_hash("漏水了")
    same = service._audit_hash("漏水了")
    assert salted == same
    assert len(salted) == 64

    bare = object.__new__(RetrievalService)
    bare._settings = type("S", (), {"audit_hash_salt": _Secret("")})()
    # Even an unset salt is not a bare sha256 of the question.
    import hashlib

    assert bare._audit_hash("漏水了") != hashlib.sha256("漏水了".encode()).hexdigest()
    # Different salts produce different hashes: one deployment cannot be joined to
    # another's log by recomputing the dictionary.
    assert bare._audit_hash("漏水了") != salted


def test_the_shipped_label_is_the_one_the_rag_scope_accepts():
    """The deployed RAG adapter is the authority on the label, not this module.

    ``scripts/ops/local_ai_http.py`` inside the frozen rag candidate hardcodes
    ``scope == "rag_only" and prompt_version != "rag-answer-v2"`` →
    ``RAG_ONLY_NO_AUTO_AUTHORIZATION`` and maps only that label to
    ``{"type": "json_object"}``. The repo copy of ``runtime_control.py`` keeps a
    wider default set for the work-order adapter, so this test only pins the
    positive direction: the one label we ship must be one the rag scope accepts.
    """

    spec = importlib.util.spec_from_file_location(
        "rag_adapter_188", ROOT / "backend/app/provider/local_ai/runtime_control.py"
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    assert RAG_ANSWER_PROMPT_VERSIONS == ("rag-answer-v2",)
    assert RAG_ANSWER_PROMPT_VERSION in adapter.RAG_PROMPT_VERSIONS

    import json

    body = json.dumps(
        {
            "model": adapter.MODELS["qwen"],
            "messages": [{"role": "user", "content": "Synthetic evidence question"}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "reasoning_effort": "none",
            "max_tokens": 512,
        }
    ).encode()
    assert adapter.decode(
        "/v1/chat/completions",
        "application/json",
        body,
        RAG_ANSWER_PROMPT_VERSION,
        "rag_only",
    )[0] == "qwen"


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value
