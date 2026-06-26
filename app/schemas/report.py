from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ContributionReportStatus, ReportStatus
from app.schemas.participation import ContributionsOut


class AttendanceMemberPreview(BaseModel):
    name: str
    email: str
    duration_minutes: int
    was_facilitator: bool


class AttendancePreviewOut(BaseModel):
    members: list[AttendanceMemberPreview]


class MeetingInputMeta(BaseModel):
    session_label: str = Field(min_length=1, max_length=255)
    session_date: date
    duration_minutes: int = Field(gt=0)


class AssignmentReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: str
    group_name: str | None
    group_number: int | None
    assignment_id: str
    report_status: ReportStatus | None
    created_at: datetime
    contribution_report_id: str | None = None
    contribution_report_status: ContributionReportStatus | None = None
    notification_sent_at: datetime | None = None


class AssignmentReportDetailOut(AssignmentReportOut):
    contributions: ContributionsOut | None = None


class CreateReportOut(BaseModel):
    group_id: str
    group_name: str
    group_number: int
    assignment_id: str
    report_status: ReportStatus
    members_provisioned: int
    meetings_created: int
