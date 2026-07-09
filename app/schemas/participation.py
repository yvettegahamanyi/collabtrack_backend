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


class GoogleDocSyncEvent(BaseModel):
    """Single Drive revision or comment that contributed to a member's score."""

    type: str  # edit | comment | comment_reply
    file_id: str
    source_id: str | None = None
    author_email: str | None = None
    author_name: str | None = None
    matched_email: str | None = None
    match_method: str | None = None  # email | me
    timestamp: str | None = None


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
    google_docs_events: list[GoogleDocSyncEvent] = Field(default_factory=list)
    meeting_engagement: MeetingEngagementMetrics | None = None


class ContributionsOut(BaseModel):
    group_id: str
    last_synced_at: datetime | None = None
    members: list[MemberParticipationOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SyncOut(BaseModel):
    group_id: str
    synced_at: datetime
    members_synced: int
    warnings: list[str] = Field(default_factory=list)
