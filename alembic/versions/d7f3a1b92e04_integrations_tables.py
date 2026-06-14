"""integrations tables

Revision ID: d7f3a1b92e04
Revises: c4d8e2f91a03
Create Date: 2026-06-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d7f3a1b92e04"
down_revision: Union[str, Sequence[str], None] = "c4d8e2f91a03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: a prior failed run may have created the enum without tables.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE integrationprovider AS ENUM ('github', 'google');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    provider_enum = postgresql.ENUM(
        "github", "google", name="integrationprovider", create_type=False
    )

    op.create_table(
        "user_integrations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("provider_user_id", sa.String(), nullable=False),
        sa.Column("provider_login", sa.String(), nullable=True),
        sa.Column("provider_email", sa.String(), nullable=True),
        sa.Column(
            "email_matched",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_user_integration_provider"
        ),
    )

    op.create_table(
        "group_github_repos",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("default_branch", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "group_google_docs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "participation_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "metrics",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id", "user_id", name="uq_participation_group_user"
        ),
    )


def downgrade() -> None:
    op.drop_table("participation_snapshots")
    op.drop_table("group_google_docs")
    op.drop_table("group_github_repos")
    op.drop_table("user_integrations")
    postgresql.ENUM(name="integrationprovider").drop(
        op.get_bind(), checkfirst=True
    )
