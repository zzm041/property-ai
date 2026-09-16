# Production operations: backup, restore, rebuild, migration

## MySQL

Backup (logical, UTC):

```bash
mysqldump --single-transaction --routines --triggers \
  -h "$MYSQL_HOST" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" property_ai \
  > backup-mysql-$(date -u +%Y%m%dT%H%M%SZ).sql
```

Restore into an empty database, then:

```bash
cd backend
alembic upgrade head
```

Do not auto-migrate on API startup.

## Alembic

```bash
alembic current
alembic upgrade head
alembic check
```

Production bootstrap is a separate admin procedure. Never run Development Seed in production.

## Milvus

Vector data is derived. MySQL `knowledge_documents` / `knowledge_document_versions.source_text` plus source objects are the authority.

Rebuild collection:

```bash
python -m app.cli.rebuild_knowledge_index
```

If Milvus is lost, knowledge assistant degrades; WorkOrder / Attendance / Announcement remain available.

## Object storage

Development keys are opaque. Production adapter stores objects in the configured bucket. Assume bucket versioning / replica policy is provided by the storage operator. File metadata remains in `file_objects`.

## Secrets

Never bake secrets into images. Inject `APP_SECRET_KEY`, `DATABASE_URL`, `WECHAT_APP_SECRET`, and provider keys at runtime.
