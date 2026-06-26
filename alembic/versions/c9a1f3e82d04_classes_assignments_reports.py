"""classes assignments reports

Revision ID: c9a1f3e82d04
Revises: b2e4f7a91c03
Create Date: 2026-06-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c9a1f3e82d04"
down_revision: Union[str, Sequence[str], None] = "b2e4f7a91c03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

report_status = postgresql.ENUM(
    "DRAFT",
    "PROCESSING",
    "READY",
    "FAILED",
    name="reportstatus",
    create_type=False,
)
contribution_report_status = postgresql.ENUM(
    "PENDING",
    "READY",
    "FAILED",
    name="contributionreportstatus",
    create_type=False,
)


def upgrade() -> None:
    report_status.create(op.get_bind(), checkfirst=True)
    contribution_report_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "classes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("instructor_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["instructor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "assignments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("supervisor_email", sa.String(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("ACTIVE", "DONE", name="servicetype", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "project_groups",
        sa.Column("assignment_id", sa.String(), nullable=True),
    )
    op.add_column(
        "project_groups",
        sa.Column("group_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "project_groups",
        sa.Column("report_status", report_status, nullable=True),
    )
    op.create_foreign_key(
        "fk_project_groups_assignment_id",
        "project_groups",
        "assignments",
        ["assignment_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_assignment_group_number",
        "project_groups",
        ["assignment_id", "group_number"],
    )

    op.add_column(
        "contribution_reports",
        sa.Column("assignment_id", sa.String(), nullable=True),
    )
    op.add_column(
        "contribution_reports",
        sa.Column("status", contribution_report_status, nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "contribution_reports",
        sa.Column("notification_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_contribution_reports_assignment_id",
        "contribution_reports",
        "assignments",
        ["assignment_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_contribution_reports_assignment_id", "contribution_reports", type_="foreignkey"
    )
    op.drop_column("contribution_reports", "notification_sent_at")
    op.drop_column("contribution_reports", "status")
    op.drop_column("contribution_reports", "assignment_id")

    op.drop_constraint("uq_assignment_group_number", "project_groups", type_="unique")
    op.drop_constraint(
        "fk_project_groups_assignment_id", "project_groups", type_="foreignkey"
    )
    op.drop_column("project_groups", "report_status")
    op.drop_column("project_groups", "group_number")
    op.drop_column("project_groups", "assignment_id")

    op.drop_table("assignments")
    op.drop_table("classes")

    contribution_report_status.drop(op.get_bind(), checkfirst=True)
    report_status.drop(op.get_bind(), checkfirst=True)
