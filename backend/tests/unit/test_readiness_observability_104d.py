"""PA-104-D: readiness must fail on the business path and backlog must be visible.

Before the fix a stopped broker still answered HTTP 200 (`status=degraded`), the
worker and publisher shipped `healthcheck: disable: true`, and no endpoint or log
exposed queue depth. These tests pin the corrected contract.
"""
import os
import time

import pytest

from app.core import readiness
from app.core.config import Settings
from app.core.readiness import check_readiness


def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="staging",
        database_url="mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
    )


def ok(_settings) -> None:
    return None


def boom(_settings):
    raise OSError("dependency down")


@pytest.mark.parametrize("dependency", ["rabbitmq", "redis"])
def test_broker_or_redis_down_makes_readiness_non_200(dependency):
    result, status = check_readiness(
        settings(), {"mysql": (True, ok), dependency: (True, boom)}, include_queue=False,
    )
    assert status == 503
    assert result["status"] == "not_ready"
    assert result["dependencies"][dependency]["status"] == "down"


def test_non_business_dependency_still_only_degrades():
    result, status = check_readiness(
        settings(), {"mysql": (True, ok), "milvus": (False, boom)}, include_queue=False,
    )
    assert status == 200
    assert result["status"] == "degraded"


def test_default_probes_classify_queue_dependencies_as_critical():
    import inspect

    source = inspect.getsource(check_readiness)
    assert '"redis": (True, probe_redis)' in source
    assert '"rabbitmq": (True, probe_rabbitmq)' in source


def test_queue_depth_is_reported_even_when_unknown(monkeypatch):
    monkeypatch.setattr(readiness, "queue_depth", lambda _settings: 7)
    result, _status = check_readiness(settings(), {"mysql": (True, ok)})
    assert result["queue"] == {"name": settings().resolved_celery_queue, "depth": 7}

    monkeypatch.setattr(readiness, "queue_depth", lambda _settings: None)
    result, _status = check_readiness(settings(), {"mysql": (True, ok)})
    assert result["queue"]["depth"] is None


def test_publisher_probe_requires_a_fresh_heartbeat(tmp_path, monkeypatch):
    from app.worker import healthcheck

    path = tmp_path / "publisher-heartbeat"
    monkeypatch.setenv(healthcheck.PUBLISHER_HEARTBEAT_ENV, str(path))
    monkeypatch.setattr(readiness, "probe_rabbitmq", lambda _settings: None)

    healthy, detail = healthcheck._probe_publisher(settings())
    assert healthy is False and "missing" in detail

    healthcheck.touch_publisher_heartbeat()
    healthy, detail = healthcheck._probe_publisher(settings())
    assert healthy is True and "broker=up" in detail

    stale = time.time() - (healthcheck.PUBLISHER_STALE_SECONDS + 5)
    os.utime(path, (stale, stale))
    healthy, detail = healthcheck._probe_publisher(settings())
    assert healthy is False and "stale" in detail


def test_publisher_probe_fails_when_broker_is_unreachable(tmp_path, monkeypatch):
    from app.worker import healthcheck

    monkeypatch.setenv(healthcheck.PUBLISHER_HEARTBEAT_ENV, str(tmp_path / "hb"))
    healthcheck.touch_publisher_heartbeat()

    def _down(_settings):
        raise OSError("broker unreachable")

    monkeypatch.setattr(readiness, "probe_rabbitmq", _down)
    monkeypatch.setattr(healthcheck, "get_settings", settings)
    assert healthcheck.main(["publisher"]) == 1


@pytest.mark.parametrize("replies,expected", [({}, False), ({"celery@w": {"ok": "pong"}}, True)])
def test_worker_probe_requires_a_live_consumer(monkeypatch, replies, expected):
    from app.worker import healthcheck

    class _Inspect:
        def ping(self):
            return replies

    class _Control:
        def inspect(self, timeout=None):
            return _Inspect()

    monkeypatch.setattr(healthcheck.celery_app, "control", _Control())
    healthy, _detail = healthcheck._probe_worker(settings())
    assert healthy is expected


def test_healthcheck_cli_exits_nonzero_when_unhealthy(tmp_path, monkeypatch, capsys):
    from app.worker import healthcheck

    monkeypatch.setenv(healthcheck.PUBLISHER_HEARTBEAT_ENV, str(tmp_path / "absent"))
    monkeypatch.setattr(healthcheck, "get_settings", settings)
    assert healthcheck.main(["publisher"]) == 1
    assert "unhealthy role=publisher" in capsys.readouterr().out


def test_publisher_loop_touches_the_heartbeat(monkeypatch):
    from app.worker import task_intent_publisher as publisher

    from unittest.mock import MagicMock

    engine = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(publisher, "create_database_engine", lambda _url: engine)
    monkeypatch.setattr(publisher, "run_publisher", run)
    from app.worker import healthcheck

    publisher.serve(
        seconds=30,
        settings=Settings(
            _env_file=None, app_env="staging", durable_tasks_enabled=True,
            database_url="mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        ),
    )
    assert run.call_args.kwargs["heartbeat"] is healthcheck.touch_publisher_heartbeat
