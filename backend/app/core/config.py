"""Environment-backed application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DEFAULT_APP_SECRET_KEY = "change-me-to-a-long-random-string"


class Settings(BaseSettings):
    """Validated settings shared by the API and infrastructure probes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Property AI"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_debug: bool = False
    app_secret_key: SecretStr = SecretStr(DEFAULT_APP_SECRET_KEY)
    app_tz: str = "Asia/Shanghai"
    dev_auth_enabled: bool = False
    jwt_issuer: str = "property-ai"
    jwt_audience: str = "property-ai-api"
    jwt_access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    dev_seed_lock_timeout_seconds: int = Field(default=5, ge=0, le=30)

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_v1_prefix: str = "/api/v1"

    database_url: str = (
        "mysql+pymysql://property:change-me@127.0.0.1:3306/"
        "property_ai?charset=utf8mb4"
    )
    test_database_url: str = (
        "mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/"
        "property_ai_test?charset=utf8mb4"
    )
    allow_destructive_test_database: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = (
        "amqp://property:change-me@127.0.0.1:5672/property_ai"
    )

    readiness_timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)
    upload_max_mb: int = Field(default=10, ge=1, le=50)
    llm_provider_behavior: Literal[
        "success", "timeout", "invalid", "low_confidence", "unavailable"
    ] = "success"
    vlm_provider_behavior: Literal["success", "timeout", "failure", "unavailable"] = (
        "success"
    )
    asr_provider_behavior: Literal["success", "timeout", "failure", "unavailable"] = (
        "success"
    )
    ai_confidence_threshold: float = Field(default=0.70, ge=0, le=1)
    ai_llm_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60.0)
    ai_vlm_timeout_seconds: float = Field(default=12.0, ge=0.1, le=60.0)
    ai_asr_timeout_seconds: float = Field(default=20.0, ge=0.1, le=120.0)
    ai_max_attempts: int = Field(default=2, ge=1, le=5)
    ai_enqueue_on_create: bool = False
    ai_auto_assign: bool = False
    incident_window_minutes: int = Field(default=10, ge=1, le=1440)
    incident_threshold: int = Field(default=5, ge=2, le=100)
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "knowledge_chunks_v1"
    milvus_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30.0)
    embedding_provider: Literal["development", "production"] = "development"
    embedding_provider_behavior: Literal["success", "timeout", "unavailable"] = (
        "success"
    )
    embedding_endpoint: str = ""
    embedding_dimensions: int = Field(default=8, ge=2, le=1024)
    reranker_provider: Literal["development", "production"] = "development"
    reranker_provider_behavior: Literal["success", "unavailable"] = "success"
    reranker_endpoint: str = ""
    wechat_login_enabled: bool = True
    wechat_provider: Literal["development", "production"] = "development"
    wechat_app_id: str = "wx-dev-app"
    wechat_app_secret: SecretStr = SecretStr("")
    notification_provider: Literal["development", "production"] = "development"
    notification_provider_behavior: Literal["success", "unavailable"] = "success"
    storage_provider: Literal["development", "production"] = "development"
    storage_endpoint: str = ""
    storage_bucket: str = ""

    @property
    def should_enqueue_ai_on_create(self) -> bool:
        if self.app_env == "test":
            return False
        return self.ai_enqueue_on_create or self.app_env == "development"

    @model_validator(mode="after")
    def validate_database_isolation(self) -> "Settings":
        development = make_url(self.database_url)
        test = make_url(self.test_database_url)

        for label, url in (("DATABASE_URL", development), ("TEST_DATABASE_URL", test)):
            if url.drivername != "mysql+pymysql":
                raise ValueError(f"{label} must use mysql+pymysql")
            if not url.database:
                raise ValueError(f"{label} must include a database name")

        development_target = (
            development.host,
            development.port or 3306,
            development.database,
        )
        test_target = (test.host, test.port or 3306, test.database)
        if development_target == test_target:
            raise ValueError(
                "TEST_DATABASE_URL must target a different MySQL database or instance"
            )
        return self

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if self.dev_auth_enabled and self.app_env not in {"development", "test"}:
            raise ValueError(
                "Development Auth is allowed only in development or test"
            )
        if (
            self.app_env == "production"
            and self.app_secret_key.get_secret_value() == DEFAULT_APP_SECRET_KEY
        ):
            raise ValueError("Production requires a non-default APP_SECRET_KEY")
        if self.app_env == "production" and self.wechat_login_enabled:
            if not self.wechat_app_id or self.wechat_app_id == "wx-dev-app":
                raise ValueError("Production WeChat login requires WECHAT_APP_ID")
            if not self.wechat_app_secret.get_secret_value():
                raise ValueError("Production WeChat login requires WECHAT_APP_SECRET")
        return self

    @property
    def active_database_url(self) -> str:
        """Use the disposable database automatically in the test environment."""

        if self.app_env == "test":
            return self.test_database_url
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""

    return Settings()
