from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from fabrica.config import get_settings


class SapSystem(BaseModel):
    name: str = ""
    kind: Literal["simulated", "adt"]
    url: str = ""
    client: str = ""
    user_env: str = ""
    password_env: str = ""
    atc_variant: str = "DEFAULT"
    language: str = "ES"
    verify_tls: bool = True

    def credentials(self) -> tuple[str, str]:
        user = os.environ.get(self.user_env, "")
        password = os.environ.get(self.password_env, "")
        if not user or not password:
            raise RuntimeError(f"Faltan credenciales para {self.name} ({self.user_env})")
        return user, password


class SapLandscape(BaseModel):
    default: str
    systems: dict[str, SapSystem]
    projects: dict[str, str] = Field(default_factory=dict)

    def for_project(self, project: str) -> SapSystem:
        name = self.projects.get(project, self.default)
        system = self.systems[name]
        return system.model_copy(update={"name": name})


@lru_cache
def sap_landscape() -> SapLandscape:
    path = get_settings().config_dir / "sap_systems.yaml"
    with path.open(encoding="utf-8") as fh:
        return SapLandscape.model_validate(yaml.safe_load(fh))
