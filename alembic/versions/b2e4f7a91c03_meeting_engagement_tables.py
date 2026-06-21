"""meeting engagement tables

Revision ID: b2e4f7a91c03
Revises: f3a8c1d92b05
Create Date: 2026-06-21 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b2e4f7a91c03"
down_revision: Union[str, Sequence[str], None] = "f3a8c1d92b05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

meeting_session_status = postgresql.ENUM(
    "PENDING",
    "UPLOADED",
    "NEEDS_MAPPING",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    name="meetingsessionstatus",
    create_type=False,
)
meeting_file_type = postgresql.ENUM(
    "ATTENDANCE",
    "TRANSCRIPT",
    "CHAT",
    name="meetingfiletype",
    create_type=False,
)


def upgrade() -> None:
    meeting_session_status.create(op.get_bind(), checkfirst=True)
    meeting_file_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "meeting_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("session_label", sa.String(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            meeting_session_status,
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.String(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("unmapped_names", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "session_date", name="uq_meeting_group_date"),
    )

    op.create_table(
        "meeting_session_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("meeting_session_id", sa.String(), nullable=False),
        sa.Column("file_type", meeting_file_type, nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_session_id"], ["meeting_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "meeting_name_mappings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id", "display_name", name="uq_meeting_mapping_group_name"
        ),
    )

    op.create_table(
        "meeting_raw_metrics",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("meeting_session_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "was_facilitator",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("speaking_turns", sa.Integer(), nullable=False),
        sa.Column("chat_messages", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_session_id"], ["meeting_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "engagement_scores",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("attendance_ratio", sa.Float(), nullable=False),
        sa.Column("speaking_ratio", sa.Float(), nullable=False),
        sa.Column("chat_participation", sa.Float(), nullable=False),
        sa.Column("meeting_lead_count", sa.Integer(), nullable=False),
        sa.Column("sessions_attended", sa.Integer(), nullable=False),
        sa.Column("total_sessions", sa.Integer(), nullable=False),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_engagement_group_user"),
    )


def downgrade() -> None:
    op.drop_table("engagement_scores")
    op.drop_table("meeting_raw_metrics")
    op.drop_table("meeting_name_mappings")
    op.drop_table("meeting_session_files")
    op.drop_table("meeting_sessions")
    meeting_file_type.drop(op.get_bind(), checkfirst=True)
    meeting_session_status.drop(op.get_bind(), checkfirst=True)
