"""Drop legacy unused tables

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-07-19

Removes initial-schema tables superseded by later integrations:
- sync_metrics_log + shared_workspace_assets -> participation_snapshots, group_github_repos, group_google_docs
- student_oauth_tokens -> user_integrations
- transcripts -> meeting_sessions + meeting_session_files
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("sync_metrics_log")
    op.drop_table("shared_workspace_assets")
    op.drop_table("student_oauth_tokens")
    op.drop_table("transcripts")

    op.execute("DROP TYPE IF EXISTS assettype")
    op.execute("DROP TYPE IF EXISTS platformtype")


def downgrade() -> None:
    import sqlalchemy as sa

    platformtype = sa.Enum("GITHUB", "GOOGLE_DOCS", name="platformtype")
    assettype = sa.Enum("GITHUB_REPOSITORY", "GOOGLE_DRIVE_DOC", name="assettype")

    platformtype.create(op.get_bind(), checkfirst=True)
    assettype.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "shared_workspace_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("asset_type", assettype, nullable=False),
        sa.Column("target_url", sa.String(), nullable=True),
        sa.Column("connection_status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "student_oauth_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("platform", platformtype, nullable=False),
        sa.Column("oauth_token", sa.String(), nullable=True),
        sa.Column("authorized_email", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transcripts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("uploaded_by_student_id", sa.String(), nullable=False),
        sa.Column("file_url", sa.String(), nullable=True),
        sa.Column("ai_generated_summary", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("speaking_turns", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_student_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sync_metrics_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("commit_frequency", sa.Integer(), nullable=False),
        sa.Column("lines_of_code_changed", sa.Integer(), nullable=False),
        sa.Column("pr_acceptance_rate", sa.Float(), nullable=False),
        sa.Column("issue_resolution_count", sa.Integer(), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False),
        sa.Column("revision_depth", sa.Integer(), nullable=False),
        sa.Column("comment_threads_count", sa.Integer(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["shared_workspace_assets.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
