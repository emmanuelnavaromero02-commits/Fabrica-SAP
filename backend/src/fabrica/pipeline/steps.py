from __future__ import annotations

import json

from fabrica.agents import roles
from fabrica.blackboard.service import Board
from fabrica.db.models import MessageKind, Requirement
from fabrica.escalation.router import EscalationRouter
from fabrica.git.repo import RepoStore
from fabrica.llm.gateway import ModelGateway
from fabrica.pipeline.build_step import run_build
from fabrica.pipeline.design_step import run_design
from fabrica.pipeline.step_support import (
    SPEC_JSON,
    IssueCheck,
    StepResult,
    StepTools,
    request_for,
    requirement_context,
)

__all__ = ["SPEC_JSON", "StepResult", "Steps"]


class Steps:
    def __init__(self, board: Board, gateway: ModelGateway, repos: RepoStore) -> None:
        self.board = board
        self.repos = repos
        self.router = EscalationRouter(gateway, board)
        self.tools = StepTools(board, self.router, repos, self._save)

    async def _save(
        self, req: Requirement, files: dict[str, str], message: str, author: str
    ) -> None:
        commit = await self.repos.write_files(req.id, files, message, author)
        for path in files:
            await self.board.record_artifact(req.id, path, commit, author)

    async def recepcion(self, req: Requirement) -> StepResult:
        req.repo_url = await self.repos.ensure_repo(req.id, req.title)
        context = await requirement_context(self.board, req)

        if not req.capability:
            valid = {"R", "I", "C", "E", "F", "W"}
            out = await self.router.run(
                req.id,
                "clasificar",
                request_for(roles.CLASIFICADOR, context),
                IssueCheck(
                    lambda o: (
                        []
                        if o.get("capability") and o.get("ricefw") in valid
                        else ["Clasificación incompleta"]
                    )
                ),
                agent=roles.CLASIFICADOR.name,
            )
            if out.passed and out.output:
                req.capability, req.ricefw = out.output["capability"], out.output["ricefw"]

        out = await self.router.run(
            req.id,
            "analizar",
            request_for(roles.ANALISTA, await requirement_context(self.board, req)),
            IssueCheck(lambda o: [] if o.get("summary") else ["Análisis vacío"]),
            agent=roles.ANALISTA.name,
        )
        if not out.passed or out.output is None:
            return StepResult("blocked", "El análisis requiere una persona")

        questions: list[str] = out.output.get("questions", [])
        for q in questions:
            await self.board.post(
                req.id,
                thread="analizar",
                sender=roles.ANALISTA.name,
                recipient="cliente",
                kind=MessageKind.PREGUNTA,
                body=q,
            )
        if questions:
            return StepResult("blocked", f"{len(questions)} pregunta(s) al cliente")

        inputs = {f"inputs/{d.name}.md": d.content for d in req.documents}
        inputs["inputs/analisis.json"] = json.dumps(out.output, ensure_ascii=False, indent=2)
        await self._save(req, inputs, "Recepción: insumos y análisis", roles.ANALISTA.name)
        return StepResult("advance")

    async def diseno(self, req: Requirement) -> StepResult:
        return await run_design(self.tools, req)

    async def construccion(self, req: Requirement) -> StepResult:
        return await run_build(self.tools, req)
