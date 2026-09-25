"""Carga tipada de config/models.yaml y config/stages.yaml."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from fabrica.config import get_settings

Provider = Literal["anthropic", "openai", "codex", "mock"]
TIER_ORDER = ("N1", "N2", "N3", "N4")


class ModelSpec(BaseModel):
    provider: Provider
    model: str
    effort: str | None = None
    price_in: float = 0.0
    price_out: float = 0.0
    fallbacks: bool = False

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1_000_000


class Tier(BaseModel):
    description: str
    models: list[ModelSpec]


class ActivityPolicy(BaseModel):
    start: str
    max_tier: str
    attempts: int = 1
    cross_vendor: bool = False

    def tiers(self) -> list[str]:
        lo, hi = TIER_ORDER.index(self.start), TIER_ORDER.index(self.max_tier)
        return list(TIER_ORDER[lo : hi + 1])


class ModelCatalog(BaseModel):
    tiers: dict[str, Tier]
    activities: dict[str, ActivityPolicy]
    budget_usd_per_requirement: float = 25.0

    def policy(self, activity: str) -> ActivityPolicy:
        return self.activities[activity]


class StageKind(StrEnum):
    AUTO = "auto"
    GATE = "gate"
    FINAL = "final"


class Stage(BaseModel):
    key: str
    label: str
    kind: StageKind
    next: str | None = None
    on_reject: str | None = None
    roles: list[str] = Field(default_factory=list)


class StageMachine(BaseModel):
    stages: list[Stage]
    discard_roles: list[str]

    def get(self, key: str) -> Stage:
        for stage in self.stages:
            if stage.key == key:
                return stage
        raise KeyError(f"Etapa desconocida: {key}")

    @property
    def first(self) -> Stage:
        return self.stages[0]


def _load(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, object] = yaml.safe_load(fh)
    return data


@lru_cache
def model_catalog() -> ModelCatalog:
    return ModelCatalog.model_validate(_load(get_settings().config_dir / "models.yaml"))


@lru_cache
def stage_machine() -> StageMachine:
    return StageMachine.model_validate(_load(get_settings().config_dir / "stages.yaml"))
