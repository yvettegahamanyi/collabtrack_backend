import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owner: Mapped["User"] = relationship(foreign_keys=[owner_id])
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
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    final_calculated_scores: Mapped[dict | None] = mapped_column(JSON)
    html_detailed_summary_report: Mapped[str | None] = mapped_column(Text)

    group: Mapped["ProjectGroup"] = relationship(back_populates="contribution_reports")


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
