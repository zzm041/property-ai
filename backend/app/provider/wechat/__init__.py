"""WeChat Mini Program code exchange. Services never call WeChat HTTP directly."""

from dataclasses import dataclass
from hashlib import sha256
import logging
import re

import httpx

from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError
from app.provider.http import request_json

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass(frozen=True, slots=True)
class WeChatSession:
    openid: str
    unionid: str | None
    appid: str


class WeChatAuthProvider:
    def exchange_code(self, code: str) -> WeChatSession:
        raise NotImplementedError


class DevelopmentWeChatAuthProvider(WeChatAuthProvider):
    """Maps deterministic fake codes. Never stores or returns session_key."""

    _DEVICE_CODE_PREFIX = "wx-dev-device-v1:"
    _DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{16,96}$")

    def __init__(self, *, appid: str = "wx-dev-app") -> None:
        self.appid = appid

    def exchange_code(self, code: str) -> WeChatSession:
        if not code or code in {"wx-invalid", "invalid"}:
            raise ProviderInvalidOutputError("WeChat code is invalid")
        if code in {"wx-unavailable"}:
            raise ProviderUnavailableError("WeChat auth is unavailable")
        if code.startswith(self._DEVICE_CODE_PREFIX):
            payload = code.removeprefix(self._DEVICE_CODE_PREFIX)
            device_id, separator, ephemeral_code = payload.partition(":")
            if (
                not separator
                or not self._DEVICE_ID.fullmatch(device_id)
                or not ephemeral_code
            ):
                raise ProviderInvalidOutputError("WeChat code is invalid")
            digest = sha256(device_id.encode("utf-8")).hexdigest()[:32]
            return WeChatSession(
                openid=f"openid-dev-device-{digest}",
                unionid=None,
                appid=self.appid,
            )
        if code.startswith("wx-new-"):
            suffix = code.removeprefix("wx-new-") or "anon"
            return WeChatSession(
                openid=f"openid-new-{suffix}",
                unionid=None,
                appid=self.appid,
            )
        return WeChatSession(
            openid=f"openid-{code}",
            unionid=None,
            appid=self.appid,
        )


class ProductionWeChatAuthProvider(WeChatAuthProvider):
    def __init__(
        self,
        *,
        appid: str,
        secret: str,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.appid = appid
        self._secret = secret
        self.timeout_seconds = timeout_seconds
        self._client = client

    def exchange_code(self, code: str) -> WeChatSession:
        try:
            payload = request_json(
                method="GET",
                url="https://api.weixin.qq.com/sns/jscode2session",
                timeout_seconds=self.timeout_seconds,
                params={
                    "appid": self.appid,
                    "secret": self._secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                client=self._client,
                provider="wechat",
                operation="jscode2session",
            )
        except (ProviderInvalidOutputError, ProviderUnavailableError):
            raise
        except Exception as exc:
            raise ProviderUnavailableError("WeChat auth is unavailable") from exc
        if not isinstance(payload, dict):
            raise ProviderInvalidOutputError("WeChat code is invalid")
        if "openid" not in payload or payload.get("errcode"):
            code = payload.get("errcode")
            if code in {45011, -1}:
                raise ProviderUnavailableError("WeChat auth is unavailable")
            raise ProviderInvalidOutputError("WeChat code is invalid")
        payload.pop("session_key", None)
        return WeChatSession(
            openid=str(payload["openid"]),
            unionid=str(payload["unionid"]) if payload.get("unionid") else None,
            appid=self.appid,
        )
