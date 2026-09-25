from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from fabrica.db.session import init_db, session_scope
from fabrica.knowledge.lessons import record_lessons, relevant_lessons, similar_requirements
from fabrica.knowledge.standards import standards
from fabrica.mcp_servers.common import actor, auth_kwargs, serve

server = MCPServer(
    name="conocimiento",
    instructions=(
        "Conocimiento de la fábrica: estándares del cliente, lecciones aprendidas de errores "
        "anteriores y requisitos cerrados parecidos. Consúltalo antes de diseñar o programar."
    ),
    **auth_kwargs(8103),
)


@server.tool(description="Estándares obligatorios del cliente, globales y del módulo SAP indicado.")
async def estandares(modulo: str = "") -> list[str]:
    return standards().for_capability(modulo or None)


@server.tool(
    description="Lecciones aprendidas para una actividad (implementar, disenar_spec…) "
    "ordenadas por relevancia respecto al texto dado."
)
async def buscar_lecciones(actividad: str, texto: str, limite: int = 5) -> list[dict[str, Any]]:
    await init_db()
    async with session_scope() as s:
        lessons = await relevant_lessons(s, actividad, texto, limit=limite)
        return [
            {
                "id": lesson.id,
                "texto": lesson.text,
                "modulo": lesson.capability,
                "usos": lesson.uses,
            }
            for lesson in lessons
        ]


@server.tool(description="Registra una lección aprendida para que la fábrica no repita el error.")
async def guardar_leccion(
    actividad: str, texto: str, requisito_id: int, modulo: str | None = None
) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        created = await record_lessons(
            s,
            activity=actividad,
            issues=[texto],
            requirement_id=requisito_id,
            capability=modulo,
            source=actor(),
        )
        return {"creada": bool(created)}


@server.tool(
    description="Requisitos ya cerrados parecidos al texto, para reutilizar diseño y código."
)
async def requisitos_similares(texto: str, limite: int = 5) -> list[dict[str, Any]]:
    await init_db()
    async with session_scope() as s:
        found = await similar_requirements(s, texto, limit=limite)
        return [
            {"id": r.id, "titulo": r.title, "modulo": r.capability, "repo": r.repo_url}
            for r in found
        ]


def run() -> None:
    serve(server, default_port=8103)
