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
    llm_provider: Literal["development", "production"] = "development"
    llm_base_url: str = ""
    llm_model: str = "qwen-plus"
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60.0)
    vlm_provider: Literal["development", "production"] = "development"
    vlm_base_url: str = ""
    vlm_model: str = "qwen-vl-plus"
    vlm_api_key: SecretStr = SecretStr("")
    vlm_timeout_seconds: float = Field(default=12.0, ge=0.1, le=60.0)
    asr_provider: Literal["development", "production"] = "development"
    asr_base_url: str = ""
    asr_model: str = "paraformer-realtime-v2"
    asr_api_key: SecretStr = SecretStr("")
    asr_timeout_seconds: float = Field(default=20.0, ge=0.1, le=120.0)
    embedding_provider: Literal["development", "production"] = "development"
    embedding_provider_behavior: Literal["success", "timeout", "unavailable"] = (
        "success"
    )
    embedding_endpoint: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = Field(default=8, ge=2, le=1024)
    embedding_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60.0)
    reranker_provider: Literal["development", "production"] = "development"
    reranker_provider_behavior: Literal["success", "unavailable"] = "success"
    reranker_endpoint: str = ""
    reranker_api_key: SecretStr = SecretStr("")
    reranker_model: str = "bge-reranker"
    reranker_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    wechat_login_enabled: bool = True
    wechat_provider: Literal["development", "production"] = "development"
    wechat_app_id: str = "wx-dev-app"
    wechat_app_secret: SecretStr = SecretStr("")
    wechat_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    notification_provider: Literal["development", "production"] = "development"
    notification_provider_behavior: Literal["success", "unavailable"] = "success"
    storage_provider: Literal["development", "production"] = "development"
    storage_endpoint: str = ""
    storage_bucket: str = ""
    storage_access_key: SecretStr = SecretStr("")
    storage_secret_key: SecretStr = SecretStr("")
    storage_region: str = "us-east-1"
    storage_presign_ttl_seconds: int = Field(default=300, ge=1, le=600)

    @property
    def should_enqueue_ai_on_create(self) -> bool:
        if self.app_env == "test":
            return False
        return self.ai_enqueue_on_create or self.app_env in {
            "development",
            "staging",
            "production",
        }

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
        if self.app_env == "production":
            self._require_production_provider_credentials()
        return self

    def _require_production_provider_credentials(self) -> None:
        if self.llm_provider == "production":
            if not self.llm_base_url or not self.llm_api_key.get_secret_value():
                raise ValueError("Production LLM requires LLM_BASE_URL and LLM_API_KEY")
        if self.vlm_provider == "production":
            if not self.vlm_base_url or not self.vlm_api_key.get_secret_value():
                raise ValueError("Production VLM requires VLM_BASE_URL and VLM_API_KEY")
        if self.asr_provider == "production":
            if not self.asr_base_url or not self.asr_api_key.get_secret_value():
                raise ValueError("Production ASR requires ASR_BASE_URL and ASR_API_KEY")
        if self.embedding_provider == "production" and not self.embedding_endpoint:
            raise ValueError("Production embedding requires EMBEDDING_ENDPOINT")
        if self.reranker_provider == "production" and not self.reranker_endpoint:
            raise ValueError("Production reranker requires RERANKER_ENDPOINT")
        if self.storage_provider == "production":
            if (
                not self.storage_endpoint
                or not self.storage_bucket
                or not self.storage_access_key.get_secret_value()
                or not self.storage_secret_key.get_secret_value()
            ):
                raise ValueError(
                    "Production storage requires STORAGE_ENDPOINT, STORAGE_BUCKET, "
                    "STORAGE_ACCESS_KEY, and STORAGE_SECRET_KEY"
                )
        if self.wechat_provider == "production" and self.wechat_login_enabled:
            if not self.wechat_app_id or self.wechat_app_id == "wx-dev-app":
                raise ValueError("Production WeChat provider requires WECHAT_APP_ID")
            if not self.wechat_app_secret.get_secret_value():
                raise ValueError("Production WeChat provider requires WECHAT_APP_SECRET")

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
