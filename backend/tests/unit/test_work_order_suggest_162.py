"""PA-162: resident pre-fill from an uploaded photo or voice note.

The suggestion is a convenience only. Every AI-side failure has to come back as
HTTP 200 with empty text plus a reason code, because the resident must always be
able to keep typing and submit by hand.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_principal
from app.api.v1.work_orders import (
    get_description_suggestion_service,
    router,
)
from app.core.config import Settings
from app.core.exception_handlers import install_exception_handlers
from app.core.rate_limit import MemoryRateLimiter
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.provider.asr import Transcript
from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError
from app.provider.vision import VisionInsight
from app.service.description_suggestion import (
    SUGGEST_TIMEOUT_SECONDS,
    DescriptionSuggestionService,
)

NOW = datetime(2026, 11, 11, 9, tzinfo=UTC)


def resident(*, user_id: int = 6, tenant: int = 1) -> Principal:
    scope = OrganizationScope(ScopeType.TENANT, tenant_id=tenant)
    return Principal(
        user_id=user_id,
        status="ACTIVE",
        display_name="162 synthetic resident",
        grants=(RoleGrant("RESIDENT", scope, frozenset({"work_order:create"})),),
    )


def staff(*, user_id: int = 4, tenant: int = 1) -> Principal:
    scope = OrganizationScope(ScopeType.TENANT, tenant_id=tenant)
    return Principal(
        user_id=user_id,
        status="ACTIVE",
        display_name="162 synthetic staff",
        grants=(RoleGrant("STAFF", scope, frozenset({"work_order:read"})),),
    )


def media(
    *,
    file_id: int = 11,
    mime_type: str = "image/jpeg",
    uploader_user_id: int = 6,
    tenant_id: int = 1,
    storage_key: str | None = None,
    scanner_status: str = "skipped_dev",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=file_id,
        mime_type=mime_type,
        uploader_user_id=uploader_user_id,
        tenant_id=tenant_id,
        storage_key=storage_key or f"tenant/{tenant_id}/file/{file_id}",
        scanner_status=scanner_status,
        content_hash="a" * 64,
        size_bytes=1024,
    )


class StubVLM:
    model_version = "stub-vlm"

    def __init__(self, *, description: str = "插座面板有烧灼痕迹，边缘发黑", error=None):
        self.description = description
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def inspect_image(self, *, storage_key: str, mime_type: str):
        self.calls.append((storage_key, mime_type))
        if self.error is not None:
            raise self.error
        return VisionInsight(
            description=self.description,
            labels=("socket",),
            confidence=0.72,
            model_version=self.model_version,
        )


class StubASR:
    model_version = "stub-asr"

    def __init__(self, *, text: str = "厨房水龙头一直滴水", error=None):
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def transcribe(self, *, storage_key: str, mime_type: str):
        self.calls.append((storage_key, mime_type))
        if self.error is not None:
            raise self.error
        return Transcript(text=self.text, confidence=0.9, model_version=self.model_version)


def build_service(
    *,
    files=(),
    vlm=None,
    asr=None,
    settings=None,
    limiter=None,
) -> DescriptionSuggestionService:
    settings = settings or Settings(_env_file=None)
    service = DescriptionSuggestionService(
        MagicMock(),
        settings=settings,
        vlm=vlm or StubVLM(),
        asr=asr or StubASR(),
        limiter=limiter or MemoryRateLimiter(),
    )
    service._repository = MagicMock()
    lookup = {item.id: item for item in files}
    service._repository.get_file.side_effect = lambda file_id: lookup.get(file_id)
    return service


def client_for(service, principal) -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_description_suggestion_service] = lambda: service
    return TestClient(app)


def suggest(service, principal=None, file_ids=(11,)):
    body = {"file_ids": list(file_ids)}
    return client_for(service, principal or resident()).post(
        "/api/v1/work-orders/suggest", json=body
    )


# --------------------------------------------------------------------- success
def test_photo_is_described_by_the_vision_provider():
    vlm = StubVLM(description="  插座面板有烧灼痕迹，\n边缘发黑  ")
    service = build_service(files=(media(),), vlm=vlm)

    response = suggest(service)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "text": "插座面板有烧灼痕迹， 边缘发黑",
        "source": "vlm",
        "reason_code": None,
    }
    assert vlm.calls == [("tenant/1/file/11", "image/jpeg")]


def test_voice_note_falls_back_to_the_transcript_provider():
    asr = StubASR(text="厨房水龙头一直滴水")
    service = build_service(
        files=(media(file_id=21, mime_type="audio/mpeg"),), asr=asr
    )

    response = suggest(service, file_ids=(21,))

    assert response.status_code == 200
    assert response.json()["data"] == {
        "text": "厨房水龙头一直滴水",
        "source": "asr",
        "reason_code": None,
    }
    assert asr.calls == [("tenant/1/file/21", "audio/mpeg")]


def test_photo_wins_when_the_resident_attached_both_kinds():
    vlm, asr = StubVLM(description="墙面渗水"), StubASR(text="漏水")
    service = build_service(
        files=(media(file_id=31, mime_type="audio/wav"), media(file_id=32)),
        vlm=vlm,
        asr=asr,
    )

    response = suggest(service, file_ids=(31, 32))

    assert response.json()["data"]["source"] == "vlm"
    assert response.json()["data"]["text"] == "墙面渗水"
    assert asr.calls == []


# --------------------------------------------------------------------- failure
def test_provider_timeout_answers_200_with_a_reason_code():
    service = build_service(
        files=(media(),), vlm=StubVLM(error=ProviderTimeoutError("VLM timed out"))
    )

    response = suggest(service)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "text": "",
        "source": "none",
        "reason_code": "AI.TIMEOUT",
    }


def test_provider_unavailable_answers_200_with_a_reason_code():
    service = build_service(
        files=(media(),),
        vlm=StubVLM(error=ProviderUnavailableError("VLM is unavailable")),
    )

    assert suggest(service).json()["data"] == {
        "text": "",
        "source": "none",
        "reason_code": "AI.UNAVAILABLE",
    }


@pytest.mark.parametrize("description", ["", "   ", "\n\t "])
def test_empty_model_output_is_never_prefilled(description):
    service = build_service(files=(media(),), vlm=StubVLM(description=description))

    response = suggest(service)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "text": "",
        "source": "none",
        "reason_code": "AI.INVALID_OUTPUT",
    }


def test_media_type_outside_the_pipeline_allow_list_is_refused():
    service = build_service(files=(media(mime_type="image/gif"),))

    response = suggest(service)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILE.UNSUPPORTED_TYPE"


def test_media_that_is_not_ready_answers_200_and_stays_out_of_the_form():
    service = build_service(files=(media(scanner_status="pending"),))

    response = suggest(service)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "text": "",
        "source": "none",
        "reason_code": "MEDIA_NOT_READY",
    }


def test_a_file_uploaded_by_someone_else_is_not_readable():
    service = build_service(files=(media(uploader_user_id=99),))

    response = suggest(service)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE.NOT_FOUND"


def test_only_a_resident_may_ask_for_a_suggestion():
    service = build_service(files=(media(),))

    response = suggest(service, principal=staff())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "IAM.FORBIDDEN"


def test_per_user_limit_blocks_the_second_call_in_the_window():
    service = build_service(
        files=(media(),),
        settings=Settings(_env_file=None, wo_suggest_limit_per_hour=1),
    )

    assert suggest(service).status_code == 200
    limited = suggest(service)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "WO.SUGGEST_RATE_LIMITED"
    assert limited.json()["error"]["details"] == {"limit": 1, "window_seconds": 3600}


def test_empty_file_list_is_rejected_as_input():
    service = build_service(files=(media(),))

    assert suggest(service, file_ids=()).status_code == 422


# ------------------------------------------------------------------ the budget
def test_the_suggestion_call_is_capped_at_fifteen_seconds(monkeypatch):
    captured: list[Settings] = []

    def _capture(settings):
        captured.append(settings)
        return StubVLM()

    monkeypatch.setattr(
        "app.service.description_suggestion.build_vlm_provider", _capture
    )
    monkeypatch.setattr(
        "app.service.description_suggestion.build_asr_provider",
        lambda settings: StubASR(),
    )
    settings = Settings(_env_file=None, asr_timeout_seconds=20.0, vlm_timeout_seconds=12.0)

    DescriptionSuggestionService(MagicMock(), settings=settings)

    assert SUGGEST_TIMEOUT_SECONDS == 15.0
    assert captured[0].asr_timeout_seconds == 15.0
    assert captured[0].vlm_timeout_seconds == 12.0
    # The stored settings keep the longer analysis deadlines untouched.
    assert settings.asr_timeout_seconds == 20.0


def test_route_is_registered_next_to_the_work_order_writes():
    paths = {(route.path, ",".join(sorted(route.methods))) for route in router.routes}

    assert ("/work-orders/suggest", "POST") in paths
