"""participation scores and group dataset metadata

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project_groups",
        sa.Column("dataset_group_id", sa.String(), nullable=True),
    )
    op.add_column(
        "project_groups",
        sa.Column(
            "participation_scores_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "project_groups",
        sa.Column(
            "dataset_exported_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "member_participation_scores",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("predicted_score", sa.Float(), nullable=False),
        sa.Column("contributor_tier", sa.String(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_participation_score_group_user"),
    )


def downgrade() -> None:
    op.drop_table("member_participation_scores")
    op.drop_column("project_groups", "dataset_exported_at")
    op.drop_column("project_groups", "participation_scores_generated_at")
    op.drop_column("project_groups", "dataset_group_id")
