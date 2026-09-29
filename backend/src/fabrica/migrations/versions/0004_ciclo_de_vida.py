from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holidays",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_table(
        "contract_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("contract_reports", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_contract_reports_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "quality_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("object_name", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("quality_reports", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quality_reports_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "stage_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("clock", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("working_hours", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("stage_segments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_stage_segments_requirement_id"), ["requirement_id"], unique=False
        )

    with op.batch_alter_table("estimates", schema=None) as batch_op:
        batch_op.add_column(sa.Column("work_packages", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("contingency_pct", sa.Double(), server_default="15", nullable=False)
        )
        batch_op.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(
            sa.Column("author", sa.String(length=80), server_default="", nullable=False)
        )

    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("profile", sa.String(length=12), server_default="cloud", nullable=False)
        )
        batch_op.add_column(sa.Column("planned_start", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("planned_end", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("progress_override", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("hours_functional", sa.Double(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.drop_column("hours_functional")
        batch_op.drop_column("progress_override")
        batch_op.drop_column("planned_end")
        batch_op.drop_column("planned_start")
        batch_op.drop_column("profile")

    with op.batch_alter_table("estimates", schema=None) as batch_op:
        batch_op.drop_column("author")
        batch_op.drop_column("version")
        batch_op.drop_column("contingency_pct")
        batch_op.drop_column("work_packages")

    with op.batch_alter_table("stage_segments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_stage_segments_requirement_id"))

    op.drop_table("stage_segments")
    with op.batch_alter_table("quality_reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quality_reports_requirement_id"))

    op.drop_table("quality_reports")
    with op.batch_alter_table("contract_reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contract_reports_requirement_id"))

    op.drop_table("contract_reports")
    op.drop_table("holidays")
