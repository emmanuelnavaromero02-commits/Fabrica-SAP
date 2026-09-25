from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.String(length=20), server_default="media", nullable=False)
        )
        batch_op.add_column(sa.Column("due_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.drop_column("due_date")
        batch_op.drop_column("priority")
