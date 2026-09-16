"""Readiness classifier tests independent of live services."""

from app.core.config import Settings
from app.core.readiness import check_readiness


def _up(_: Settings) -> None:
    return None


def _down(_: Settings) -> None:
    raise ConnectionError("credential-bearing dependency error")


def test_noncritical_failure_is_degraded() -> None:
    result, status_code = check_readiness(
        Settings(_env_file=None),
        probes={
            "mysql": (True, _up),
            "redis": (False, _down),
            "rabbitmq": (False, _up),
        },
    )

    assert status_code == 200
    assert result["status"] == "degraded"
    assert "credential-bearing" not in str(result)


def test_llm_down_is_degraded_not_critical() -> None:
    result, status_code = check_readiness(
        Settings(_env_file=None),
        probes={
            "mysql": (True, _up),
            "redis": (False, _up),
            "rabbitmq": (False, _up),
            "llm": (False, _down),
            "vlm": (False, _down),
            "asr": (False, _down),
        },
    )

    assert status_code == 200
    assert result["status"] == "degraded"
    assert result["dependencies"]["llm"]["critical"] is False


def test_milvus_and_embedding_down_are_degraded() -> None:
    result, status_code = check_readiness(
        Settings(_env_file=None),
        probes={
            "mysql": (True, _up),
            "redis": (False, _up),
            "rabbitmq": (False, _up),
            "embedding": (False, _down),
            "milvus": (False, _down),
        },
    )
    assert status_code == 200
    assert result["status"] == "degraded"
    assert result["dependencies"]["milvus"]["critical"] is False


def test_critical_failure_is_not_ready() -> None:
    result, status_code = check_readiness(
        Settings(_env_file=None),
        probes={
            "mysql": (True, _down),
            "redis": (False, _up),
            "rabbitmq": (False, _up),
        },
    )

    assert status_code == 503
    assert result["status"] == "not_ready"
