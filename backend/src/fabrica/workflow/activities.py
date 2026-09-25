from __future__ import annotations

import asyncio
import contextlib

from temporalio import activity

from fabrica.blackboard.service import Board
from fabrica.db.models import RunState
from fabrica.db.session import session_scope
from fabrica.pipeline.engine import Engine

HEARTBEAT_EVERY = 30.0


async def _beat_forever() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(HEARTBEAT_EVERY)


@activity.defn
async def drive_activity(req_id: int) -> bool:
    beating = asyncio.create_task(_beat_forever())
    try:
        await Engine().drive(req_id)
    finally:
        beating.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beating
    async with session_scope() as session:
        req = await Board(session).requirement(req_id)
        return req.state == RunState.DONE
