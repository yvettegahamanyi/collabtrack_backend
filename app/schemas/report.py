from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import ContributionReportStatus, ReportStatus
from app.schemas.participation import ContributionsOut


class MemberPreview(BaseModel):
    name: str
    email: str


class MembersPreviewOut(BaseModel):
    members: list[MemberPreview]


class ReportMemberInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr


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
    has_collaboration_resources: bool = False
    moodle_grade_sync_available: bool = False


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


class SetupReportOut(BaseModel):
    group_id: str
    group_name: str | None
    report_status: ReportStatus
    resources_linked: int
    meetings_queued: int


class MoodleGradeSyncStudentResult(BaseModel):
    user_id: str
    student_name: str
    moodle_user_id: str | None = None
    score_given: float | None = None
    status: str
    message: str | None = None


class MoodleGradeSyncOut(BaseModel):
    group_id: str
    assignment_id: str
    score_maximum: float
    synced_count: int
    failed_count: int
    skipped_count: int
    results: list[MoodleGradeSyncStudentResult]
    synced_at: datetime
