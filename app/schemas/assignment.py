from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import ServiceType
from app.schemas.report import AssignmentReportOut


class AssignmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    supervisor_email: EmailStr | None = None
    status: ServiceType = ServiceType.ACTIVE


class AssignmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    supervisor_email: EmailStr | None = None
    status: ServiceType | None = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    class_id: str
    title: str
    description: str | None
    supervisor_email: str | None
    status: ServiceType
    created_at: datetime
    report_count: int = 0


class AssignmentDetailOut(AssignmentOut):
    class_name: str | None = None
    reports: list[AssignmentReportOut] = []
