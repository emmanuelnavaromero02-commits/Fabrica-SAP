"""Motor de etapas: encadena etapas automáticas hasta una compuerta, un bloqueo o el fin.

Lo usan igual el modo inline (API en local) y las actividades de Temporal.
Cada etapa corre en su propia transacción para que la UI vea el avance.
"""

from __future__ import annotations

import logging

from fabrica.blackboard.service import Board
from fabrica.catalog import StageKind, stage_machine
from fabrica.db.models import MessageKind, RunState
from fabrica.db.session import session_scope
from fabrica.domain.stages import advance
from fabrica.git.repo import RepoStore, repo_store
from fabrica.llm.gateway import ModelGateway
from fabrica.pipeline.steps import StepResult, Steps

log = logging.getLogger(__name__)

_MAX_STAGES_PER_RUN = 10


class Engine:
    def __init__(self, gateway: ModelGateway | None = None, repos: RepoStore | None = None) -> None:
        self.gateway = gateway or ModelGateway()
        self.repos = repos or repo_store()

    async def run_stage(self, req_id: int) -> bool:
        """Ejecuta la etapa automática actual. Devuelve True si debe seguir con la próxima."""
        async with session_scope() as session:
            board = Board(session)
            req = await board.requirement(req_id)
            stage = stage_machine().get(req.stage)
            if stage.kind is not StageKind.AUTO or req.state != RunState.RUNNING:
                return False

            handler = getattr(Steps(board, self.gateway, self.repos), req.stage)
            try:
                result: StepResult = await handler(req)
            except Exception as exc:  # un fallo inesperado nunca debe perder el requisito
                log.exception("Etapa %s del requisito %s falló", req.stage, req_id)
                result = StepResult("blocked", f"Error interno: {exc}")

            if result.outcome == "advance":
                nxt = advance(req.stage)
                await board.post(
                    req_id,
                    thread="flujo",
                    sender="motor",
                    kind=MessageKind.INFO,
                    body=f"{stage.label} completada → {stage_machine().get(nxt.stage).label}",
                )
                req.stage, req.state = nxt.stage, nxt.state
                return nxt.state == RunState.RUNNING

            req.state = RunState.BLOCKED
            await board.post(
                req_id,
                thread="flujo",
                sender="motor",
                kind=MessageKind.ESCALAMIENTO,
                recipient="persona",
                body=f"{stage.label} en pausa: {result.note}",
            )
            return False

    async def drive(self, req_id: int) -> None:
        """Avanza el requisito mientras haya trabajo automático."""
        for _ in range(_MAX_STAGES_PER_RUN):
            if not await self.run_stage(req_id):
                return
