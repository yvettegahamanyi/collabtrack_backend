from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import AccountStatus, GroupMemberRole, ServiceType


class GroupCreate(BaseModel):
    group_name: str = Field(min_length=1, max_length=255, examples=["Team Alpha"])
    description: str | None = Field(default=None, examples=["Capstone project group"])
    assignment_status: ServiceType = Field(
        default=ServiceType.ACTIVE, examples=["ACTIVE"]
    )


class GroupUpdate(BaseModel):
    group_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    assignment_status: ServiceType | None = None


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_name: str | None
    description: str | None
    assignment_status: ServiceType
    git_weight: float | None
    doc_weight: float | None
    transcript_weight: float | None
    owner_id: str
    created_at: datetime


class MemberOut(BaseModel):
    user_id: str
    name: str | None
    email: str
    role: GroupMemberRole
    is_owner: bool
    joined_at: datetime
    account_status: AccountStatus | None = None


class AddGroupMemberRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["Jane Doe"])
    email: EmailStr = Field(examples=["jane@university.edu"])

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class GroupDetailOut(GroupOut):
    members: list[MemberOut] = []


class InviteCreate(BaseModel):
    role: GroupMemberRole = Field(examples=["STUDENT"])
    expires_in_hours: int = Field(default=72, ge=1, le=168, examples=[72])


class InviteOut(BaseModel):
    token: str
    invite_url: str
    role: GroupMemberRole
    expires_at: datetime
    group_id: str


class InviteDetails(BaseModel):
    group_id: str
    group_name: str | None
    description: str | None
    role: GroupMemberRole
    expires_at: datetime


class InviteAcceptData(BaseModel):
    group_id: str
    role: GroupMemberRole
