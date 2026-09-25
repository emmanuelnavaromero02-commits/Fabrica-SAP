from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from fabrica.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url or get_settings().db_url)
    return config


def upgrade(url: str | None = None, target: str = "head") -> None:
    command.upgrade(alembic_config(url), target)


def run() -> None:
    parser = argparse.ArgumentParser(prog="fabrica-migrate")
    sub = parser.add_subparsers(dest="action")
    up = sub.add_parser("upgrade")
    up.add_argument("target", nargs="?", default="head")
    new = sub.add_parser("revision")
    new.add_argument("message")
    sub.add_parser("current")
    args = parser.parse_args()

    config = alembic_config()
    if args.action == "revision":
        command.revision(config, message=args.message, autogenerate=True)
    elif args.action == "current":
        command.current(config)
    else:
        command.upgrade(config, getattr(args, "target", "head"))
