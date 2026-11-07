"""A staging SMS budget is armed once, only on an explicit operator command."""

import pytest

from app.core.config import Settings


class FakeRedis:
    def __init__(self):
        self.values = {}

    def config_get(self, name):
        return {name: "yes" if name == "appendonly" else "always"}

    def set(self, key, value, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


def test_arm_cli_requires_approval_and_never_resets_existing_budget():
    from app.cli.arm_sms_budget import main

    settings = Settings(
        _env_file=None,
        app_env="staging",
        app_secret_key="test-nondefault-secret-for-budget",
        sms_live_allowed_phone_e164="+8613800000000",
    )
    client = FakeRedis()
    with pytest.raises(RuntimeError, match="approval"):
        main(settings=settings, client=client, approval="")
    assert client.values == {}
    assert main(settings=settings, client=client, approval="AUTH-REAL-SMS-20261005") == 0
    before = dict(client.values)
    with pytest.raises(RuntimeError, match="already exists"):
        main(settings=settings, client=client, approval="AUTH-REAL-SMS-20261005")
    assert client.values == before


def test_arm_cli_refuses_redis_without_always_fsync():
    from app.cli.arm_sms_budget import main

    settings = Settings(
        _env_file=None,
        app_env="staging",
        app_secret_key="test-nondefault-secret-for-budget",
        sms_live_allowed_phone_e164="+8613800000000",
    )
    client = FakeRedis()
    client.config_get = lambda name: {name: "yes" if name == "appendonly" else "everysec"}
    with pytest.raises(RuntimeError, match="appendfsync"):
        main(settings=settings, client=client, approval="AUTH-REAL-SMS-20261005")
    assert client.values == {}


def test_arm_cli_refuses_redis_without_appendonly():
    from app.cli.arm_sms_budget import main

    settings = Settings(
        _env_file=None,
        app_env="staging",
        app_secret_key="test-nondefault-secret-for-budget",
        sms_live_allowed_phone_e164="+8613800000000",
    )
    client = FakeRedis()
    client.config_get = lambda name: {name: "no" if name == "appendonly" else "always"}
    with pytest.raises(RuntimeError, match="appendonly"):
        main(settings=settings, client=client, approval="AUTH-REAL-SMS-20261005")
    assert client.values == {}
