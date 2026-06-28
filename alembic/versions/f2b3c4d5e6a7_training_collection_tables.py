"""training collection tables

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-06-26 10:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, Sequence[str], None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

training_collection_status = postgresql.ENUM(
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    name="trainingcollectionstatus",
    create_type=False,
)


def upgrade() -> None:
    training_collection_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "training_collections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_group_id", sa.String(), nullable=False),
        sa.Column("dataset_group_id", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            training_collection_status,
            nullable=False,
            server_default="PROCESSING",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_group_id"], ["project_groups.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_training_collections_dataset_group_id"),
        "training_collections",
        ["dataset_group_id"],
        unique=False,
    )

    op.create_table(
        "training_collection_members",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("collection_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("dataset_student_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("github_email", sa.String(), nullable=True),
        sa.Column("google_docs_email", sa.String(), nullable=True),
        sa.Column("google_meet_email", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["collection_id"], ["training_collections.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id", "dataset_student_id", name="uq_training_member_student_id"
        ),
        sa.UniqueConstraint(
            "collection_id", "user_id", name="uq_training_member_user"
        ),
    )


def downgrade() -> None:
    op.drop_table("training_collection_members")
    op.drop_index(
        op.f("ix_training_collections_dataset_group_id"),
        table_name="training_collections",
    )
    op.drop_table("training_collections")
    training_collection_status.drop(op.get_bind(), checkfirst=True)
