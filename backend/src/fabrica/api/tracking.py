from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fabrica.api.deps import Who
from fabrica.db.session import session_scope
from fabrica.tracking.portfolio import portfolio
from fabrica.tracking.time import heartbeat, hours_by_user_and_requirement

router = APIRouter(prefix="/api", tags=["seguimiento"])

SUPERVISORS = {"admin", "lider"}


class HeartbeatIn(BaseModel):
    requirement_id: int | None = None


@router.post("/time/heartbeat", status_code=204)
async def time_heartbeat(data: HeartbeatIn, who: Who) -> None:
    async with session_scope() as s:
        await heartbeat(s, user=who.user, requirement_id=data.requirement_id, source="web")


@router.get("/time")
async def time_report(
    who: Who,
    user: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    if who.role not in SUPERVISORS:
        if user and user != who.user:
            raise HTTPException(403, "Solo líderes y administradores ven el tiempo de otros")
        user = who.user
    async with session_scope() as s:
        return await hours_by_user_and_requirement(s, user=user, since=since, until=until)


@router.get("/portfolio")
async def portfolio_report(who: Who) -> dict[str, Any]:
    if who.role not in SUPERVISORS:
        raise HTTPException(403, "El portafolio es para líderes y administradores")
    async with session_scope() as s:
        return await portfolio(s)
