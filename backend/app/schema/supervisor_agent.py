"""Input contract for a fixed-scope, read-only supervisor review."""
from pydantic import BaseModel, ConfigDict, Field


class SupervisorAgentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    property_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=500)
