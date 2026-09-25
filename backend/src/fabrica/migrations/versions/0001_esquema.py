from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("project", sa.String(length=80), nullable=False),
        sa.Column("capability", sa.String(length=40), nullable=True),
        sa.Column("ricefw", sa.String(length=1), nullable=True),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("spent_usd", sa.Double(), nullable=False),
        sa.Column("repo_url", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sap_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=True),
        sa.Column("system", sa.String(length=40), nullable=False),
        sa.Column("tool", sa.String(length=40), nullable=False),
        sa.Column("object_name", sa.String(length=60), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sap_calls", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sap_calls_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("author", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artifacts_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("activity", sa.String(length=40), nullable=False),
        sa.Column("tier", sa.String(length=4), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_attempts_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_decisions_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_documents_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("thread", sa.String(length=60), nullable=False),
        sa.Column("sender", sa.String(length=80), nullable=False),
        sa.Column("recipient", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_messages_requirement_id"), ["requirement_id"], unique=False
        )

    op.create_table(
        "transports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("system", sa.String(length=40), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("objects", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transports", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_transports_requirement_id"), ["requirement_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("transports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transports_requirement_id"))

    op.drop_table("transports")
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messages_requirement_id"))

    op.drop_table("messages")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documents_requirement_id"))

    op.drop_table("documents")
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_decisions_requirement_id"))

    op.drop_table("decisions")
    with op.batch_alter_table("attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_attempts_requirement_id"))

    op.drop_table("attempts")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_requirement_id"))

    op.drop_table("artifacts")
    with op.batch_alter_table("sap_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sap_calls_requirement_id"))

    op.drop_table("sap_calls")
    op.drop_table("requirements")
