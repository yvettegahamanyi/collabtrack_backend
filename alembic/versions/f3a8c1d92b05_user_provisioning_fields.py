"""user provisioning fields for instructor roster flow

Revision ID: f3a8c1d92b05
Revises: d7f3a1b92e04
Create Date: 2026-06-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3a8c1d92b05"
down_revision: Union[str, Sequence[str], None] = "d7f3a1b92e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_status_enum = sa.Enum("ACTIVE", "PENDING", name="accountstatus")


def upgrade() -> None:
    """Upgrade schema."""
    account_status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column(
            "account_status",
            account_status_enum,
            server_default="ACTIVE",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "has_logged_in",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("provisioned_by_instructor_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_provisioned_by_instructor",
        "users",
        "users",
        ["provisioned_by_instructor_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_users_provisioned_by_instructor", "users", type_="foreignkey"
    )
    op.drop_column("users", "provisioned_by_instructor_id")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "has_logged_in")
    op.drop_column("users", "account_status")
    account_status_enum.drop(op.get_bind(), checkfirst=True)
