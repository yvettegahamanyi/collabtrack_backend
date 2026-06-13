"""collab_track_dataset table

Revision ID: c4d8e2f91a03
Revises: eeb96686ef1f
Create Date: 2026-06-10 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d8e2f91a03"
down_revision: Union[str, Sequence[str], None] = "eeb96686ef1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collab_track_dataset",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("assignment_type", sa.String(), nullable=False),
        sa.Column("commit_consistency", sa.Float(), nullable=False),
        sa.Column("code_share", sa.Float(), nullable=False),
        sa.Column("review_participation", sa.Float(), nullable=False),
        sa.Column("attendance_ratio", sa.Float(), nullable=False),
        sa.Column("speaking_participation_ratio", sa.Float(), nullable=False),
        sa.Column("chat_participation_ratio", sa.Float(), nullable=False),
        sa.Column("docs_contribution_share", sa.Float(), nullable=False),
        sa.Column("comment_activity", sa.Float(), nullable=False),
        sa.Column("benchmark_score", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_collab_track_dataset_group_id"),
        "collab_track_dataset",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collab_track_dataset_student_id"),
        "collab_track_dataset",
        ["student_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_collab_track_dataset_student_id"), table_name="collab_track_dataset"
    )
    op.drop_index(
        op.f("ix_collab_track_dataset_group_id"), table_name="collab_track_dataset"
    )
    op.drop_table("collab_track_dataset")
