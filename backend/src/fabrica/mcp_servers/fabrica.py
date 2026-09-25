"""MCP `fabrica`: el tablero para agentes y personas (Claude Code, Codex, VS Code)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from fabrica.blackboard.service import Board
from fabrica.catalog import StageKind, stage_machine
from fabrica.db.models import MessageKind, Requirement, RunState
from fabrica.db.session import init_db, session_scope
from fabrica.mcp_servers.common import actor, serve

server = MCPServer(
    name="fabrica",
    instructions=(
        "Tablero de la fábrica SAP. Lee requisitos y conversaciones, publica mensajes "
        "tipados. Las decisiones de compuerta NO se toman por aquí: van por el portal."
    ),
)

_ALLOWED_KINDS = {k.value for k in MessageKind} - {MessageKind.DECISION.value}


@server.tool(description="Requisitos que esperan a un rol (compuertas) o están bloqueados.")
async def mi_cola(rol: str) -> list[dict[str, Any]]:
    await init_db()
    machine = stage_machine()
    gates = {s.key for s in machine.stages if s.kind is StageKind.GATE and rol in s.roles}
    async with session_scope() as s:
        rows = await s.scalars(select(Requirement).where(Requirement.state != RunState.DONE))
        return [
            {"id": r.id, "titulo": r.title, "etapa": r.stage, "estado": r.state}
            for r in rows.all()
            if r.stage in gates or r.state == RunState.BLOCKED
        ]


@server.tool(description="Detalle de un requisito con sus documentos y conversación.")
async def leer_requisito(requisito_id: int) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        board = Board(s)
        req = await board.requirement(requisito_id)
        return {
            "id": req.id,
            "titulo": req.title,
            "descripcion": req.description,
            "etapa": req.stage,
            "estado": req.state,
            "modulo": req.capability,
            "ricefw": req.ricefw,
            "repo": req.repo_url,
            "documentos": [
                {"nombre": d.name, "tipo": d.kind, "contenido": d.content} for d in req.documents
            ],
            "mensajes": [
                {
                    "id": m.id,
                    "de": m.sender,
                    "para": m.recipient,
                    "tipo": m.kind,
                    "texto": m.body,
                    "resuelto": m.resolved,
                }
                for m in await board.messages(requisito_id)
            ],
        }


@server.tool(description="Publica un mensaje tipado (pregunta, propuesta, objecion, evidencia…).")
async def enviar_mensaje(
    requisito_id: int, hilo: str, tipo: str, texto: str, para: str | None = None
) -> dict[str, Any]:
    if tipo not in _ALLOWED_KINDS:
        raise ValueError(f"Tipo no permitido: {tipo}. Usa uno de {sorted(_ALLOWED_KINDS)}")
    await init_db()
    async with session_scope() as s:
        msg = await Board(s).post(
            requisito_id,
            thread=hilo,
            sender=actor(),
            recipient=para,
            kind=MessageKind(tipo),
            body=texto,
        )
        return {"id": msg.id, "hilo": hilo, "tipo": tipo}


@server.tool(description="Responde una pregunta abierta del tablero.")
async def responder_pregunta(requisito_id: int, pregunta_id: int, texto: str) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        msg = await Board(s).answer(requisito_id, pregunta_id, actor(), texto)
        return {"id": msg.id}


def run() -> None:
    serve(server, default_port=8101)
