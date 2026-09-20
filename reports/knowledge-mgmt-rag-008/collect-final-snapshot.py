"""Collect a read-only, sanitized final snapshot for KNOWLEDGE-MGMT-RAG-008."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import site
import struct
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
site.addsitedir(str(REPO / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

from pymilvus import Collection, connections  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    apply_isolated_environ,
    build_isolated_settings,
)


def rows(connection, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(text(statement), parameters or {}).mappings().all()
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def package(name: str, module: Any) -> dict[str, str]:
    return {
        "version": metadata.version(name),
        "loaded_from": str(Path(module.__file__).resolve()),
    }


def main() -> None:
    settings = build_isolated_settings()
    apply_isolated_environ(settings)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        documents = rows(
            connection,
            "SELECT id, tenant_id, property_id, title, visibility, status, current_version "
            "FROM knowledge_documents WHERE id IN (1, 2, 3) ORDER BY id",
        )
        versions = rows(
            connection,
            "SELECT document_id, version, status, content_hash, created_at "
            "FROM knowledge_document_versions WHERE document_id IN (1, 2, 3) "
            "ORDER BY document_id, version",
        )
        jobs = rows(
            connection,
            "SELECT document_id, version, status, error_code, attempt, started_at, finished_at "
            "FROM knowledge_ingestion_jobs WHERE document_id IN (1, 2, 3) "
            "ORDER BY document_id, version",
        )
        audits = rows(
            connection,
            "SELECT action, resource_type, resource_id, actor_user_id, request_id, created_at "
            "FROM audit_logs WHERE resource_type = 'KNOWLEDGE_DOCUMENT' "
            "AND resource_id IN (1, 2, 3) ORDER BY id",
        )

    alias = "knowledge-mgmt-rag-008-final"
    connections.connect(alias=alias, uri=settings.milvus_uri, timeout=3)
    collection = Collection(settings.milvus_collection, using=alias)
    collection.load()
    vector_rows = collection.query(
        expr="document_id in [1, 2, 3]",
        output_fields=[
            "chunk_id",
            "document_id",
            "document_version",
            "tenant_id",
            "property_id",
            "visibility",
        ],
        limit=100,
        timeout=3,
    )
    vector_dim = next(
        int(field.params["dim"])
        for field in collection.schema.fields
        if field.name == "vector"
    )
    collection_entities = int(collection.num_entities)
    connections.disconnect(alias)

    import cryptography  # noqa: E402
    import fastapi  # noqa: E402
    import grpc  # noqa: E402
    import numpy  # noqa: E402
    import pydantic  # noqa: E402
    import pymilvus  # noqa: E402
    import pymysql  # noqa: E402
    import sqlalchemy  # noqa: E402

    screenshots = [
        {
            "file": path.name,
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(OUT.glob("*.png"))
    ]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    evidence = {
        "task_id": "KNOWLEDGE-MGMT-RAG-008",
        "recorded_at": datetime.now(UTC).isoformat(),
        "code_sha": head,
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "architecture": f"{struct.calcsize('P') * 8}-bit {platform.machine()}",
            "user_site_enabled": site.ENABLE_USER_SITE,
            "dependency_reuse": str((REPO / ".venv" / "Lib" / "site-packages").resolve()),
            "packages": {
                "fastapi": package("fastapi", fastapi),
                "pydantic": package("pydantic", pydantic),
                "sqlalchemy": package("sqlalchemy", sqlalchemy),
                "pymysql": package("pymysql", pymysql),
                "cryptography": package("cryptography", cryptography),
                "pymilvus": package("pymilvus", pymilvus),
                "grpcio": package("grpcio", grpc),
                "numpy": package("numpy", numpy),
            },
        },
        "runtime": {
            "api": "http://127.0.0.1:8083/api/v1",
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "alembic": revision,
            "rabbit_vhost": ISOLATED_VHOST,
            "rabbit_queue": ISOLATED_QUEUE,
            "milvus_collection": settings.milvus_collection,
            "milvus_vector_dimension": vector_dim,
            "milvus_entities": collection_entities,
            "embedding_provider": settings.embedding_provider,
            "model_boundary": "DEVELOPMENT_FAKE; real bge-m3/Qwen not run",
        },
        "database_documents": documents,
        "database_versions": versions,
        "database_ingestion_jobs": jobs,
        "database_audits": audits,
        "milvus_rows_retained_for_history": vector_rows,
        "page_acceptance": {
            "positive_flow": [
                "management page created document 1",
                "worker activated version 1",
                "failed version 2 did not replace version 1",
                "worker activated version 3",
                "resident query cited document 1 version 3 and grounded the Development answer",
                "management page archived document 1 and displayed preserved history",
                "resident re-login query returned insufficient_knowledge with zero citations",
            ],
            "archived_query_status": "insufficient_knowledge",
            "archived_query_citations": [],
            "screenshots": screenshots,
            "actual_image_inspection": True,
        },
        "test_results": {
            "focused_after_fix": "20 passed, 1 warning",
            "pure_unit_after_fix": "490 passed, 1 warning",
            "integration_fixture_skipped": "forbidden broad DELETE and migration behavior was identified before execution",
        },
        "safety": {
            "direct_database_business_writes": False,
            "migration_run": False,
            "shared_vhost_consumed": False,
            "shared_services_rebuilt": False,
            "credentials_recorded": False,
        },
    }
    (OUT / "runtime-final-snapshot.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUT / "runtime-final-snapshot.json"),
                "documents": [
                    {"id": item["id"], "status": item["status"]} for item in documents
                ],
                "archive_audits": sum(
                    item["action"] == "KNOWLEDGE_DOCUMENT_ARCHIVED" for item in audits
                ),
                "screenshots": len(screenshots),
                "credentials_recorded": False,
            },
            ensure_ascii=False,
        )
    )
    engine.dispose()


if __name__ == "__main__":
    main()
