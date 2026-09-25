from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("holder_role", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("holder_user", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("holder_since", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("sap_system", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_projects_client_id"), ["client_id"], unique=False)

    op.create_table(
        "capabilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("capabilities", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_capabilities_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "billing_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("hours_total", sa.Double(), nullable=False),
        sa.Column("hours_billed", sa.Double(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("billing_snapshots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_billing_snapshots_project"), ["project"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("billing_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_billing_snapshots_project"))
    op.drop_table("billing_snapshots")

    with op.batch_alter_table("capabilities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_capabilities_project_id"))
    op.drop_table("capabilities")

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_projects_client_id"))
    op.drop_table("projects")

    op.drop_table("clients")

    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.drop_column("holder_since")
        batch_op.drop_column("holder_user")
        batch_op.drop_column("holder_role")
