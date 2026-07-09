"""make meeting session date and duration optional

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "meeting_sessions",
        "session_date",
        existing_type=sa.Date(),
        nullable=True,
    )
    op.alter_column(
        "meeting_sessions",
        "duration_minutes",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "meeting_sessions",
        "duration_minutes",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "meeting_sessions",
        "session_date",
        existing_type=sa.Date(),
        nullable=False,
    )
