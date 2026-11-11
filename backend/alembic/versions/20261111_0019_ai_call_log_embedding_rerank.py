"""Widen ai_call_logs.provider_type to the knowledge assistant's providers.

Revision ID: 20261111_0019_ai_call_log_embedding_rerank
Revises: 20261111_0018_content_fingerprint_loose
Create Date: 2026-11-11

F2 / t177: the knowledge assistant calls an embedding model and a reranker, and
those calls have to be auditable in the same table as the work-order chain. The
0006 check constraint only allowed LLM/VLM/ASR, so an embedding or rerank row
would be rejected by MySQL. Widen it; the enum in app.domain.ai carries the same
five values.

The downgrade refuses to run while such rows exist instead of deleting audit
history.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261111_0019_ai_call_log_embedding_rerank"
down_revision = "20261111_0018_content_fingerprint_loose"
branch_labels = None
depends_on = None

# The metadata naming convention prefixes "ck_<table>_", so the bare name maps to
# the existing ck_ai_call_logs_provider_type. Passing the prefixed name here would
# ask MySQL for ck_ai_call_logs_ck_ai_call_logs_provider_type and fail.
CONSTRAINT = "provider_type"
BEFORE = "BINARY provider_type IN ('LLM', 'VLM', 'ASR')"
AFTER = "BINARY provider_type IN ('LLM', 'VLM', 'ASR', 'EMBEDDING', 'RERANK')"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "ai_call_logs", type_="check")
    op.create_check_constraint(CONSTRAINT, "ai_call_logs", AFTER)


def downgrade() -> None:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM ai_call_logs"
            " WHERE provider_type IN ('EMBEDDING', 'RERANK')"
        )
    ).scalar_one()
    if rows:
        raise RuntimeError(
            "cannot narrow ai_call_logs.provider_type while "
            f"{rows} EMBEDDING/RERANK audit row(s) exist; export them first"
        )
    op.drop_constraint(CONSTRAINT, "ai_call_logs", type_="check")
    op.create_check_constraint(CONSTRAINT, "ai_call_logs", BEFORE)
