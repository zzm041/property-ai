"""HTTP contracts with an injected runner; never loads a real model."""
import asyncio
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.selfhost.config import RuntimeConfig
from scripts.selfhost.server import create_app
from scripts.selfhost.runner import RuntimeFailure


class Runner:
    faulted = False

    def __init__(self):
        self.calls = []

    async def run(self, kind, value):
        self.calls.append((kind, value))
        return {"received": kind}


@pytest.fixture
def gateway(tmp_path):
    token = tmp_path / "token"
    token.write_text("x" * 48)
    config = RuntimeConfig.from_dict({"model_root": "models", "bge_python": sys.executable,
        "asr_python": sys.executable, "token_file": str(token), "ollama_url": "http://127.0.0.1:11435",
        "ollama_version": "0.34.4", "chat_model": "customer-qwen", "chat_digest": "a" * 64}, tmp_path)
    runner = Runner()
    client = TestClient(create_app(config, runner))
    return client, runner


AUTH = {"Authorization": "Bearer " + "x" * 48}


def chat(**updates):
    return {"model": "customer-qwen", "messages": [{"role": "user", "content": "房间灯不亮"}],
            "temperature": 0, "max_tokens": 1000, **updates}


def test_auth_required_before_body_and_health_does_not_infer(gateway):
    client, runner = gateway
    assert client.post("/v1/chat/completions", content="bad").status_code == 401
    assert client.get("/healthz").status_code == 401
    assert client.get("/healthz", headers=AUTH).json()["inference_verified"] is False
    assert runner.calls == []


@pytest.mark.parametrize("updates", [{"model": "unapproved"}, {"stream": True}, {"max_tokens": 99999},
    {"tools": []}, {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://169.254.169.254/secret"}}]}]}])
def test_chat_rejects_unbounded_or_external_input(gateway, updates):
    client, runner = gateway
    assert client.post("/v1/chat/completions", json=chat(**updates), headers=AUTH).status_code == 422
    assert runner.calls == []


def test_chat_preserves_business_prompt_and_schema(gateway):
    client, runner = gateway
    payload = chat(response_format={"type": "json_schema", "json_schema": {"name": "work_order", "schema": {"type": "object"}}})
    assert client.post("/v1/chat/completions", json=payload, headers=AUTH).status_code == 200
    assert runner.calls[0][0] == "chat"
    assert runner.calls[0][1]["messages"] == payload["messages"]
    assert runner.calls[0][1]["response_format"] == payload["response_format"]
    assert runner.calls[0][1]["reasoning_effort"] == "none"


@pytest.mark.parametrize("path,body", [("/v1/embeddings", {"model": "bge-m3", "input": ["维修公告"]}),
    ("/v1/rerank", {"model": "bge-reranker-v2-m3", "query": "维修", "documents": ["停水通知"]})])
def test_cpu_contracts(gateway, path, body):
    client, runner = gateway
    assert client.post(path, json=body, headers=AUTH).status_code == 200
    assert len(runner.calls) == 1


@pytest.mark.parametrize("path,body", [("/v1/embeddings", {"model": "bge-m3", "input": ["x"] * 17}),
    ("/v1/embeddings", {"model": "other", "input": ["x"]}),
    ("/v1/rerank", {"model": "bge-reranker-v2-m3", "query": "", "documents": ["x"]})])
def test_cpu_limits(gateway, path, body):
    client, runner = gateway
    assert client.post(path, json=body, headers=AUTH).status_code == 422
    assert runner.calls == []


def test_asr_multipart(gateway):
    client, runner = gateway
    assert client.post("/v1/audio/transcriptions", data={"model": "Qwen3-ASR-0.6B"},
        files={"file": ("sample.wav", b"audio fixture", "audio/wav")}, headers=AUTH).status_code == 200
    assert runner.calls == [("asr", b"audio fixture")]


def test_malformed_json_and_body_size_fail_without_runner(gateway):
    client, runner = gateway
    assert client.post("/v1/embeddings", content="{", headers=AUTH).status_code == 422
    assert client.post("/v1/embeddings", content=b"x" * (15 * 1024 * 1024 + 1), headers=AUTH).status_code == 413
    assert runner.calls == []


def test_inference_error_is_not_fabricated_success(gateway):
    client, runner = gateway
    async def fail(*args):
        raise RuntimeFailure("MODEL_TIMEOUT", 504)
    runner.run = fail
    result = client.post("/v1/chat/completions", json=chat(), headers=AUTH)
    assert result.status_code == 504
    assert result.json() == {"error": {"code": "MODEL_TIMEOUT"}}


def test_busy_or_faulted_runtime_rejects_without_retry(gateway):
    client, runner = gateway
    client.app.state.busy = True
    assert client.post("/v1/chat/completions", json=chat(), headers=AUTH).status_code == 503
    client.app.state.busy = False
    runner.faulted = True
    assert client.post("/v1/chat/completions", json=chat(), headers=AUTH).status_code == 503
    assert runner.calls == []
