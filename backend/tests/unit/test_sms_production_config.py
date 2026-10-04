"""Production SMS cannot start from incomplete staging configuration."""

import pytest

from app.core.config import Settings


def _production_sms(**overrides):
    values = {
        "_env_file": None,
        "app_env": "staging",
        "sms_provider": "production",
        "sms_aliyun_access_key_id": "test-id",
        "sms_aliyun_access_key_secret": "test-secret",
        "sms_aliyun_sign_name": "test-system-sign",
        "sms_aliyun_template_code": "100001",
        "sms_aliyun_password_reset_template_code": "100003",
        "sms_aliyun_phone_change_template_code": "100002",
        "sms_live_allowed_phone_e164": "+8613800000000",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "missing",
    [
        "sms_aliyun_access_key_id",
        "sms_aliyun_access_key_secret",
        "sms_aliyun_sign_name",
        "sms_aliyun_template_code",
        "sms_aliyun_password_reset_template_code",
        "sms_aliyun_phone_change_template_code",
        "sms_live_allowed_phone_e164",
    ],
)
def test_staging_production_sms_refuses_missing_value(missing):
    with pytest.raises(ValueError, match="SMS"):
        _production_sms(**{missing: ""})


def test_production_sms_secrets_and_phone_are_masked():
    settings = _production_sms()
    rendered = repr(settings)
    assert "test-secret" not in rendered
    assert "test-id" not in rendered
    assert "+8613800000000" not in rendered
    assert settings.sms_live_allowed_phone_e164.get_secret_value() == "+8613800000000"
