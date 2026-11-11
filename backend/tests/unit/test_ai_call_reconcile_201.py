"""PA-201 (t201): request_id is a correlation key, not a unique key.

One request legitimately writes several ``ai_call_logs`` rows (a RAG answer
writes embed + rerank + generate), so the reconciliation has to count requests
by ``distinct request_id`` and calls by row id. These tests pin both halves of
that decision: the schema must not grow a unique constraint on ``request_id``,
and the tool must report the fan-out instead of collapsing it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = REPO_ROOT / "scripts" / "ops" / "ai_call_reconcile.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("ai_call_reconcile", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolves field types through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rows():
    return [
        {"id": 1, "request_id": "rid-a", "provider_type": "EMBEDDING", "operation": "embed",
         "status": "SUCCESS", "latency_ms": 8561, "input_hash": "h1"},
        {"id": 2, "request_id": "rid-a", "provider_type": "RERANK", "operation": "rerank",
         "status": "SUCCESS", "latency_ms": 9055, "input_hash": "h2"},
        {"id": 3, "request_id": "rid-a", "provider_type": "LLM", "operation": "structured_completion",
         "status": "SUCCESS", "latency_ms": 5898, "input_hash": "h3"},
        {"id": 4, "request_id": "rid-b", "provider_type": "ASR", "operation": "transcribe",
         "status": "TIMEOUT", "latency_ms": 12000, "input_hash": "h4"},
        {"id": 5, "request_id": "rid-b", "provider_type": "ASR", "operation": "transcribe",
         "status": "SUCCESS", "latency_ms": 14631, "input_hash": "h4"},
        {"id": 6, "request_id": "rid-c", "provider_type": "VLM", "operation": "describe",
         "status": "SUCCESS", "latency_ms": 1748, "input_hash": "h6"},
        {"id": 7, "request_id": "rid-d", "provider_type": "LLM", "operation": "structured_completion",
         "status": "SUCCESS", "latency_ms": 2667, "input_hash": "h7"},
        {"id": 8, "request_id": "rid-d", "provider_type": "LLM", "operation": "structured_completion",
         "status": "SUCCESS", "latency_ms": 2667, "input_hash": "h7"},
    ]


def test_one_request_counts_once_however_many_calls_it_made():
    tool = load_tool()

    summary = tool.summarize(rows())

    assert summary.calls == 8
    assert summary.requests == 4
    assert summary.rows_per_request == 2.0
    assert summary.fan_out == {1: 1, 2: 2, 3: 1}


def test_a_retry_is_reported_instead_of_being_counted_as_a_new_request():
    tool = load_tool()

    summary = tool.summarize(rows())

    # rid-b ran ASR twice (timeout then success) and rid-d wrote the same call
    # twice: three excess calls over two requests, never a third request.
    assert summary.retried_calls == 2
    assert summary.retried_requests == ["rid-b", "rid-d"]


def test_repeated_write_is_told_apart_from_a_real_retry():
    tool = load_tool()

    summary = tool.summarize(rows())

    # Only rid-d repeats a row *identically* (same status, latency and hash).
    assert summary.repeated_write_rows == 2
    assert summary.repeated_write_requests == 1


def test_breakdown_returns_every_call_of_the_request_in_write_order():
    tool = load_tool()

    calls = tool.request_breakdown(list(reversed(rows())), "rid-a")

    assert [row["id"] for row in calls] == [1, 2, 3]
    assert {row["provider_type"] for row in calls} == {"EMBEDDING", "RERANK", "LLM"}


def test_dedupe_key_is_the_finest_provider_call_identity():
    tool = load_tool()

    assert tool.dedupe_key(rows()[0]) == ("rid-a", "EMBEDDING", "embed")
    # Two calls of one request differ by this key …
    assert tool.dedupe_key(rows()[0]) != tool.dedupe_key(rows()[2])
    # … while a genuine retry keeps it and must stay visible.
    assert tool.dedupe_key(rows()[3]) == tool.dedupe_key(rows()[4])


def test_schema_keeps_request_id_non_unique():
    from app.model import ai as ai_model

    table = ai_model.AiCallLog.__table__

    indexes = [index for index in table.indexes if index.name == "ix_ai_call_logs_request_id"]
    assert len(indexes) == 1
    assert indexes[0].unique is False
    assert [column.name for column in indexes[0].columns] == ["request_id"]
    for constraint in table.constraints:
        columns = [column.name for column in getattr(constraint, "columns", ())]
        assert not (len(columns) == 1 and columns[0] == "request_id"), constraint
