"""Give SUPERVISOR the announcement publish permission.

Revision ID: 20261112_0020_supervisor_announcement_publish
Revises: 20261111_0019_ai_call_log_embedding_rerank
Create Date: 2026-11-12

F-4 / t210: `announcement:publish` only lived on PROPERTY_ADMIN and above, so a
property run by supervisors could draft and submit announcements but never
publish one — every announcement stayed in PENDING_REVIEW forever, and the
"review and publish" button was never rendered for the only staff who actually
looks at the queue.

A supervisor is the person who reviews the draft in practice (see
docs/02-ROLES-PERMISSIONS.md §4.5), so the permission moves onto SUPERVISOR.
The statement is idempotent: a database that already carries the grant (for
example one seeded through app.domain.dev) is left untouched, and a database
without the permission row gets it created first.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261112_0020_supervisor_announcement_publish"
down_revision = "20261111_0019_ai_call_log_embedding_rerank"
branch_labels = None
depends_on = None

PERMISSION = "announcement:publish"
PERMISSION_NAME = "Publish announcements"
ROLE = "SUPERVISOR"

ENSURE_PERMISSION = """
INSERT INTO permissions (code, name, created_at, updated_at)
SELECT :code, :name, CURRENT_TIMESTAMP(6), CURRENT_TIMESTAMP(6)
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)
"""

ENSURE_GRANT = """
INSERT INTO role_permissions (role_id, permission_id, created_at)
SELECT r.id, p.id, CURRENT_TIMESTAMP(6)
FROM roles r JOIN permissions p ON p.code = :code
WHERE r.code = :role
  AND NOT EXISTS (
      SELECT 1 FROM role_permissions rp
      WHERE rp.role_id = r.id AND rp.permission_id = p.id
  )
"""

DELETE_GRANT = """
DELETE rp FROM role_permissions rp
JOIN roles r ON r.id = rp.role_id
JOIN permissions p ON p.id = rp.permission_id
WHERE r.code = :role AND p.code = :code
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(ENSURE_PERMISSION), {"code": PERMISSION, "name": PERMISSION_NAME})
    bind.execute(sa.text(ENSURE_GRANT), {"code": PERMISSION, "role": ROLE})


def downgrade() -> None:
    # Only the role grant goes back; the permission row itself is shared with
    # PROPERTY_ADMIN/TENANT_ADMIN and predates this revision.
    bind = op.get_bind()
    bind.execute(sa.text(DELETE_GRANT), {"code": PERMISSION, "role": ROLE})
