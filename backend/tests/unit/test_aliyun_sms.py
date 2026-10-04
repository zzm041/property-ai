"""PNVS request contract with a fake SDK transport; no network calls."""

import json

import pytest
from alibabacloud_dypnsapi20170525 import models
from alibabacloud_tea_openapi.exceptions import ClientException

from app.core.config import Settings


class Budget:
    def __init__(self):
        self.in_flight = None
        self.finished = []

    def reserve(self, phone_e164, request_id):
        self.in_flight = (phone_e164, request_id)

    def finish(self, request_id):
        self.finished.append(request_id)
        self.in_flight = None


class Client:
    def __init__(self, *, response=None, error=None):
        self.calls = []
        self.response = response if response is not None else models.SendSmsVerifyCodeResponse(
            body=models.SendSmsVerifyCodeResponseBody(
                code="OK", success=True, request_id="provider-request"
            )
        )
        self.error = error

    def send_sms_verify_code_with_options(self, request, runtime):
        self.calls.append((request, runtime))
        if self.error is not None:
            raise self.error
        return self.response


def _settings():
    return Settings(
        _env_file=None,
        app_env="staging",
        sms_provider="production",
        sms_aliyun_access_key_id="test-id",
        sms_aliyun_access_key_secret="test-secret",
        sms_aliyun_sign_name="system-sign",
        sms_aliyun_template_code="100001",
        sms_aliyun_password_reset_template_code="100003",
        sms_aliyun_phone_change_template_code="100002",
        sms_live_allowed_phone_e164="+8613800000000",
    )


@pytest.mark.parametrize(
    ("purpose", "template"),
    [("login", "100001"), ("reset", "100003"), ("change_phone", "100002")],
)
def test_pnvs_uses_purpose_template_and_disables_all_retries(purpose, template):
    from app.provider.sms.aliyun import AliyunSmsProvider

    budget = Budget()
    client = Client()
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    result = provider.send(
        phone_e164="+8613800000000", purpose=purpose, code="123456", request_id="req-1"
    )
    request, runtime = client.calls[0]
    assert result.provider_mode == "PRODUCTION"
    assert budget.finished == ["req-1"]
    assert request.phone_number == "13800000000"
    assert request.country_code == "86"
    assert request.sign_name == "system-sign"
    assert request.template_code == template
    assert json.loads(request.template_param) == {"code": "123456", "min": "5"}
    assert request.out_id == "req-1"
    assert request.valid_time == 300
    assert request.duplicate_policy == 1
    assert request.code_length == 6
    assert request.return_verify_code is False
    assert request.auto_retry == 0
    assert runtime.autoretry is False
    assert runtime.max_attempts == 1
    assert runtime.connect_timeout == 5000
    assert runtime.read_timeout == 5000


def test_business_rejection_counts_attempt_but_does_not_report_success():
    from app.provider.sms.aliyun import AliyunSmsProvider, SmsDeliveryRejected

    budget = Budget()
    client = Client(
        response=models.SendSmsVerifyCodeResponse(
            body=models.SendSmsVerifyCodeResponseBody(code="DENIED", success=False)
        )
    )
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    with pytest.raises(SmsDeliveryRejected):
        provider.send(
            phone_e164="+8613800000000", purpose="login", code="123456", request_id="req-2"
        )
    assert len(client.calls) == 1
    assert budget.finished == ["req-2"]


def test_unknown_transport_result_keeps_budget_in_flight_and_never_retries():
    from app.provider.sms.aliyun import AliyunSmsProvider, SmsDeliveryUnknown

    budget = Budget()
    client = Client(error=TimeoutError("injected timeout"))
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    with pytest.raises(SmsDeliveryUnknown, match="unconfirmed"):
        provider.send(
            phone_e164="+8613800000000", purpose="reset", code="123456", request_id="req-3"
        )
    assert len(client.calls) == 1
    assert budget.in_flight == ("+8613800000000", "req-3")
    assert budget.finished == []


def test_malformed_response_is_unknown_and_keeps_budget_blocked():
    from app.provider.sms.aliyun import AliyunSmsProvider, SmsDeliveryUnknown

    budget = Budget()
    client = Client(response=models.SendSmsVerifyCodeResponse())
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    with pytest.raises(SmsDeliveryUnknown):
        provider.send(
            phone_e164="+8613800000000", purpose="login", code="123456", request_id="req-5"
        )
    assert len(client.calls) == 1
    assert budget.in_flight == ("+8613800000000", "req-5")
    assert budget.finished == []


def test_explicit_sdk_client_rejection_counts_attempt_and_reopens_budget():
    from app.provider.sms.aliyun import AliyunSmsProvider, SmsDeliveryRejected

    budget = Budget()
    client = Client(error=ClientException(
        status_code=403, code="Forbidden", request_id="server-request",
        message="access denied",
    ))
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    with pytest.raises(SmsDeliveryRejected):
        provider.send(
            phone_e164="+8613800000000", purpose="login", code="123456", request_id="req-6"
        )
    assert len(client.calls) == 1
    assert budget.finished == ["req-6"]


@pytest.mark.parametrize("server_request_id", [None, "None", "null", " "])
def test_sdk_client_error_without_server_request_id_remains_unknown(server_request_id):
    from app.provider.sms.aliyun import AliyunSmsProvider, SmsDeliveryUnknown

    budget = Budget()
    client = Client(error=ClientException(
        status_code=403, code="Forbidden", request_id=server_request_id
    ))
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    with pytest.raises(SmsDeliveryUnknown):
        provider.send(
            phone_e164="+8613800000000", purpose="reset", code="123456", request_id="req-7"
        )
    assert budget.in_flight == ("+8613800000000", "req-7")
    assert budget.finished == []


def test_unapproved_phone_never_reaches_sdk():
    from app.provider.sms.aliyun import AliyunSmsProvider
    from app.provider.sms.budget import SmsBudgetUnavailable

    class RestrictingBudget(Budget):
        def reserve(self, phone_e164, request_id):
            raise SmsBudgetUnavailable("blocked")

    client = Client()
    provider = AliyunSmsProvider(settings=_settings(), budget=RestrictingBudget(), client=client)
    with pytest.raises(SmsBudgetUnavailable):
        provider.send(
            phone_e164="+8613900000000", purpose="login", code="123456", request_id="req-4"
        )
    assert client.calls == []


def test_sdk_client_uses_https(monkeypatch):
    from app.provider.sms import aliyun

    captured = {}
    monkeypatch.setattr(aliyun, "PnvsClient", lambda config: captured.setdefault("config", config))
    aliyun.AliyunSmsProvider(settings=_settings(), budget=Budget())
    assert captured["config"].protocol == "HTTPS"


def test_redis_outage_blocks_before_sdk_call():
    from app.provider.sms.aliyun import AliyunSmsProvider
    from app.provider.sms.budget import RedisSmsBudget, SmsBudgetUnavailable

    class BrokenRedis:
        def eval(self, *args):
            raise ConnectionError("injected isolated Redis outage")

    client = Client()
    budget = RedisSmsBudget(
        client=BrokenRedis(),
        allowed_phone_e164="+8613800000000",
        app_secret="test-budget-secret",
        key="sms:live-budget:test:outage",
    )
    provider = AliyunSmsProvider(settings=_settings(), budget=budget, client=client)
    with pytest.raises(SmsBudgetUnavailable):
        provider.send(
            phone_e164="+8613800000000", purpose="login", code="123456", request_id="req-8"
        )
    assert client.calls == []
