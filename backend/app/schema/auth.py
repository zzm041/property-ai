"""Development Auth request and response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DevelopmentTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)


class AccessTokenData(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int
