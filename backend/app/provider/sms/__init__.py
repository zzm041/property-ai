"""短信 Provider。Development 实现不得连真实运营商，且不得作为真实短信验收。"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SmsSendResult:
    delivery_channel: str
    provider_mode: str


class SmsProvider:
    delivery_channel: str
    provider_mode: str

    def send(self, *, phone_e164: str, purpose: str, code: str) -> SmsSendResult:
        raise NotImplementedError


class DevelopmentSmsProvider(SmsProvider):
    """Labeled development substitute. Tests may inspect captured codes in-process."""

    delivery_channel = "development"
    provider_mode = "DEVELOPMENT"

    def __init__(self) -> None:
        self.captured: list[dict[str, str]] = []

    def send(self, *, phone_e164: str, purpose: str, code: str) -> SmsSendResult:
        self.captured.append(
            {"phone_e164": phone_e164, "purpose": purpose, "code": code}
        )
        return SmsSendResult(
            delivery_channel=self.delivery_channel,
            provider_mode=self.provider_mode,
        )

    def last_code(self, phone_e164: str, purpose: str) -> str | None:
        for item in reversed(self.captured):
            if item["phone_e164"] == phone_e164 and item["purpose"] == purpose:
                return item["code"]
        return None
