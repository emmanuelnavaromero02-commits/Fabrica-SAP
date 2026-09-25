from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from fabrica.blackboard.service import Board
from fabrica.db.session import init_db, session_scope
from fabrica.git.repo import repo_store
from fabrica.mcp_servers.common import actor, serve
from fabrica.pipeline.steps import SPEC_JSON
from fabrica.sap.bridge import Assertion, CheckResult, PolicyViolation, SapObject
from fabrica.sap.factory import bridge_for

server = MCPServer(
    name="sap",
    instructions=(
        "Puente SAP del requisito. El sistema lo decide el servidor. Escritura solo en DEV y "
        "paquetes Z/Y; los transportes los libera una persona."
    ),
)


def _result(result: CheckResult) -> dict[str, Any]:
    return {"ok": result.ok, "hallazgos": [f.as_text() for f in result.findings]}


@server.tool(description="Lee el código fuente de un objeto en DEV.")
async def leer_objeto(requisito_id: int, nombre: str) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        obj = await bridge_for(Board(s), requisito_id, actor()).read_object(nombre)
        return {"existe": obj is not None, "fuente": obj.source if obj else ""}


@server.tool(
    description="Crea o actualiza un objeto ABAP en DEV dentro del transporte del requisito."
)
async def escribir_objeto(
    requisito_id: int, nombre: str, tipo: str, paquete: str, fuente: str
) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        bridge = bridge_for(Board(s), requisito_id, actor())
        obj = SapObject(name=nombre, type=tipo, package=paquete, source=fuente)
        try:
            await bridge.write_object(obj, transport=f"FABK9{requisito_id:05d}")
        except PolicyViolation as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "sistema": bridge.system}


@server.tool(description="Revisión de sintaxis del objeto en DEV.")
async def revisar_sintaxis(requisito_id: int, nombre: str) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        return _result(await bridge_for(Board(s), requisito_id, actor()).syntax_check(nombre))


@server.tool(description="Corre ATC (Clean Core) sobre el objeto.")
async def correr_atc(requisito_id: int, nombre: str) -> dict[str, Any]:
    await init_db()
    async with session_scope() as s:
        return _result(await bridge_for(Board(s), requisito_id, actor()).run_atc(nombre))


@server.tool(description="Corre las aseveraciones de la spec como pruebas sobre el objeto.")
async def correr_pruebas(requisito_id: int, nombre: str) -> dict[str, Any]:
    raw = await repo_store().read_file(requisito_id, SPEC_JSON)
    if raw is None:
        return {"ok": False, "hallazgos": ["El requisito aún no tiene spec"]}
    assertions = [Assertion(**a) for a in json.loads(raw)["assertions"]]
    await init_db()
    async with session_scope() as s:
        bridge = bridge_for(Board(s), requisito_id, actor())
        return _result(await bridge.run_unit(nombre, assertions))


def run() -> None:
    serve(server, default_port=8102)
