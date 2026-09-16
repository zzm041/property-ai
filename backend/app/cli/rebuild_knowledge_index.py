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
        service = KnowledgeService(session, settings=settings)
        for document in documents:
            service.ingest_version(
                document_id=document.id,
                version=int(document.current_version),
                request_id=f"rebuild-{document.id}",
                rebuild=True,
            )
        print(f"Rebuilt {len(documents)} active knowledge documents")
        return 0
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
