from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel

from fabrica.config import get_settings

SIZE_RANK: dict[str, int] = {
    "XS": 1,
    "TINY": 1,
    "S": 2,
    "SMALL": 2,
    "M": 3,
    "MEDIUM": 3,
    "L": 4,
    "LARGE": 4,
    "XL": 5,
    "VERY LARGE": 5,
    "VERY_LARGE": 5,
}

RANK_TO_SIZE: dict[int, str] = {
    1: "XS",
    2: "S",
    3: "M",
    4: "L",
    5: "XL",
}


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


def highest_rank_size(sizes: list[str]) -> str:
    if not sizes:
        return "XS"
    highest = max((SIZE_RANK.get(s.strip().upper(), 1) for s in sizes), default=1)
    return RANK_TO_SIZE.get(highest, "XS")


def compute(
    items: list[dict[str, Any]],
    table: EstimationTable | None = None,
    integrations_count: int = 0,
    integration_unit_hours: float = 8.0,
) -> EstimateResult:
    table = table or estimation_table()
    breakdown = {"backend": 0.0, "frontend": 0.0, "pruebas": 0.0}
    priced = []
    for item in items:
        raw_size = item.get("size")
        if not raw_size and item.get("axes"):
            raw_size = highest_rank_size(list(item["axes"]))
        size_key = RANK_TO_SIZE.get(
            SIZE_RANK.get(str(raw_size).strip().upper(), 1), str(raw_size).upper()
        )
        if size_key not in table.sizes:
            size_key = "M"
        hours = table.sizes[size_key]
        breakdown["backend"] += hours.backend
        breakdown["frontend"] += hours.frontend
        breakdown["pruebas"] += hours.pruebas
        priced.append({**item, "size": size_key, "hours": hours.total})
    if integrations_count > 0:
        breakdown["integraciones"] = round(integrations_count * integration_unit_hours, 2)
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


def generate_work_package_breakdown(
    parent_subject: str,
    parent_id: int,
    tech_stack: str,
    estimate: EstimateResult,
    *,
    has_persistence: bool = True,
    has_business_logic: bool = True,
    has_custom_auth: bool = False,
    integrations_count: int = 0,
    has_frontend: bool = True,
) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = [
        {
            "id": "test_plan",
            "type": "Test Plan",
            "subject": f"Plan de pruebas: {parent_subject}",
            "parent_id": parent_id,
            "estimated_hours": 6.0,
        },
        {
            "id": "dev_iteration",
            "type": "Development Iteration",
            "subject": f"Iteración 1: {parent_subject}",
            "parent_id": parent_id,
            "estimated_hours": 0.0,
        },
    ]
    if has_persistence:
        packages.append(
            {
                "id": "backend_data_model",
                "type": "Backend Task",
                "subject": "Modelo de datos: tablas Z + CDS views",
                "parent": "dev_iteration",
                "parent_id": parent_id,
                "estimated_hours": min(
                    16.0, max(6.0, estimate.breakdown.get("backend", 10.0) * 0.3)
                ),
            }
        )
    if has_business_logic:
        packages.append(
            {
                "id": "backend_business_object",
                "type": "Backend Task",
                "subject": "RAP Business Object + Behavior",
                "parent": "dev_iteration",
                "parent_id": parent_id,
                "estimated_hours": min(
                    32.0, max(12.0, estimate.breakdown.get("backend", 16.0) * 0.5)
                ),
            }
        )
    if has_custom_auth:
        packages.append(
            {
                "id": "backend_authorization",
                "type": "Backend Task",
                "subject": "Autorización: roles PFCG y authorization-as-code",
                "parent": "dev_iteration",
                "parent_id": parent_id,
                "estimated_hours": 6.0,
            }
        )
    if integrations_count > 0:
        packages.append(
            {
                "id": "backend_integration",
                "type": "Backend Task",
                "subject": "Integraciones: BAPIs / IDocs / OData externo",
                "parent": "dev_iteration",
                "parent_id": parent_id,
                "estimated_hours": estimate.breakdown.get("integraciones", 8.0),
            }
        )
    if has_frontend:
        packages.append(
            {
                "id": "ui_task",
                "type": "UI Task",
                "subject": "UI: List Report / Object Page SAP Fiori",
                "parent": "dev_iteration",
                "parent_id": parent_id,
                "estimated_hours": estimate.breakdown.get("frontend", 16.0),
            }
        )
    packages.extend(
        [
            {
                "id": "qa_technical",
                "type": "Technical QA",
                "subject": f"Revisión técnica ATC y Clean Core: {parent_subject}",
                "parent_id": parent_id,
                "estimated_hours": 4.0,
            },
            {
                "id": "qa_functional",
                "type": "Functional Testing",
                "subject": f"Pruebas funcionales: {parent_subject}",
                "parent_id": parent_id,
                "estimated_hours": estimate.breakdown.get("pruebas_funcionales", 8.0),
            },
            {
                "id": "uat",
                "type": "UAT",
                "subject": f"Acompañamiento UAT: {parent_subject}",
                "parent_id": parent_id,
                "estimated_hours": estimate.breakdown.get("acompanamiento_uat", 8.0),
            },
            {
                "id": "deployment",
                "type": "Deployment",
                "subject": f"Despliegue y pase a producción: {parent_subject}",
                "parent_id": parent_id,
                "estimated_hours": estimate.breakdown.get("despliegue", 4.0),
            },
        ]
    )
    return packages
