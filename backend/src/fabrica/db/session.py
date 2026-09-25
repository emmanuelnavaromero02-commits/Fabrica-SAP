"""Motor y sesiones asíncronas (Postgres en producción, SQLite en local y pruebas)."""

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
    """Crea el motor. Se puede llamar con otra URL (p. ej. en pruebas)."""
    global _engine, _factory
    settings = get_settings()
    url = url or settings.db_url
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    _engine = create_async_engine(url, future=True)
    _factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    """Crea las tablas. En la beta reemplaza a las migraciones (Alembic queda pendiente)."""
    if _engine is None:
        configure()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sesión con commit al salir y rollback si hay error."""
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
    """Cierra las conexiones (al apagar el proceso o al terminar una prueba)."""
    if _engine is not None:
        await _engine.dispose()


async def reset_db() -> None:
    """Borra y recrea las tablas. Solo para pruebas."""
    if _engine is None:
        configure()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
