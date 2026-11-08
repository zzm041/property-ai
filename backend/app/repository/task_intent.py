"""Intent persistence only; callers own all transactions."""
from sqlalchemy import and_, or_, select
from app.model.task_intent import TaskIntent


class TaskIntentRepository:
    def __init__(self, session):
        self.session = session

    def get(self, intent_id, *, lock=False):
        statement = select(TaskIntent).where(TaskIntent.id == intent_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def for_resource(self, operation, resource_id, version):
        return self.session.scalar(select(TaskIntent).where(
            TaskIntent.operation == operation, TaskIntent.resource_id == resource_id,
            TaskIntent.resource_version == version, TaskIntent.generation == 1))

    def due_ids(self, now, *, intent_ids=None, limit=50):
        statement = select(TaskIntent.id).where(or_(
            and_(TaskIntent.status.in_(("PENDING", "RETRY_WAIT", "PUBLISHED")), TaskIntent.available_at <= now),
            and_(TaskIntent.status.in_(("PUBLISHING", "PROCESSING")), TaskIntent.lease_until <= now),
        )).order_by(TaskIntent.available_at, TaskIntent.id).limit(min(max(limit, 1), 50))
        if intent_ids is not None:
            statement = statement.where(TaskIntent.id.in_(tuple(intent_ids)))
        return tuple(self.session.scalars(statement))

    def add(self, row):
        self.session.add(row)
        self.session.flush()
        return row
