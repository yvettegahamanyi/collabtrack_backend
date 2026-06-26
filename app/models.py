import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class IntegrationProviderType(TypeDecorator):
    """Bind integration provider enum values (github/google), not Python names."""

    impl = PG_ENUM(
        "github",
        "google",
        name="integrationprovider",
        create_type=False,
    )
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, IntegrationProvider):
            return value.value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return IntegrationProvider(value)


def generate_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class RoleType(str, enum.Enum):
    STUDENT = "STUDENT"
    INSTRUCTOR = "INSTRUCTOR"
    ADMIN = "ADMIN"


class PlatformType(str, enum.Enum):
    GITHUB = "GITHUB"
    GOOGLE_DOCS = "GOOGLE_DOCS"


class IntegrationProvider(str, enum.Enum):
    github = "github"
    google = "google"


class ServiceType(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DONE = "DONE"


class AssetType(str, enum.Enum):
    GITHUB_REPOSITORY = "GITHUB_REPOSITORY"
    GOOGLE_DRIVE_DOC = "GOOGLE_DRIVE_DOC"


class GroupMemberRole(str, enum.Enum):
    """Role within a project group (distinct from the global User.role)."""

    STUDENT = "STUDENT"
    INSTRUCTOR = "INSTRUCTOR"


class AccountStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"


class MeetingSessionStatus(str, enum.Enum):
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    NEEDS_MAPPING = "NEEDS_MAPPING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeetingFileType(str, enum.Enum):
    ATTENDANCE = "ATTENDANCE"
    TRANSCRIPT = "TRANSCRIPT"
    CHAT = "CHAT"


class ReportStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class ContributionReportStatus(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    name: Mapped[str | None] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String)
    # Role is set during onboarding (student/instructor) or by the admin seed.
    role: Mapped[RoleType | None] = mapped_column(SAEnum(RoleType), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    account_status: Mapped[AccountStatus] = mapped_column(
        SAEnum(AccountStatus),
        default=AccountStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
        nullable=False,
    )
    has_logged_in: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    provisioned_by_instructor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    oauth_tokens: Mapped[list["StudentOAuthToken"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    integrations: Mapped[list["UserIntegration"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["GroupMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    classes_taught: Mapped[list["CourseClass"]] = relationship(
        back_populates="instructor", cascade="all, delete-orphan"
    )


class CourseClass(Base):
    __tablename__ = "classes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    instructor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    instructor: Mapped["User"] = relationship(back_populates="classes_taught")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="course_class", cascade="all, delete-orphan"
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id"))
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    supervisor_email: Mapped[str | None] = mapped_column(String)
    status: Mapped[ServiceType] = mapped_column(
        SAEnum(ServiceType), default=ServiceType.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    course_class: Mapped["CourseClass"] = relationship(back_populates="assignments")
    groups: Mapped[list["ProjectGroup"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )
    contribution_reports: Mapped[list["ContributionReport"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class UserIntegration(Base):
    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_integration_provider"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[IntegrationProvider] = mapped_column(IntegrationProviderType())
    provider_user_id: Mapped[str] = mapped_column(String)
    provider_login: Mapped[str | None] = mapped_column(String)
    provider_email: Mapped[str | None] = mapped_column(String)
    email_matched: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="integrations")


class GroupGithubRepo(Base):
    __tablename__ = "group_github_repos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    owner: Mapped[str] = mapped_column(String)
    repo: Mapped[str] = mapped_column(String)
    default_branch: Mapped[str | None] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="github_repos")


class GroupGoogleDoc(Base):
    __tablename__ = "group_google_docs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    file_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="google_docs")


class ParticipationSnapshot(Base):
    __tablename__ = "participation_snapshots"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_participation_group_user"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="participation_snapshots")
    user: Mapped["User"] = relationship()


class StudentOAuthToken(Base):
    __tablename__ = "student_oauth_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    platform: Mapped[PlatformType] = mapped_column(SAEnum(PlatformType))
    oauth_token: Mapped[str | None] = mapped_column(String)
    authorized_email: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student: Mapped["User"] = relationship(back_populates="oauth_tokens")


class ProjectGroup(Base):
    __tablename__ = "project_groups"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_name: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    assignment_status: Mapped[ServiceType] = mapped_column(
        SAEnum(ServiceType), default=ServiceType.ACTIVE
    )
    git_weight: Mapped[float | None] = mapped_column(Float)
    doc_weight: Mapped[float | None] = mapped_column(Float)
    transcript_weight: Mapped[float | None] = mapped_column(Float)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assignments.id"), nullable=True
    )
    group_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_status: Mapped[ReportStatus | None] = mapped_column(
        SAEnum(ReportStatus), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owner: Mapped["User"] = relationship(foreign_keys=[owner_id])
    assignment: Mapped["Assignment | None"] = relationship(back_populates="groups")
    memberships: Mapped[list["GroupMembership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    invitations: Mapped[list["GroupInvitation"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    assets: Mapped[list["SharedWorkspaceAsset"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    contribution_reports: Mapped[list["ContributionReport"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    github_repos: Mapped[list["GroupGithubRepo"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    google_docs: Mapped[list["GroupGoogleDoc"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    participation_snapshots: Mapped[list["ParticipationSnapshot"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    meeting_sessions: Mapped[list["MeetingSession"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    meeting_name_mappings: Mapped[list["MeetingNameMapping"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    engagement_scores: Mapped[list["EngagementScore"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_user"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[GroupMemberRole] = mapped_column(SAEnum(GroupMemberRole))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class GroupInvitation(Base):
    __tablename__ = "group_invitations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    role: Mapped[GroupMemberRole] = mapped_column(SAEnum(GroupMemberRole))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="invitations")
    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])


class SharedWorkspaceAsset(Base):
    __tablename__ = "shared_workspace_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    asset_type: Mapped[AssetType] = mapped_column(SAEnum(AssetType))
    target_url: Mapped[str | None] = mapped_column(String)
    connection_status: Mapped[str | None] = mapped_column(String)

    group: Mapped["ProjectGroup"] = relationship(back_populates="assets")
    sync_metrics: Mapped[list["SyncMetricsLog"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class SyncMetricsLog(Base):
    __tablename__ = "sync_metrics_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    asset_id: Mapped[str] = mapped_column(ForeignKey("shared_workspace_assets.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    commit_frequency: Mapped[int] = mapped_column(Integer, default=0)
    lines_of_code_changed: Mapped[int] = mapped_column(Integer, default=0)
    pr_acceptance_rate: Mapped[float] = mapped_column(Float, default=0.0)
    issue_resolution_count: Mapped[int] = mapped_column(Integer, default=0)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    revision_depth: Mapped[int] = mapped_column(Integer, default=0)
    comment_threads_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    asset: Mapped["SharedWorkspaceAsset"] = relationship(back_populates="sync_metrics")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    uploaded_by_student_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    file_url: Mapped[str | None] = mapped_column(String)
    ai_generated_summary: Mapped[str | None] = mapped_column(Text)
    word_count: Mapped[int | None] = mapped_column(Integer)
    speaking_turns: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="transcripts")


class ContributionReport(Base):
    __tablename__ = "contribution_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assignments.id"), nullable=True
    )
    status: Mapped[ContributionReportStatus] = mapped_column(
        SAEnum(ContributionReportStatus),
        default=ContributionReportStatus.PENDING,
        server_default=text("'PENDING'"),
        nullable=False,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    notification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_calculated_scores: Mapped[dict | None] = mapped_column(JSON)
    html_detailed_summary_report: Mapped[str | None] = mapped_column(Text)

    group: Mapped["ProjectGroup"] = relationship(back_populates="contribution_reports")
    assignment: Mapped["Assignment | None"] = relationship(
        back_populates="contribution_reports"
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # We store a SHA-256 hash of the reset token, never the raw token.
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="password_reset_tokens")


class MeetingSession(Base):
    __tablename__ = "meeting_sessions"
    __table_args__ = (
        UniqueConstraint("group_id", "session_date", name="uq_meeting_group_date"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    session_label: Mapped[str] = mapped_column(String)
    session_date: Mapped[date] = mapped_column(Date)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[MeetingSessionStatus] = mapped_column(
        SAEnum(MeetingSessionStatus),
        default=MeetingSessionStatus.PENDING,
        server_default=text("'PENDING'"),
        nullable=False,
    )
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    unmapped_names: Mapped[list | None] = mapped_column(JSON, nullable=True)

    group: Mapped["ProjectGroup"] = relationship(back_populates="meeting_sessions")
    uploaded_by_user: Mapped["User | None"] = relationship(foreign_keys=[uploaded_by])
    files: Mapped[list["MeetingSessionFile"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    raw_metrics: Mapped[list["MeetingRawMetric"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class MeetingSessionFile(Base):
    __tablename__ = "meeting_session_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    meeting_session_id: Mapped[str] = mapped_column(ForeignKey("meeting_sessions.id"))
    file_type: Mapped[MeetingFileType] = mapped_column(SAEnum(MeetingFileType))
    storage_path: Mapped[str] = mapped_column(String)
    original_filename: Mapped[str] = mapped_column(String)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["MeetingSession"] = relationship(back_populates="files")


class MeetingNameMapping(Base):
    __tablename__ = "meeting_name_mappings"
    __table_args__ = (
        UniqueConstraint("group_id", "display_name", name="uq_meeting_mapping_group_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    display_name: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="meeting_name_mappings")
    user: Mapped["User"] = relationship()


class MeetingRawMetric(Base):
    __tablename__ = "meeting_raw_metrics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    meeting_session_id: Mapped[str] = mapped_column(ForeignKey("meeting_sessions.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    was_facilitator: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    speaking_turns: Mapped[int] = mapped_column(Integer, default=0)
    chat_messages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["MeetingSession"] = relationship(back_populates="raw_metrics")
    user: Mapped["User"] = relationship()


class EngagementScore(Base):
    __tablename__ = "engagement_scores"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_engagement_group_user"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("project_groups.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    attendance_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    speaking_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    chat_participation: Mapped[float] = mapped_column(Float, default=0.0)
    meeting_lead_count: Mapped[int] = mapped_column(Integer, default=0)
    sessions_attended: Mapped[int] = mapped_column(Integer, default=0)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["ProjectGroup"] = relationship(back_populates="engagement_scores")
    user: Mapped["User"] = relationship()


class CollabTrackDataset(Base):
    __tablename__ = "collab_track_dataset"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    student_id: Mapped[str] = mapped_column(String, index=True)
    group_id: Mapped[str] = mapped_column(String, index=True)
    assignment_type: Mapped[str] = mapped_column(String)
    commit_consistency: Mapped[float] = mapped_column(Float)
    code_share: Mapped[float] = mapped_column(Float)
    review_participation: Mapped[float] = mapped_column(Float)
    attendance_ratio: Mapped[float] = mapped_column(Float)
    speaking_participation_ratio: Mapped[float] = mapped_column(Float)
    chat_participation_ratio: Mapped[float] = mapped_column(Float)
    docs_contribution_share: Mapped[float] = mapped_column(Float)
    comment_activity: Mapped[float] = mapped_column(Float)
    benchmark_score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
