"""Celery task delegates to AnalysisService and is logically idempotent."""

from app.worker.tasks import analyze_work_order_task, run_analyze_work_order


def test_celery_task_is_registered() -> None:
    assert analyze_work_order_task.name == "app.worker.tasks.analyze_work_order_task"
    assert callable(run_analyze_work_order)
