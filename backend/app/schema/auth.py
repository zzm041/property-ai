"""Official credential request/response schemas."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DevelopmentTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int | None = Field(default=None, gt=0)
    seed_key: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_one_identifier(self) -> Self:
        has_user = self.user_id is not None
        has_seed = bool(self.seed_key)
        if has_user == has_seed:
            raise ValueError("Provide exactly one of user_id or seed_key")
        return self


class AccessTokenData(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int


class SessionTokenData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    auth_method: Literal["sms", "password", "wechat"]
    user_id: int


class SmsSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=20)
    purpose: Literal["login", "reset", "change_phone"] = "login"
    challenge_id: str = Field(min_length=8, max_length=128)
    challenge_answer: str = Field(min_length=1, max_length=16)


class VisualChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=20)
    purpose: Literal["login", "reset"] = "login"


class VisualChallengeData(BaseModel):
    challenge_id: str
    prompt: str
    expires_in: int


class SmsSendData(BaseModel):
    delivery_channel: str
    provider_mode: str
    masked_phone: str
    expires_in: int


class SmsLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class PhoneChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_phone: str = Field(min_length=8, max_length=20)
    old_code: str = Field(min_length=4, max_length=8)
    new_phone: str = Field(min_length=8, max_length=20)
    new_code: str = Field(min_length=4, max_length=8)


class PasswordResetVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class PasswordResetVerifyData(BaseModel):
    reset_token: str
    expires_in: int


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reset_token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


class TokenRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=16, max_length=256)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, min_length=16, max_length=256)


class WeChatBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
