"""Environment-backed application configuration."""

from functools import lru_cache
from ipaddress import ip_address
import re
from typing import Literal
from urllib.parse import urlsplit

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
    jwt_refresh_token_ttl_seconds: int = Field(default=1209600, ge=3600, le=2592000)
    sms_provider: Literal["development", "production"] = "development"
    sms_otp_ttl_seconds: int = Field(default=300, ge=60, le=600)
    sms_otp_max_attempts: int = Field(default=5, ge=1, le=10)
    sms_send_limit_per_hour: int = Field(default=5, ge=1, le=20)
    sms_login_limit_per_hour: int = Field(default=10, ge=1, le=30)
    sms_aliyun_access_key_id: SecretStr = SecretStr("")
    sms_aliyun_access_key_secret: SecretStr = SecretStr("")
    sms_aliyun_sign_name: str = ""
    sms_aliyun_template_code: str = ""
    sms_aliyun_password_reset_template_code: str = ""
    sms_aliyun_phone_change_template_code: str = ""
    sms_live_allowed_phone_e164: SecretStr = SecretStr("")
    sms_aliyun_timeout_seconds: float = Field(default=5.0, ge=1.0, le=15.0)
    dev_seed_lock_timeout_seconds: int = Field(default=5, ge=0, le=30)
    identity_test_proof_enabled: bool = False
    identity_test_proof_secret: SecretStr = SecretStr("")
    identity_test_proof_target: str = ""

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
    celery_task_queue: str = ""
    sla_sweep_interval_seconds: int = Field(default=30, ge=5, le=3600)
    notification_retry_sweep_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=3600,
    )
    db_pool_size: int = Field(default=5, ge=1, le=32)
    db_max_overflow: int = Field(default=10, ge=0, le=32)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60, le=7200)

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
    ai_context_recent_orders_limit: int = Field(default=5, ge=0, le=20)
    ai_context_recent_days: int = Field(default=30, ge=1, le=365)
    ai_enqueue_on_create: bool = False
    # Staged rollout: opt in only after0012, never silently detect/create tables.
    durable_tasks_enabled: bool = False
    ai_auto_assign: bool = False
    dispatch_weight_duty: float = Field(default=0.25, ge=0, le=1)
    dispatch_weight_skill: float = Field(default=0.20, ge=0, le=1)
    dispatch_weight_capacity: float = Field(default=0.20, ge=0, le=1)
    dispatch_weight_same_building: float = Field(default=0.10, ge=0, le=1)
    dispatch_weight_response: float = Field(default=0.10, ge=0, le=1)
    dispatch_weight_completion: float = Field(default=0.08, ge=0, le=1)
    dispatch_weight_rating: float = Field(default=0.07, ge=0, le=1)
    dispatch_response_reference_seconds: float = Field(
        default=900.0,
        ge=1.0,
        le=86400.0,
    )
    dispatch_reject_cooldown_seconds: int = Field(
        default=600,
        ge=0,
        le=86400,
    )
    incident_window_minutes: int = Field(default=10, ge=1, le=1440)
    incident_threshold: int = Field(default=5, ge=2, le=100)
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "knowledge_chunks_v1"
    milvus_metric_type: Literal['IP', 'COSINE'] = 'IP'
    milvus_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30.0)
    llm_provider: Literal["development", "production"] = "development"
    llm_base_url: str = ""
    llm_model: str = "qwen-plus"
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60.0)
    llm_disable_thinking: bool = False
    llm_max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    supervisor_agent_enabled: bool = False
    supervisor_agent_provider: Literal['development', 'production'] = 'development'
    supervisor_agent_base_url: str = ''
    supervisor_agent_model: str = ''
    supervisor_agent_api_key: SecretStr = SecretStr('')
    # Knowledge answers may use a separately approved self-hosted model while
    # work-order analysis remains on its independently gated provider.  The
    # default preserves the historical single-provider configuration.
    rag_llm_provider: Literal["inherit", "development", "production"] = "inherit"
    rag_llm_base_url: str = ""
    rag_llm_model: str = ""
    rag_llm_api_key: SecretStr = SecretStr("")
    rag_llm_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60.0)
    rag_llm_disable_thinking: bool = False
    rag_llm_max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    local_ai_internal_token: SecretStr = SecretStr("")
    vlm_provider: Literal["development", "production"] = "development"
    vlm_base_url: str = ""
    vlm_model: str = "qwen-vl-plus"
    vlm_model_revision: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:+-]*$")
    vlm_api_key: SecretStr = SecretStr("")
    vlm_timeout_seconds: float = Field(default=12.0, ge=0.1, le=60.0)
    vlm_disable_thinking: bool = False
    vlm_max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    asr_provider: Literal["development", "production"] = "development"
    asr_base_url: str = ""
    asr_model: str = "paraformer-realtime-v2"
    asr_model_revision: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:+-]*$")
    asr_api_key: SecretStr = SecretStr("")
    asr_timeout_seconds: float = Field(default=20.0, ge=0.1, le=120.0)
    embedding_provider: Literal["development", "production"] = "development"
    embedding_provider_behavior: Literal["success", "timeout", "unavailable"] = (
        "success"
    )
    embedding_endpoint: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "bge-m3"
    embedding_revision: str = ''
    embedding_normalization: Literal['unspecified', 'l2'] = 'unspecified'
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
    dev_storage_dir: str = ""
    storage_endpoint: str = ""
    storage_bucket: str = ""
    storage_access_key: SecretStr = SecretStr("")
    storage_secret_key: SecretStr = SecretStr("")
    storage_region: str = "us-east-1"
    storage_presign_ttl_seconds: int = Field(default=300, ge=1, le=600)
    file_scanner_provider: Literal["development", "unavailable", "clamav"] = "unavailable"
    file_scanner_host: str = Field(default="", max_length=253)
    file_scanner_port: int = Field(default=3310, ge=1, le=65535)
    file_scanner_timeout_seconds: float = Field(default=10.0, ge=0.1, le=60, allow_inf_nan=False)

    @property
    def should_enqueue_ai_on_create(self) -> bool:
        if self.app_env == "test":
            return self.ai_enqueue_on_create
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
        if self.sms_provider == "production":
            required = (
                self.sms_aliyun_access_key_id.get_secret_value(),
                self.sms_aliyun_access_key_secret.get_secret_value(),
                self.sms_aliyun_sign_name,
                self.sms_aliyun_template_code,
                self.sms_aliyun_password_reset_template_code,
                self.sms_aliyun_phone_change_template_code,
            )
            if not all(required):
                raise ValueError("Production SMS configuration is incomplete")
            if self.app_env == "staging" and not re.fullmatch(
                r"\+861[3-9]\d{9}",
                self.sms_live_allowed_phone_e164.get_secret_value(),
            ):
                raise ValueError("Staging production SMS requires one allowed phone")
        if self.app_env == "production" and self.app_debug:
            raise ValueError("Production debug mode must be disabled")
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
            if self.file_scanner_provider == "development":
                raise ValueError(
                    "Production cannot use the Development file scanner"
                )
        if self.file_scanner_provider == "clamav":
            try:
                scanner_address = ip_address(self.file_scanner_host)
            except ValueError as exc:
                raise ValueError("ClamAV file scanner requires an explicit private IP address") from exc
            if (not scanner_address.is_private or scanner_address.is_unspecified
                    or scanner_address.is_multicast or (scanner_address.is_reserved and not scanner_address.is_loopback)
                    or scanner_address.is_link_local or "%" in self.file_scanner_host):
                raise ValueError("ClamAV file scanner requires a private unicast or loopback IP address")
        if self.identity_test_proof_enabled:
            target = make_url(self.active_database_url)
            actual_target = f"{target.host}:{target.port or 3306}/{target.database}"
            if (
                self.app_env != "test"
                or target.drivername != "mysql+pymysql"
                or target.host != "127.0.0.1"
                or (target.port or 3306) not in {3307, 3317}
                or target.database != "property_ai_auth001_test"
                or self.identity_test_proof_target != actual_target
                or not self.identity_test_proof_secret.get_secret_value()
            ):
                raise ValueError(
                    "Identity test proof requires exact AUTH001 test isolation and a private signing secret"
                )
        return self

    def _require_production_provider_credentials(self) -> None:
        if self.llm_provider == "production":
            if not self.llm_base_url or not self.llm_api_key.get_secret_value():
                raise ValueError("Production LLM requires LLM_BASE_URL and LLM_API_KEY")
        if self.rag_llm_provider == "production":
            if not self.rag_llm_base_url or not self.resolve_local_ai_key(
                self.rag_llm_api_key,
                self.rag_llm_base_url,
            ):
                raise ValueError(
                    "Production RAG LLM requires RAG_LLM_BASE_URL and RAG_LLM_API_KEY"
                )
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

    def resolve_local_ai_key(self, explicit: SecretStr, endpoint: str) -> str:
        """Use the shared token only for this host's explicitly local adapter."""

        value = explicit.get_secret_value()
        if value:
            return value
        local = self.local_ai_internal_token.get_secret_value()
        if not local:
            return ""
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "host.docker.internal",
        }:
            return ""
        return local

    @property
    def active_database_url(self) -> str:
        """Use the disposable database automatically in the test environment."""

        if self.app_env == "test":
            return self.test_database_url
        return self.database_url

    @property
    def resolved_celery_queue(self) -> str:
        """Environment-isolated Celery/RabbitMQ queue. Never share across APP_ENV."""

        if self.celery_task_queue.strip():
            return self.celery_task_queue.strip()
        return f"property_ai.{self.app_env}"

    @property
    def resolved_celery_dlq(self) -> str:
        return f"{self.resolved_celery_queue}.dlq"


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""

    return Settings()
