"""add is_sandbox flags for training users and groups

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_sandbox",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "project_groups",
        sa.Column(
            "is_sandbox",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE users
            SET is_sandbox = true
            WHERE email LIKE '%@collabtrack.local'
               OR id IN (SELECT user_id FROM training_collection_members)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE project_groups
            SET is_sandbox = true
            WHERE description = 'Sandbox group for training data collection'
               OR id IN (SELECT project_group_id FROM training_collections)
            """
        )
    )


def downgrade() -> None:
    op.drop_column("project_groups", "is_sandbox")
    op.drop_column("users", "is_sandbox")
