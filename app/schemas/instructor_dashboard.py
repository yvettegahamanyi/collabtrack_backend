from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import ReportStatus, ServiceType


class DashboardSummaryOut(BaseModel):
    class_count: int
    active_assignment_count: int
    reports_ready_count: int
    failed_report_count: int


class DashboardReportRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: str
    group_name: str | None
    group_number: int | None
    assignment_id: str
    assignment_title: str
    class_id: str
    class_name: str
    report_status: ReportStatus | None
    created_at: datetime
    notification_sent_at: datetime | None = None
    member_count: int = 0


class DashboardAssignmentActionOut(BaseModel):
    id: str
    title: str
    class_id: str
    class_name: str
    status: ServiceType


class TeamClassificationCountOut(BaseModel):
    archetype: str
    label: str
    count: int


class InstructorDashboardOut(BaseModel):
    summary: DashboardSummaryOut
    recent_reports: list[DashboardReportRowOut]
    all_reports: list[DashboardReportRowOut]
    failed_reports: list[DashboardReportRowOut]
    assignments_without_reports: list[DashboardAssignmentActionOut]
    team_classifications: list[TeamClassificationCountOut]
    classified_group_count: int = 0
    unclassified_group_count: int = 0
