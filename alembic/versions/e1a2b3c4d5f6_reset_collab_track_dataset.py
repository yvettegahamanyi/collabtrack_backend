"""reset collab_track_dataset schema

Revision ID: e1a2b3c4d5f6
Revises: c9a1f3e82d04
Create Date: 2026-06-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, Sequence[str], None] = "c9a1f3e82d04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE collab_track_dataset")
    op.drop_column("collab_track_dataset", "assignment_type")
    op.drop_column("collab_track_dataset", "commit_consistency")
    op.create_unique_constraint(
        "uq_collab_track_dataset_group_student",
        "collab_track_dataset",
        ["group_id", "student_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_collab_track_dataset_group_student",
        "collab_track_dataset",
        type_="unique",
    )
    op.add_column(
        "collab_track_dataset",
        sa.Column("commit_consistency", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "collab_track_dataset",
        sa.Column("assignment_type", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("collab_track_dataset", "commit_consistency", server_default=None)
    op.alter_column("collab_track_dataset", "assignment_type", server_default=None)
