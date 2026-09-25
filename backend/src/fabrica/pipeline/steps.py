"""Trabajo de los agentes en cada etapa automática.

Cada función recibe el tablero de una transacción y devuelve qué pasó:
- advance: la etapa terminó bien → pasa a la siguiente.
- blocked: faltan respuestas del cliente o una persona debe intervenir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from fabrica.agents import roles
from fabrica.blackboard.service import Board
from fabrica.config import get_settings
from fabrica.db.models import MessageKind, Requirement
from fabrica.escalation.router import EscalationRouter
from fabrica.git.repo import RepoStore
from fabrica.llm.base import LLMRequest
from fabrica.llm.gateway import ModelGateway
from fabrica.sap.bridge import Assertion
from fabrica.sap.factory import bridge_for
from fabrica.verifiers.abap import AbapVerifier
from fabrica.verifiers.base import Verification
from fabrica.verifiers.spec import SpecVerifier

SPEC_JSON = "diseno/spec.json"


@dataclass
class StepResult:
    outcome: Literal["advance", "blocked"]
    note: str = ""


class _Check:
    """Verificador a partir de una función simple sobre la salida."""

    def __init__(self, fn: Any) -> None:
        self.fn = fn

    async def verify(self, output: dict[str, Any]) -> Verification:
        return Verification.from_issues(self.fn(output))


def _request(role: roles.AgentRole, prompt: str, workdir: str | None = None) -> LLMRequest:
    return LLMRequest(system=role.system, prompt=prompt, schema=role.schema, workdir=workdir)


async def _context(board: Board, req: Requirement) -> str:
    docs = "\n\n".join(f"### {d.name} ({d.kind})\n{d.content}" for d in req.documents)
    answers = await board.answers(req.id)
    text = (
        f"Título: {req.title}\nProyecto: {req.project}\n"
        f"Módulo: {req.capability or '?'} · Tipo RICEFW: {req.ricefw or '?'}\n\n"
        f"Descripción:\n{req.description}\n\nDocumentos: {docs or '(ninguno)'}\n"
    )
    if answers:
        text += "\nRespuestas del cliente:\n" + "\n".join(f"- {a}" for a in answers)
    return text


class Steps:
    def __init__(self, board: Board, gateway: ModelGateway, repos: RepoStore) -> None:
        self.board = board
        self.repos = repos
        self.router = EscalationRouter(gateway, board)

    async def _save(
        self, req: Requirement, files: dict[str, str], message: str, author: str
    ) -> None:
        commit = await self.repos.write_files(req.id, files, message, author)
        for path in files:
            await self.board.record_artifact(req.id, path, commit, author)

    # ── Recepción ──────────────────────────────────────────────────────────
    async def recepcion(self, req: Requirement) -> StepResult:
        req.repo_url = await self.repos.ensure_repo(req.id, req.title)
        context = await _context(self.board, req)

        if not req.capability:
            valid = {"R", "I", "C", "E", "F", "W"}
            out = await self.router.run(
                req.id,
                "clasificar",
                _request(roles.CLASIFICADOR, context),
                _Check(
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
            _request(roles.ANALISTA, await _context(self.board, req)),
            _Check(lambda o: [] if o.get("summary") else ["Análisis vacío"]),
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

    # ── Diseño ─────────────────────────────────────────────────────────────
    async def diseno(self, req: Requirement) -> StepResult:
        allowed = get_settings().sap_allowed_packages
        out = await self.router.run(
            req.id,
            "disenar_spec",
            _request(roles.ARQUITECTO, await _context(self.board, req)),
            SpecVerifier(allowed),
            agent=roles.ARQUITECTO.name,
        )
        if not out.passed or out.output is None:
            return StepResult("blocked", "El diseño requiere una persona")
        await self._save(
            req,
            {
                "diseno/spec.md": out.output["spec_markdown"],
                SPEC_JSON: json.dumps(out.output, ensure_ascii=False, indent=2),
            },
            "Diseño: especificación y aseveraciones",
            roles.ARQUITECTO.name,
        )
        return StepResult("advance")

    # ── Construcción ───────────────────────────────────────────────────────
    async def construccion(self, req: Requirement) -> StepResult:
        raw = await self.repos.read_file(req.id, SPEC_JSON)
        if raw is None:
            return StepResult("blocked", "No hay spec aprobada en el repo")
        spec = json.loads(raw)
        main = spec["objects"][0]
        assertions = [Assertion(**a) for a in spec["assertions"]]
        verifier = AbapVerifier(
            bridge_for(self.board, req.id, roles.DESARROLLADOR.name),
            main_object=main,
            assertions=assertions,
            transport=f"FABK9{req.id:05d}",
        )
        prompt = (
            f"{await _context(self.board, req)}\n\n## Spec\n{spec['spec_markdown']}\n\n"
            f"Objeto principal: {main['name']}\n"
            f"Aseveraciones: {json.dumps(spec['assertions'], ensure_ascii=False)}"
        )

        dev = await self.router.run(
            req.id,
            "implementar",
            _request(roles.DESARROLLADOR, prompt),
            verifier,
            agent=roles.DESARROLLADOR.name,
        )
        if not dev.passed or dev.output is None:
            return StepResult("blocked", "La implementación requiere una persona")

        code = "\n\n".join(f"// {f['path']}\n{f['content']}" for f in dev.output["files"])
        review = await self.router.run(
            req.id,
            "revisar",
            _request(roles.REVISOR, f"{prompt}\n\n## Código\n{code}"),
            _Check(
                lambda o: [] if o.get("approved") else list(o.get("objections") or ["Rechazado"])
            ),
            agent=roles.REVISOR.name,
            avoid_provider=dev.provider,
        )
        if not review.passed:
            return StepResult("blocked", "El revisor dejó objeciones sin resolver")

        docs = await self.router.run(
            req.id,
            "documentar",
            _request(roles.DOCUMENTADOR, f"{prompt}\n\n## Código\n{code}"),
            _Check(lambda o: [] if o.get("markdown") else ["Documento vacío"]),
            agent=roles.DOCUMENTADOR.name,
        )
        files = {f["path"]: f["content"] for f in dev.output["files"]}
        files["evidencia/construccion.json"] = json.dumps(
            {"nivel": dev.tier, "proveedor": dev.provider, "revisor": review.provider},
            ensure_ascii=False,
            indent=2,
        )
        if docs.passed and docs.output:
            files["docs/manual-tecnico.md"] = docs.output["markdown"]
        await self._save(
            req, files, f"Construcción: código verificado ({dev.tier})", roles.DESARROLLADOR.name
        )
        return StepResult("advance")
