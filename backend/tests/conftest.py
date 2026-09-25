"""Cada prueba usa su propia base SQLite, repos y SAP simulado en un directorio temporal."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fabrica import catalog, config
from fabrica.db import session as db


@pytest.fixture(autouse=True)
async def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    monkeypatch.setenv("FABRICA_DATA_DIR", str(tmp_path))
    # FABRICA_TEST_DATABASE_URL permite correr las pruebas contra Postgres.
    url = os.environ.get("FABRICA_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("FABRICA_DATABASE_URL", url)
    monkeypatch.setenv("FABRICA_LLM_MODE", "mock")
    monkeypatch.setenv("FABRICA_RUNNER", "inline")
    monkeypatch.setenv("FABRICA_GIT_BACKEND", "local")
    config.get_settings.cache_clear()
    catalog.model_catalog.cache_clear()
    catalog.stage_machine.cache_clear()
    db.configure()
    await db.reset_db()
    yield tmp_path
    await db.dispose()
    config.get_settings.cache_clear()
