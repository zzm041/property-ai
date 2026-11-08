"""Alibaba Cloud PNVS sender. Business OTP generation and verification stay local."""

from __future__ import annotations

import json
import re
from typing import Any

from alibabacloud_dypnsapi20170525 import models as pnvs_models
from alibabacloud_dypnsapi20170525.client import Client as PnvsClient
from alibabacloud_tea_openapi import models as openapi_models
from alibabacloud_tea_openapi.exceptions import ClientException
from alibabacloud_tea_util import models as util_models

from app.core.config import Settings
from app.provider.sms import SmsProvider, SmsSendResult
from app.provider.sms.budget import RedisSmsBudget


class SmsDeliveryRejected(RuntimeError):
    """PNVS definitively rejected the single send attempt."""


class SmsDeliveryUnknown(RuntimeError):
    """PNVS may have received the attempt; the budget stays blocked."""


class AliyunSmsProvider(SmsProvider):
    delivery_channel = "sms"
    provider_mode = "PRODUCTION"

    def __init__(
        self,
        *,
        settings: Settings,
        budget: RedisSmsBudget,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._client = client if client is not None else PnvsClient(
            openapi_models.Config(
                access_key_id=settings.sms_aliyun_access_key_id.get_secret_value(),
                access_key_secret=settings.sms_aliyun_access_key_secret.get_secret_value(),
                protocol="HTTPS",
                endpoint="dypnsapi.aliyuncs.com",
            )
        )

    def send(
        self, *, phone_e164: str, purpose: str, code: str, request_id: str = ""
    ) -> SmsSendResult:
        templates = {
            "login": self._settings.sms_aliyun_template_code,
            "reset": self._settings.sms_aliyun_password_reset_template_code,
            "change_phone": self._settings.sms_aliyun_phone_change_template_code,
        }
        if purpose not in templates or not re.fullmatch(r"\+861[3-9]\d{9}", phone_e164):
            raise SmsDeliveryRejected("SMS send request is invalid")
        if not re.fullmatch(r"\d{6}", code) or not request_id:
            raise SmsDeliveryRejected("SMS send request is invalid")

        self._budget.reserve(phone_e164, request_id)
        request = pnvs_models.SendSmsVerifyCodeRequest(
            auto_retry=0,
            code_length=6,
            country_code="86",
            duplicate_policy=1,
            out_id=request_id,
            phone_number=phone_e164[3:],
            return_verify_code=False,
            sign_name=self._settings.sms_aliyun_sign_name,
            template_code=templates[purpose],
            template_param=json.dumps(
                {
                    "code": code,
                    "min": str(max(1, self._settings.sms_otp_ttl_seconds // 60)),
                },
                separators=(",", ":"),
            ),
            valid_time=self._settings.sms_otp_ttl_seconds,
        )
        timeout_ms = int(self._settings.sms_aliyun_timeout_seconds * 1000)
        runtime = util_models.RuntimeOptions(
            autoretry=False,
            max_attempts=1,
            connect_timeout=timeout_ms,
            read_timeout=timeout_ms,
        )
        try:
            response = self._client.send_sms_verify_code_with_options(request, runtime)
        except ClientException as exc:
            server_request_id = str(exc.request_id or "").strip()
            if (
                isinstance(exc.status_code, int)
                and 400 <= exc.status_code < 500
                and server_request_id.lower() not in {"", "none", "null"}
            ):
                self._budget.finish(request_id)
                raise SmsDeliveryRejected("SMS provider rejected the send") from None
            raise SmsDeliveryUnknown("SMS send result is unconfirmed") from None
        except Exception:
            raise SmsDeliveryUnknown("SMS send result is unconfirmed") from None

        body = getattr(response, "body", None)
        if body is None or not getattr(body, "code", None) or getattr(body, "success", None) is None:
            raise SmsDeliveryUnknown("SMS send result is unconfirmed")
        if getattr(body, "success", None) is not True:
            self._budget.finish(request_id)
            raise SmsDeliveryRejected("SMS provider rejected the send")
        if getattr(body, "code", None) != "OK":
            self._budget.finish(request_id)
            raise SmsDeliveryRejected("SMS provider rejected the send")
        self._budget.finish(request_id)
        return SmsSendResult(
            delivery_channel=self.delivery_channel,
            provider_mode=self.provider_mode,
        )
