from datetime import date, datetime

from pydantic import BaseModel, Field


class MeetingSessionCreate(BaseModel):
    session_label: str | None = Field(default=None, min_length=1, max_length=200)
    session_date: date | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=600)


class NameMappingItem(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    user_id: str


class NameMappingSubmit(BaseModel):
    mappings: list[NameMappingItem]


class UnmappedNameOut(BaseModel):
    display_name: str


class MeetingSessionOut(BaseModel):
    id: str
    group_id: str
    session_label: str
    session_date: date | None
    duration_minutes: int | None
    status: str
    uploaded_at: datetime | None = None
    processed_at: datetime | None = None
    error_message: str | None = None
    unmapped_names: list[UnmappedNameOut] | None = None


class MeetingEngagementMetrics(BaseModel):
    attendance_ratio: float = 0.0
    speaking_ratio: float = 0.0
    chat_participation: float = 0.0
    meeting_lead_count: int = 0
    sessions_attended: int = 0
    total_sessions: int = 0


class StudentEngagementOut(BaseModel):
    user_id: str
    student_name: str
    attendance_ratio: float
    speaking_ratio: float
    chat_participation: float
    meeting_lead_count: int
    sessions_attended: int
    total_sessions: int


class GroupEngagementReport(BaseModel):
    group_id: str
    total_sessions: int
    last_updated: datetime | None
    engagement_scores: list[StudentEngagementOut]
