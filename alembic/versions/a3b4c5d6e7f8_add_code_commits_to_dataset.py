"""add code_commits to collab_track_dataset

Revision ID: a3b4c5d6e7f8
Revises: f2b3c4d5e6a7
Create Date: 2026-06-28 17:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collab_track_dataset",
        sa.Column("code_commits", sa.Float(), nullable=False, server_default="0"),
    )
    op.alter_column("collab_track_dataset", "code_commits", server_default=None)


def downgrade() -> None:
    op.drop_column("collab_track_dataset", "code_commits")
