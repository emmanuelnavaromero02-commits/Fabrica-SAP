from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from fabrica.config import get_settings


class SapSystem(BaseModel):
    name: str = ""
    kind: Literal["adt", "mock"] = "adt"
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


class SapNotConfigured(LookupError): ...


class SapLandscape(BaseModel):
    default: str | None = None
    systems: dict[str, SapSystem] = Field(default_factory=dict)
    projects: dict[str, str] = Field(default_factory=dict)

    def for_project(self, project: str) -> SapSystem:
        name = self.projects.get(project, self.default)
        if name is None or name not in self.systems:
            raise SapNotConfigured(f"El proyecto {project} no tiene un sistema SAP DEV configurado")
        return self.systems[name].model_copy(update={"name": name})


@lru_cache
def sap_landscape() -> SapLandscape:
    path = get_settings().config_dir / "sap_systems.yaml"
    with path.open(encoding="utf-8") as fh:
        return SapLandscape.model_validate(yaml.safe_load(fh) or {})
