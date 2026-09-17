"""Day 3 failure classification: MySQL is critical; AI/RAG are degraded only."""

from app.core.config import Settings
from app.core.readiness import check_readiness


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test", wechat_login_enabled=False)


def _boom(_settings: Settings) -> None:
    raise RuntimeError("dependency down")


def _ok(_settings: Settings) -> None:
    return None


def test_mysql_down_is_not_ready_503() -> None:
    result, status = check_readiness(
        _settings(),
        probes={
            "mysql": (True, _boom),
            "llm": (False, _ok),
            "milvus": (False, _ok),
        },
    )
    assert status == 503
    assert result["status"] == "not_ready"
    assert result["dependencies"]["mysql"]["status"] == "down"
    assert result["dependencies"]["mysql"]["critical"] is True


def test_ai_rag_storage_worker_deps_down_stay_degraded() -> None:
    result, status = check_readiness(
        _settings(),
        probes={
            "mysql": (True, _ok),
            "redis": (False, _boom),
            "rabbitmq": (False, _boom),
            "llm": (False, _boom),
            "embedding": (False, _boom),
            "storage": (False, _boom),
            "milvus": (False, _boom),
        },
    )
    assert status == 200
    assert result["status"] == "degraded"
    assert result["dependencies"]["mysql"]["status"] == "up"
    for name in ("redis", "rabbitmq", "llm", "embedding", "storage", "milvus"):
        assert result["dependencies"][name]["status"] == "down"
        assert result["dependencies"][name]["critical"] is False
