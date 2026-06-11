from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import GroupMemberRole, ServiceType


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


class MemberOut(BaseModel):
    user_id: str
    name: str | None
    email: str
    role: GroupMemberRole
    is_owner: bool
    joined_at: datetime
