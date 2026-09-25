from __future__ import annotations

from functools import lru_cache

import yaml
from pydantic import BaseModel, Field

from fabrica.config import get_settings


class Standards(BaseModel):
    global_rules: list[str] = Field(default_factory=list, alias="global")
    capabilities: dict[str, list[str]] = Field(default_factory=dict)

    def for_capability(self, capability: str | None) -> list[str]:
        return [*self.global_rules, *self.capabilities.get((capability or "").upper(), [])]

    def render(self, capability: str | None) -> str:
        rules = self.for_capability(capability)
        if not rules:
            return ""
        items = "\n".join(f"- {rule}" for rule in rules)
        return f"\n\n## ESTÁNDARES DEL CLIENTE (OBLIGATORIOS)\n{items}\n"


@lru_cache
def standards() -> Standards:
    path = get_settings().config_dir / "estandares.yaml"
    if not path.exists():
        return Standards()
    with path.open(encoding="utf-8") as fh:
        return Standards.model_validate(yaml.safe_load(fh) or {})
