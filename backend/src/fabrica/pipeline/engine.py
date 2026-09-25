from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, or_, update

from fabrica.blackboard.service import Board
from fabrica.catalog import StageKind, stage_machine
from fabrica.db.models import MessageKind, Requirement, RunState, now
from fabrica.db.session import session_scope
from fabrica.domain.stages import advance
from fabrica.git.repo import RepoStore, repo_store
from fabrica.llm.gateway import ModelGateway
from fabrica.notify.notifier import announce
from fabrica.pipeline.steps import StepResult, Steps

log = logging.getLogger(__name__)

_MAX_STAGES_PER_RUN = 10
LEASE = timedelta(hours=6)


def _still_running(req_id: int, stage: str) -> Any:
    return (
        update(Requirement)
        .where(
            Requirement.id == req_id,
            Requirement.stage == stage,
            Requirement.state == RunState.RUNNING,
        )
        .execution_options(synchronize_session=False)
    )


class Engine:
    def __init__(self, gateway: ModelGateway | None = None, repos: RepoStore | None = None) -> None:
        self.gateway = gateway or ModelGateway()
        self.repos = repos or repo_store()

    async def _claim(self, req_id: int) -> str | None:
        async with session_scope() as session:
            req = await Board(session).requirement(req_id)
            if stage_machine().get(req.stage).kind is not StageKind.AUTO:
                return None
            if req.state != RunState.RUNNING:
                return None
            moment = now()
            claimed = cast(
                CursorResult[Any],
                await session.execute(
                    _still_running(req_id, req.stage)
                    .where(or_(Requirement.lease_until.is_(None), Requirement.lease_until < moment))
                    .values(lease_until=moment + LEASE)
                ),
            )
            return req.stage if claimed.rowcount == 1 else None

    async def _work(self, req_id: int) -> StepResult:
        try:
            async with session_scope() as session:
                board = Board(session)
                req = await board.requirement(req_id)
                handler = getattr(Steps(board, self.gateway, self.repos), req.stage)
                result: StepResult = await handler(req)
                return result
        except Exception as exc:
            log.exception("Etapa del requisito %s falló", req_id)
            return StepResult("blocked", f"Error interno: {type(exc).__name__}: {exc}")

    async def _finish(self, req_id: int, stage_key: str, result: StepResult) -> bool:
        label = stage_machine().get(stage_key).label
        async with session_scope() as session:
            board = Board(session)
            if result.outcome == "advance":
                nxt = advance(stage_key)
                values: dict[str, Any] = {"stage": nxt.stage, "state": nxt.state}
            else:
                values = {"state": RunState.BLOCKED}
            applied = cast(
                CursorResult[Any],
                await session.execute(
                    _still_running(req_id, stage_key).values(**values, lease_until=None)
                ),
            )
            if applied.rowcount != 1:
                await session.execute(
                    update(Requirement).where(Requirement.id == req_id).values(lease_until=None)
                )
                await board.post(
                    req_id,
                    thread="flujo",
                    sender="motor",
                    kind=MessageKind.INFO,
                    body=f"Resultado de {label} descartado: el requisito cambió mientras "
                    "trabajaban los agentes",
                )
                return False
            if result.outcome == "advance":
                await board.post(
                    req_id,
                    thread="flujo",
                    sender="motor",
                    kind=MessageKind.INFO,
                    body=f"{label} completada → {stage_machine().get(nxt.stage).label}",
                )
                return nxt.state == RunState.RUNNING
            await board.post(
                req_id,
                thread="flujo",
                sender="motor",
                kind=MessageKind.ESCALAMIENTO,
                recipient="persona",
                body=f"{label} en pausa: {result.note}",
            )
            return False

    async def run_stage(self, req_id: int) -> bool:
        stage_key = await self._claim(req_id)
        if stage_key is None:
            return False
        result = await self._work(req_id)
        return await self._finish(req_id, stage_key, result)

    async def drive(self, req_id: int) -> None:
        for _ in range(_MAX_STAGES_PER_RUN):
            if not await self.run_stage(req_id):
                break
        await announce(req_id)
