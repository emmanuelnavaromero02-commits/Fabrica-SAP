"""Actividades de Temporal: envuelven el mismo motor que usa el modo inline."""

from __future__ import annotations

from temporalio import activity

from fabrica.blackboard.service import Board
from fabrica.db.models import RunState
from fabrica.db.session import session_scope
from fabrica.pipeline.engine import Engine


@activity.defn
async def drive_activity(req_id: int) -> bool:
    """Avanza el requisito. Devuelve True si ya terminó (cerrado o desestimado)."""
    await Engine().drive(req_id)
    async with session_scope() as session:
        req = await Board(session).requirement(req_id)
        return req.state == RunState.DONE
