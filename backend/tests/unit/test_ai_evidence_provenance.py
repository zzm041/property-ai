"""Fake vs real AI evidence must not be mixed."""

from app.core.config import Settings
from app.provider.factory import build_llm_provider
from app.provider.llm import DevelopmentLLMProvider, QwenLLMProvider
from app.service.auto_dispatch import AutoDispatchService
from app.domain.ai import AiRisk, MappedAnalysis
from app.domain.work_order import WorkOrderCategory, WorkOrderPriority, WorkOrderSeverity


def test_development_llm_is_not_production_evidence() -> None:
    settings = Settings(_env_file=None, llm_provider="development")
    provider = build_llm_provider(settings)
    assert isinstance(provider, DevelopmentLLMProvider)
    assert provider.model_version.startswith("dev-")


def test_production_llm_adapter_is_distinct() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="production",
        llm_base_url="https://example.invalid/v1",
        llm_api_key="not-a-real-key",
        llm_model="qwen-plus",
    )
    provider = build_llm_provider(settings)
    assert isinstance(provider, QwenLLMProvider)
    assert provider.model_version == "qwen-plus"


def test_auto_dispatch_stays_off_by_default_and_blocks_without_schema() -> None:
    settings = Settings(_env_file=None, ai_auto_assign=False)
    mapped = MappedAnalysis(
        category=WorkOrderCategory.ELECTRICAL,
        subcategory="outlet",
        severity=WorkOrderSeverity.HIGH,
        priority=WorkOrderPriority.HIGH,
        summary="test",
        risk=AiRisk.NONE,
        required_role="STAFF",
        required_skill="electrician",
        required_duty="MAINTENANCE",
        confidence=0.9,
        low_confidence=False,
    )
    order = object()
    assert AutoDispatchService(None, settings=settings).maybe_assign(
        work_order=order, mapped=mapped, request_id="r1"
    ) == "DISABLED"
    on = Settings(_env_file=None, ai_auto_assign=True)
    assert AutoDispatchService(None, settings=on).maybe_assign(
        work_order=order, mapped=mapped, request_id="r1"
    ) == "SCHEMA_PENDING_0009"
    electric = MappedAnalysis(
        category=mapped.category,
        subcategory=mapped.subcategory,
        severity=mapped.severity,
        priority=mapped.priority,
        summary=mapped.summary,
        risk=AiRisk.ELECTRIC,
        required_role=mapped.required_role,
        required_skill=mapped.required_skill,
        required_duty=mapped.required_duty,
        confidence=mapped.confidence,
        low_confidence=False,
    )
    assert AutoDispatchService(None, settings=on).maybe_assign(
        work_order=order, mapped=electric, request_id="r1"
    ) == "SCHEMA_PENDING_0009"
    risky = MappedAnalysis(
        category=mapped.category,
        subcategory=mapped.subcategory,
        severity=mapped.severity,
        priority=mapped.priority,
        summary=mapped.summary,
        risk=AiRisk.GAS,
        required_role=mapped.required_role,
        required_skill=mapped.required_skill,
        required_duty=mapped.required_duty,
        confidence=mapped.confidence,
        low_confidence=False,
    )
    assert AutoDispatchService(None, settings=on).maybe_assign(
        work_order=order, mapped=risky, request_id="r1"
    ) == "HIGH_RISK"
