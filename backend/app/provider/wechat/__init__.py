"""WeChat Mini Program code exchange. Services never call WeChat HTTP directly."""

from dataclasses import dataclass
import logging

import httpx

from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError

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
        client: httpx.Client | None = None,
    ) -> None:
        self.appid = appid
        self._secret = secret
        self.timeout_seconds = timeout_seconds
        self._client = client

    def exchange_code(self, code: str) -> WeChatSession:
        http = self._client or httpx.Client(timeout=self.timeout_seconds)
        own_client = self._client is None
        try:
            response = http.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": self.appid,
                    "secret": self._secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            payload = response.json()
        except Exception:
            raise ProviderUnavailableError("WeChat auth is unavailable")
        finally:
            if own_client:
                http.close()
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
