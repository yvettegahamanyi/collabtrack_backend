from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import RoleType


class UserOut(BaseModel):
    """Public representation of a user."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "85ad9613-6a57-4c96-b087-1834cfbc79fd",
                "name": "Jane Doe",
                "email": "student@example.com",
                "role": "STUDENT",
                "is_active": True,
                "created_at": "2026-06-10T13:34:32.859813Z",
            }
        },
    )

    id: str
    name: str | None
    email: str
    role: RoleType | None
    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    """Profile update used during onboarding (and general profile edits)."""

    name: str | None = Field(default=None, examples=["Jane Doe"])
    role: RoleType | None = Field(
        default=None,
        description="Self-assigned during onboarding. Only STUDENT or INSTRUCTOR.",
        examples=["STUDENT"],
    )

    @field_validator("role")
    @classmethod
    def role_must_be_onboardable(cls, v: RoleType | None) -> RoleType | None:
        # Users can only self-assign STUDENT or INSTRUCTOR during onboarding.
        if v is not None and v not in (RoleType.STUDENT, RoleType.INSTRUCTOR):
            raise ValueError("role must be either STUDENT or INSTRUCTOR")
        return v
