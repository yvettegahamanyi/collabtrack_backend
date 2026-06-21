from datetime import datetime

from pydantic import BaseModel, Field

from app.models import AccountStatus
from app.schemas.meetings import MeetingEngagementMetrics


class GithubMetrics(BaseModel):
    total_commits: int = 0
    lines_changed: int = 0
    prs_created: int = 0
    prs_reviewed: int = 0
    comments: int = 0


class GoogleDocsMetrics(BaseModel):
    edits: int = 0
    comments: int = 0


class MemberParticipationOut(BaseModel):
    user_id: str
    name: str | None
    email: str
    account_status: AccountStatus | None = None
    github_connected: bool
    google_connected: bool
    github_login: str | None = None
    google_email_matched: bool | None = None
    github: GithubMetrics | None = None
    google_docs: GoogleDocsMetrics | None = None
    meeting_engagement: MeetingEngagementMetrics | None = None


class ContributionsOut(BaseModel):
    group_id: str
    last_synced_at: datetime | None = None
    members: list[MemberParticipationOut] = Field(default_factory=list)


class SyncOut(BaseModel):
    group_id: str
    synced_at: datetime
    members_synced: int
