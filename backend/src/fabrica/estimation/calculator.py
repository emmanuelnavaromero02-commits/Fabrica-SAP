from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel

from fabrica.config import get_settings


class SizeHours(BaseModel):
    backend: float
    frontend: float
    pruebas: float

    @property
    def total(self) -> float:
        return self.backend + self.frontend + self.pruebas


class ComplexityBand(BaseModel):
    up_to: float
    label: str


class EstimationTable(BaseModel):
    sizes: dict[str, SizeHours]
    fixed_hours: dict[str, float]
    contingency: float
    hours_per_day: float
    complexity: list[ComplexityBand]


class EstimateResult(BaseModel):
    items: list[dict[str, Any]]
    breakdown: dict[str, float]
    hours_base: float
    hours_total: float
    days: float
    complexity: str


@lru_cache
def estimation_table() -> EstimationTable:
    path = get_settings().config_dir / "estimacion.yaml"
    with path.open(encoding="utf-8") as fh:
        return EstimationTable.model_validate(yaml.safe_load(fh))


def compute(items: list[dict[str, Any]], table: EstimationTable | None = None) -> EstimateResult:
    table = table or estimation_table()
    breakdown = {"backend": 0.0, "frontend": 0.0, "pruebas": 0.0}
    priced = []
    for item in items:
        hours = table.sizes[item["size"]]
        breakdown["backend"] += hours.backend
        breakdown["frontend"] += hours.frontend
        breakdown["pruebas"] += hours.pruebas
        priced.append({**item, "hours": hours.total})
    breakdown.update(table.fixed_hours)
    base = round(sum(breakdown.values()), 2)
    total = round(base * (1 + table.contingency), 2)
    label = next(band.label for band in table.complexity if total <= band.up_to)
    return EstimateResult(
        items=priced,
        breakdown=breakdown,
        hours_base=base,
        hours_total=total,
        days=round(total / table.hours_per_day, 1),
        complexity=label,
    )
