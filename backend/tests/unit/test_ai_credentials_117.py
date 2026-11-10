"""PA-117: staging must not report ready while the work-order LLM has no credential.

Before the fix `Settings` only validated provider credentials under
`APP_ENV=production`, so a staging box with `LLM_PROVIDER=production` and an empty
`LLM_API_KEY` started fine, answered `/readyz` 200/ready, and failed only when
analysis ran — `ProviderUnavailableError("LLM is unavailable")` in ~0 ms. These
tests pin both halves of the fix: the fully-credential-less box refuses to start,
and every other combination is visible in the readiness payload.
"""

import pytest

from app.core.config import Settings
from app.core.readiness import check_readiness


def staging_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "staging",
        "database_url": "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
    }
    values.update(overrides)
    return Settings(**values)


def ok(_settings) -> None:
    return None


def test_staging_with_no_ai_credential_at_all_refuses_to_start():
    with pytest.raises(ValueError, match="LLM_API_KEY or LOCAL_AI_INTERNAL_TOKEN"):
        staging_settings(llm_provider="production")


def test_staging_with_only_the_local_token_starts_and_reports_the_missing_key():
    settings = staging_settings(
        llm_provider="production",
        local_ai_internal_token="shared-token-0123456789abcdef0123456789abcdef",
    )

    credentials = settings.ai_credentials()

    # The container that produced PA-117 looked exactly like this: a local token,
    # no LLM_API_KEY, provider=production => guaranteed ProviderUnavailableError.
    assert credentials["status"] == "missing"
    assert credentials["missing"] == ["LLM_API_KEY"]
    assert credentials["sources"] == {
        "llm_api_key": "missing",
        "local_ai_internal_token": "configured",
    }
    assert credentials["fallback_to_local_token"] is False


def test_staging_with_the_llm_key_reports_configured():
    credentials = staging_settings(
        llm_provider="production",
        llm_api_key="llm-key-0123456789abcdef",
    ).ai_credentials()

    assert credentials["status"] == "configured"
    assert credentials["missing"] == []
    assert credentials["sources"]["llm_api_key"] == "configured"


def test_development_provider_needs_no_credential():
    settings = staging_settings()  # llm_provider defaults to "development"

    credentials = settings.ai_credentials()

    assert credentials["status"] == "not_required"
    assert credentials["missing"] == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"llm_provider": "production", "llm_api_key": "llm-key-0123456789abcdef"}, "configured"),
        (
            {
                "llm_provider": "production",
                "local_ai_internal_token": "shared-token-0123456789abcdef0123456789abcdef",
            },
            "missing",
        ),
    ],
)
def test_readiness_payload_exposes_the_credential_state(overrides, expected):
    settings = staging_settings(**overrides)

    result, status = check_readiness(
        settings, {"mysql": (True, ok)}, include_queue=False
    )

    assert status == 200
    assert result["ai_credentials"]["status"] == expected
    # PA-117 scope: exposed, not enforced — api/beat/publisher never call the model.
    assert result["status"] == "ready"
