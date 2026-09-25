from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect

from fabrica.db.migrate import alembic_config
from fabrica.db.models import Base


def test_migrations_build_the_current_schema(tmp_path: Path) -> None:
    db = tmp_path / "migrado.db"
    config = alembic_config(f"sqlite+aiosqlite:///{db}")
    command.upgrade(config, "head")
    tables = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert set(Base.metadata.tables) <= tables
    command.check(config)
