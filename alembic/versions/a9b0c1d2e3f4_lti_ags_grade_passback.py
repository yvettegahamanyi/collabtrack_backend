"""LTI AGS grade passback fields

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("moodle_user_id", sa.String(), nullable=True))
    op.create_index("ix_users_moodle_user_id", "users", ["moodle_user_id"], unique=False)

    op.add_column(
        "moodle_activity_links",
        sa.Column("ags_lineitem_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "moodle_activity_links",
        sa.Column("ags_lineitems_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "moodle_activity_links",
        sa.Column("ags_scopes", sa.JSON(), nullable=True),
    )
    op.add_column(
        "moodle_activity_links",
        sa.Column("ags_score_maximum", sa.Float(), nullable=True),
    )
    op.add_column(
        "moodle_activity_links",
        sa.Column("last_grade_sync_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("moodle_activity_links", "last_grade_sync_at")
    op.drop_column("moodle_activity_links", "ags_score_maximum")
    op.drop_column("moodle_activity_links", "ags_scopes")
    op.drop_column("moodle_activity_links", "ags_lineitems_url")
    op.drop_column("moodle_activity_links", "ags_lineitem_url")
    op.drop_index("ix_users_moodle_user_id", table_name="users")
    op.drop_column("users", "moodle_user_id")
