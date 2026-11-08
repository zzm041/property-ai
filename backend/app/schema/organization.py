"""System-admin organization onboarding contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.phone import normalize_phone


class CreateOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_name: str = Field(min_length=1, max_length=200)
    tenant_contact: str | None = Field(default=None, max_length=255)
    property_name: str = Field(min_length=1, max_length=200)
    property_address: str = Field(min_length=1, max_length=500)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    initial_admin_phone: str = Field(min_length=1, max_length=32)

    @field_validator("initial_admin_phone")
    @classmethod
    def validate_admin_phone(cls, value: str) -> str:
        normalized = normalize_phone(value)
        if normalized is None:
            raise ValueError("initial_admin_phone must be a valid mobile number")
        return normalized


class OrganizationOnboardingData(BaseModel):
    tenant_id: int
    tenant_name: str
    property_id: int
    property_name: str
    initial_admin_user_id: int
    initial_admin_role: str
