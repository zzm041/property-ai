"""Scope the content quarantine to a code version and let operators clear it.

Revision ID: 20261112_0021_ai_call_log_quarantine_scope
Revises: 20261112_0020_supervisor_announcement_publish
Create Date: 2026-11-12

F-7 (t214): PA-169 quarantined report *content* for 24 hours whenever its
``content_fingerprint`` had produced an unusable answer. The probe never asked
which code had produced that answer, so a text rejected by a build that has since
been fixed kept short-circuiting to ``AI.INPUT_QUARANTINED`` with a 0 ms provider
skipped call - E2E and demos stayed red while the product was already green.

Two nullable columns carry the fix:

* ``code_version`` - the build that wrote the row (``buildinfo.code_version()``).
  The probe now requires the row's version to equal the running one. Rows written
  before this revision have NULL and therefore match nothing: an unversioned
  refusal is exactly the case that must be re-asked.
* ``quarantine_cleared_at`` - an operator stamp written by
  ``scripts/ops/ai_quarantine.py``. The probe ignores stamped rows, and the rows
  themselves stay in the audit trail (nothing is deleted).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261112_0021_ai_call_log_quarantine_scope"
down_revision = "20261112_0020_supervisor_announcement_publish"
branch_labels = None
depends_on = None

COLUMNS = (
    ("code_version", sa.String(length=64)),
    ("quarantine_cleared_at", sa.DateTime(timezone=False)),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    present = {column["name"] for column in inspector.get_columns("ai_call_logs")}
    for name, type_ in COLUMNS:
        if name in present:
            continue
        op.add_column("ai_call_logs", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    present = {column["name"] for column in inspector.get_columns("ai_call_logs")}
    for name, _ in reversed(COLUMNS):
        if name in present:
            op.drop_column("ai_call_logs", name)
