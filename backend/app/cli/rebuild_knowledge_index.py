"""Rebuild Milvus from MySQL knowledge source text. Vector DB is not authoritative."""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.errors import AppError
from app.model.knowledge import KnowledgeDocument
from app.service.knowledge import KnowledgeService


def main(argv: list[str] | None = None) -> int:
    del argv
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        documents = session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.status == "ACTIVE",
                KnowledgeDocument.current_version.is_not(None),
            )
        ).all()
        rows = [
            (int(document.id), int(document.current_version))
            for document in documents
        ]
        if session.in_transaction():
            session.commit()
        service = KnowledgeService(session, settings=settings)
        for document_id, version in rows:
            service.ingest_version(
                document_id=document_id,
                version=version,
                request_id=f"rebuild-{document_id}",
                rebuild=True,
            )
        print(f"Rebuilt {len(rows)} active knowledge documents")
        return 0
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
