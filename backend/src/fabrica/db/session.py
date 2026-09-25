from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fabrica.config import get_settings
from fabrica.db.models import Base

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def configure(url: str | None = None) -> None:
    global _engine, _factory
    settings = get_settings()
    url = url or settings.db_url
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    _engine = create_async_engine(url, future=True)
    _factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    if not get_settings().auto_schema:
        return
    if _engine is None:
        configure()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _factory is None:
        configure()
    assert _factory is not None
    async with _factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose() -> None:
    if _engine is not None:
        await _engine.dispose()


async def reset_db() -> None:
    if _engine is None:
        configure()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
