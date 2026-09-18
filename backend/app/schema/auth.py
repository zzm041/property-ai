"""Development Auth request and response schemas."""

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
