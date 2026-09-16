"""WeChat Mini Program code exchange. Services never call WeChat HTTP directly."""

from dataclasses import dataclass

import httpx

from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError


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

    def __init__(self, *, appid: str = "wx-dev-app") -> None:
        self.appid = appid

    def exchange_code(self, code: str) -> WeChatSession:
        if not code or code in {"wx-invalid", "invalid"}:
            raise ProviderInvalidOutputError("WeChat code is invalid")
        if code in {"wx-unavailable"}:
            raise ProviderUnavailableError("WeChat auth is unavailable")
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
    ) -> None:
        self.appid = appid
        self._secret = secret
        self.timeout_seconds = timeout_seconds

    def exchange_code(self, code: str) -> WeChatSession:
        try:
            response = httpx.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": self.appid,
                    "secret": self._secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                timeout=self.timeout_seconds,
            )
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - vendor client surface
            raise ProviderUnavailableError("WeChat auth is unavailable") from exc
        if "openid" not in payload:
            raise ProviderInvalidOutputError("WeChat code is invalid")
        return WeChatSession(
            openid=str(payload["openid"]),
            unionid=str(payload["unionid"]) if payload.get("unionid") else None,
            appid=self.appid,
        )
