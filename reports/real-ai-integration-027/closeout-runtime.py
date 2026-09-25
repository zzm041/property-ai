"""Launch the fixed fe90913 AUTH001 API/Worker without enabling R3 AUTO.

This runner is intentionally limited to the existing isolated database, vhost,
queue and R2 provider endpoint.  It reads the existing private internal token
without printing or copying it into public evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import site
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tmp/production-v1-closeout/source-fe90913"
PRIVATE = ROOT / "tmp/production-v1-closeout"
R2_PRIVATE = ROOT / "tmp/real-ai-integration-027/runtime"
COLLECTION = "knowledge_chunks_auth001_bgem3_1024"
EXPECTED_SOURCE_HASH = "dadb2ec0b37145609e09ba27750c14ea94021adda52de2cda4f43792c138b465"

site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))
sys.path[:0] = [str(SOURCE / "backend"), str(SOURCE / "scripts/ops")]

import auth001_isolated_runtime as isolated_runtime

# Keep the frozen helper code while binding only its local/private path
# constants to the actual repository.  No credential value is read here.
isolated_runtime.REPO = ROOT
isolated_runtime.BACKEND = ROOT / "backend"
isolated_runtime.DEFAULT_BROKER_PASSWORD_FILE = (
    ROOT / "tmp/auth001-runtime/broker-password.txt"
)
isolated_runtime.ISOLATED_STORAGE_DIR = ROOT / "tmp/auth001-objects"
apply_isolated_environ = isolated_runtime.apply_isolated_environ
build_isolated_settings = isolated_runtime.build_isolated_settings
from app.core.database import assert_isolated_auth_database, create_database_engine
from sqlalchemy import text


def source_hash() -> str:
    rows = []
    for path in sorted((SOURCE / "backend/app").rglob("*.py")):
        rows.append(
            (
                str(path.relative_to(SOURCE)).replace("\\", "/"),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def settings():
    private = json.loads((R2_PRIVATE / "context.json").read_text(encoding="utf-8"))
    internal_key = private["internal_key"]
    base = build_isolated_settings(enable_enqueue=True)
    values = base.model_dump()
    values.update(
        durable_tasks_enabled=True,
        llm_provider="production",
        llm_base_url="http://127.0.0.1:11436/v1",
        llm_model="qwen3.5:4b",
        llm_api_key=internal_key,
        llm_timeout_seconds=60,
        llm_disable_thinking=True,
        llm_max_output_tokens=512,
        asr_provider="production",
        asr_base_url="http://127.0.0.1:11436/v1",
        asr_model="Qwen3-ASR-0.6B",
        asr_api_key=internal_key,
        asr_timeout_seconds=120,
        embedding_provider="production",
        embedding_endpoint="http://127.0.0.1:11436/v1/embeddings",
        embedding_model="bge-m3",
        embedding_api_key=internal_key,
        embedding_dimensions=1024,
        embedding_timeout_seconds=60,
        embedding_revision="5617a9f61b028005a4858fdac845db406aefb181",
        embedding_normalization="l2",
        reranker_provider="production",
        reranker_endpoint="http://127.0.0.1:11436/v1/rerank",
        reranker_model="bge-reranker-v2-m3",
        reranker_api_key=internal_key,
        reranker_timeout_seconds=30,
        milvus_collection=COLLECTION,
        milvus_metric_type="COSINE",
    )
    return type(base)(_env_file=None, **values)


def configure():
    actual_source_hash = source_hash()
    if actual_source_hash != EXPECTED_SOURCE_HASH:
        raise RuntimeError(
            "fixed closeout source changed; refuse to start an unreviewed candidate"
        )
    candidate = settings()
    assert_isolated_auth_database(
        candidate, database_url=candidate.active_database_url
    )
    assert candidate.celery_broker_url.endswith("/property_ai_auth001")
    assert candidate.resolved_celery_queue == "property_ai.auth001"
    assert candidate.milvus_collection == COLLECTION
    engine = create_database_engine(candidate.active_database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalar_one()
                == "20260925_0012_task_intents"
            )
    finally:
        engine.dispose()
    apply_isolated_environ(candidate)
    for name in (
        "durable_tasks_enabled", "llm_provider", "llm_base_url", "llm_model",
        "llm_api_key", "llm_timeout_seconds", "llm_disable_thinking",
        "llm_max_output_tokens", "asr_provider", "asr_base_url", "asr_model",
        "asr_api_key", "asr_timeout_seconds", "embedding_provider",
        "embedding_endpoint", "embedding_model", "embedding_api_key",
        "embedding_dimensions", "embedding_timeout_seconds", "embedding_revision",
        "embedding_normalization", "reranker_provider", "reranker_endpoint",
        "reranker_model", "reranker_api_key", "reranker_timeout_seconds",
        "milvus_metric_type",
    ):
        value = getattr(candidate, name)
        os.environ[name.upper()] = str(
            value.get_secret_value() if hasattr(value, "get_secret_value") else value
        )
    return candidate


def serve(component: str) -> None:
    candidate = configure()
    print(
        json.dumps(
            {
                "component": component,
                "pid": os.getpid(),
                "source_commit": "fe90913a6f3918bca33b2f100137a786a67c4f86",
                "source_hash": EXPECTED_SOURCE_HASH,
                "database": "AUTH001@0012",
                "vhost": "property_ai_auth001",
                "queue": "property_ai.auth001",
                "provider_boundary": "R2 endpoint only; work-order inference remains blocked",
            }
        ),
        flush=True,
    )
    if component == "api":
        import uvicorn
        from app.main import create_app

        stop_file = PRIVATE / "stop-api"
        if stop_file.exists():
            raise RuntimeError("closeout API stop flag already exists")
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(candidate),
                host="127.0.0.1",
                port=8083,
                log_level="warning",
                access_log=False,
            )
        )

        def stop() -> None:
            while not stop_file.exists():
                time.sleep(0.5)
            server.should_exit = True

        threading.Thread(target=stop, daemon=True).start()
        server.run()
        return

    from app.worker.task_intents import celery_app

    celery_app.worker_main(
        [
            "worker", "-Q", "property_ai.auth001", "--pool=solo",
            "--loglevel=warning", "--hostname=auth001@%h",
            "--without-mingle", "--without-gossip",
        ]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["api", "worker"])
    serve(parser.parse_args().component)
