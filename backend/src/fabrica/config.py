from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FABRICA_", env_file=(ROOT / ".env", ".env"), extra="ignore"
    )

    database_url: str = ""
    auto_schema: bool = True
    config_dir: Path = ROOT / "config"
    data_dir: Path = ROOT / "data"

    runner: Literal["inline", "temporal"] = "inline"
    temporal_host: str = "localhost:7233"
    temporal_queue: str = "fabrica"

    llm_mode: Literal["mock", "live"] = "mock"
    codex_bin: str = "codex"

    git_backend: Literal["local", "gitea"] = "local"
    gitea_url: str = "http://localhost:3000"
    gitea_token: str = ""
    gitea_org: str = "fabrica"

    sap_backend: Literal["simulated"] = "simulated"
    sap_allowed_packages: tuple[str, ...] = ("Z", "Y")

    cors_origins: tuple[str, ...] = ("http://localhost:5173",)

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'fabrica.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
