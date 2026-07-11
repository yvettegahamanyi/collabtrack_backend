"""moodle lti links

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-11 08:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("moodle_lti_sub", sa.String(), nullable=True),
    )
    op.create_index(
        op.f("ix_users_moodle_lti_sub"),
        "users",
        ["moodle_lti_sub"],
        unique=True,
    )

    op.create_table(
        "moodle_course_links",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("moodle_issuer", sa.String(), nullable=False),
        sa.Column("moodle_course_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("class_id"),
        sa.UniqueConstraint(
            "moodle_issuer",
            "moodle_course_id",
            name="uq_moodle_course_issuer",
        ),
    )
    op.create_index(
        op.f("ix_moodle_course_links_moodle_course_id"),
        "moodle_course_links",
        ["moodle_course_id"],
        unique=False,
    )

    op.create_table(
        "moodle_activity_links",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("assignment_id", sa.String(), nullable=False),
        sa.Column("moodle_issuer", sa.String(), nullable=False),
        sa.Column("moodle_course_id", sa.String(), nullable=False),
        sa.Column("moodle_resource_link_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id"),
        sa.UniqueConstraint(
            "moodle_issuer",
            "moodle_resource_link_id",
            name="uq_moodle_activity_issuer",
        ),
    )
    op.create_index(
        op.f("ix_moodle_activity_links_moodle_course_id"),
        "moodle_activity_links",
        ["moodle_course_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_moodle_activity_links_moodle_resource_link_id"),
        "moodle_activity_links",
        ["moodle_resource_link_id"],
        unique=False,
    )

    op.create_table(
        "moodle_group_links",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("moodle_issuer", sa.String(), nullable=False),
        sa.Column("moodle_course_id", sa.String(), nullable=False),
        sa.Column("moodle_group_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["group_id"], ["project_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id"),
        sa.UniqueConstraint(
            "moodle_issuer",
            "moodle_course_id",
            "moodle_group_id",
            name="uq_moodle_group_issuer_course",
        ),
    )
    op.create_index(
        op.f("ix_moodle_group_links_moodle_course_id"),
        "moodle_group_links",
        ["moodle_course_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_moodle_group_links_moodle_group_id"),
        "moodle_group_links",
        ["moodle_group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_moodle_group_links_moodle_group_id"),
        table_name="moodle_group_links",
    )
    op.drop_index(
        op.f("ix_moodle_group_links_moodle_course_id"),
        table_name="moodle_group_links",
    )
    op.drop_table("moodle_group_links")

    op.drop_index(
        op.f("ix_moodle_activity_links_moodle_resource_link_id"),
        table_name="moodle_activity_links",
    )
    op.drop_index(
        op.f("ix_moodle_activity_links_moodle_course_id"),
        table_name="moodle_activity_links",
    )
    op.drop_table("moodle_activity_links")

    op.drop_index(
        op.f("ix_moodle_course_links_moodle_course_id"),
        table_name="moodle_course_links",
    )
    op.drop_table("moodle_course_links")

    op.drop_index(op.f("ix_users_moodle_lti_sub"), table_name="users")
    op.drop_column("users", "moodle_lti_sub")
