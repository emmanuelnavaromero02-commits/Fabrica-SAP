from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import Integer, cast, func, select

from fabrica.api.deps import Who
from fabrica.catalog import model_catalog, stage_machine
from fabrica.config import get_settings
from fabrica.db.models import Attempt
from fabrica.db.session import session_scope
from fabrica.domain.schemas import AuthConfigOut, Identity, StageOut

router = APIRouter(prefix="/api", tags=["catálogo"])


@router.get("/me", response_model=Identity)
async def me(who: Who) -> Identity:
    return who


@router.get("/auth/config", response_model=AuthConfigOut)
async def auth_config() -> AuthConfigOut:
    settings = get_settings()
    return AuthConfigOut(
        mode=settings.auth_mode, issuer=settings.oidc_issuer, client_id=settings.oidc_client_id
    )


@router.get("/stages", response_model=list[StageOut])
async def stages() -> list[StageOut]:
    return [
        StageOut(key=s.key, label=s.label, kind=s.kind.value, roles=s.roles)
        for s in stage_machine().stages
    ]


@router.get("/tiers")
async def tiers() -> dict[str, Any]:
    catalog = model_catalog()
    return {
        "tiers": {k: t.model_dump() for k, t in catalog.tiers.items()},
        "activities": {k: a.model_dump() for k, a in catalog.activities.items()},
        "budget_usd_per_requirement": catalog.budget_usd_per_requirement,
    }


@router.get("/usage")
async def usage(who: Who) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = await s.execute(
            select(
                Attempt.activity,
                Attempt.tier,
                Attempt.provider,
                func.count(),
                func.sum(cast(Attempt.passed, Integer)),
                func.sum(Attempt.cost_usd),
            ).group_by(Attempt.activity, Attempt.tier, Attempt.provider)
        )
        return [
            {
                "activity": a,
                "tier": t,
                "provider": p,
                "attempts": n,
                "passed": int(ok or 0),
                "cost_usd": round(float(c or 0), 6),
            }
            for a, t, p, n, ok, c in rows.all()
        ]
