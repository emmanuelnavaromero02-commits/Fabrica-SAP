from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from fabrica.blackboard.service import Board
from fabrica.git.repo import repo_store
from fabrica.mcp_servers.common import actor, auth_kwargs, mcp_session, serve
from fabrica.pipeline.steps import SPEC_JSON
from fabrica.sap.adt_bridge import AdtError
from fabrica.sap.bridge import Assertion, CheckResult, PolicyViolation, SapObject
from fabrica.sap.factory import sap_for

server = MCPServer(
    name="sap",
    instructions=(
        "Puente SAP del requisito. El sistema lo decide el servidor. Escritura solo en DEV y "
        "paquetes Z/Y; los transportes los libera una persona."
    ),
    **auth_kwargs(8102),
)


def _result(result: CheckResult) -> dict[str, Any]:
    return {"ok": result.ok, "hallazgos": [f.as_text() for f in result.findings]}


@server.tool(description="Lee el código fuente de un objeto en DEV.")
async def leer_objeto(requisito_id: int, nombre: str) -> dict[str, Any]:
    async with mcp_session(requisito_id) as s:
        sap = await sap_for(Board(s), requisito_id, actor())
        obj = await sap.bridge.read_object(nombre)
        return {"existe": obj is not None, "fuente": obj.source if obj else ""}


@server.tool(description="Crea o actualiza un objeto ABAP en DEV en la orden del requisito.")
async def escribir_objeto(
    requisito_id: int, nombre: str, tipo: str, paquete: str, fuente: str
) -> dict[str, Any]:
    async with mcp_session(requisito_id) as s:
        sap = await sap_for(Board(s), requisito_id, actor())
        obj = SapObject(name=nombre, type=tipo, package=paquete, source=fuente)
        try:
            transport = await sap.write(obj)
        except (PolicyViolation, AdtError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "sistema": sap.bridge.system, "transporte": transport}


@server.tool(description="Revisión de sintaxis del objeto en DEV.")
async def revisar_sintaxis(requisito_id: int, nombre: str) -> dict[str, Any]:
    async with mcp_session(requisito_id) as s:
        sap = await sap_for(Board(s), requisito_id, actor())
        return _result(await sap.bridge.syntax_check(nombre))


@server.tool(description="Activa el objeto en DEV.")
async def activar(requisito_id: int, nombre: str) -> dict[str, Any]:
    async with mcp_session(requisito_id) as s:
        sap = await sap_for(Board(s), requisito_id, actor())
        return _result(await sap.bridge.activate(nombre))


@server.tool(description="Corre ATC (Clean Core) sobre el objeto.")
async def correr_atc(requisito_id: int, nombre: str) -> dict[str, Any]:
    async with mcp_session(requisito_id) as s:
        sap = await sap_for(Board(s), requisito_id, actor())
        return _result(await sap.bridge.run_atc(nombre))


@server.tool(description="Corre ABAP Unit y las aseveraciones de la spec sobre el objeto.")
async def correr_pruebas(requisito_id: int, nombre: str) -> dict[str, Any]:
    raw = await repo_store().read_file(requisito_id, SPEC_JSON)
    if raw is None:
        return {"ok": False, "hallazgos": ["El requisito aún no tiene spec"]}
    assertions = [Assertion(**a) for a in json.loads(raw)["assertions"]]
    async with mcp_session(requisito_id) as s:
        sap = await sap_for(Board(s), requisito_id, actor())
        return _result(await sap.bridge.run_unit(nombre, assertions))


@server.tool(description="Orden de transporte del requisito y los objetos que contiene.")
async def ver_transporte(requisito_id: int) -> list[dict[str, Any]]:
    async with mcp_session(requisito_id) as s:
        return [
            {"sistema": t.system, "orden": t.number, "objetos": t.objects, "estado": t.status}
            for t in await Board(s).transports(requisito_id)
        ]


def run() -> None:
    serve(server, default_port=8102)
