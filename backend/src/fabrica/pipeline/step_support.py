from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fabrica.agents.roles import AgentRole
from fabrica.blackboard.service import Board
from fabrica.db.models import Requirement
from fabrica.escalation.router import EscalationRouter
from fabrica.git.repo import RepoStore
from fabrica.llm.base import LLMRequest
from fabrica.verifiers.base import Verification

SPEC_JSON = "diseno/spec.json"

SaveFn = Callable[[Requirement, dict[str, str], str, str], Awaitable[None]]


@dataclass
class StepResult:
    outcome: Literal["advance", "blocked"]
    note: str = ""


@dataclass
class StepTools:
    board: Board
    router: EscalationRouter
    repos: RepoStore
    save: SaveFn


class IssueCheck:
    def __init__(self, fn: Callable[[dict[str, Any]], list[str]]) -> None:
        self.fn = fn

    async def verify(self, output: dict[str, Any]) -> Verification:
        return Verification.from_issues(self.fn(output))


def request_for(role: AgentRole, prompt: str, workdir: str | None = None) -> LLMRequest:
    return LLMRequest(system=role.system, prompt=prompt, schema=role.schema, workdir=workdir)


async def requirement_context(board: Board, req: Requirement) -> str:
    docs = "\n\n".join(f"### {d.name} ({d.kind})\n{d.content}" for d in req.documents)
    answers = await board.answers(req.id)
    text = (
        f"Título: {req.title}\nProyecto: {req.project}\n"
        f"Módulo: {req.capability or '?'} · Tipo RICEFW: {req.ricefw or '?'}\n"
        f"Perfil tecnológico: {req.profile}\n\n"
        f"Descripción:\n{req.description}\n\nDocumentos: {docs or '(ninguno)'}\n"
    )
    if answers:
        text += "\nRespuestas del cliente:\n" + "\n".join(f"- {a}" for a in answers)
    return text
