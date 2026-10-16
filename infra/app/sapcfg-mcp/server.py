#!/usr/bin/env python3
"""sapcfg-mcp — MCP independiente para CONFIGURAR SAP por código (genérico, cualquier módulo).

Mecanismo: BC-Sets (SCPR). El servidor expone tools y delega en el handler ABAP
`ZCL_7BS_SAPCFG_API` publicado en SICF (`/sap/bc/z7bs/sapcfg`), que por dentro llama
las FMs estándar `SCPR_EXT_BCSET_WRITE/READ` y `SCPR_ACTIVATE_BCSETS_REMOTE`.

Flujo (híbrido con gate humano):
  sapcfg_preview  -> construye el BC-Set + ACTIVA EN SIMULACIÓN (dry-run) -> qué cambiaría
  (humano revisa/aprueba)
  sapcfg_apply    -> ACTIVA de verdad en una orden de customizing (W)

Auth a SAP = basic, con las MISMAS credenciales por sistema que el abap-mcp, leídas de
SSM Parameter Store (`/fabrica-sw/abap/<system_id>`). Esto es el SCAFFOLDING: las tools
quedan registradas y el server conecta; la lógica fina (diff bonito, RECATTR, etc.) se
itera en vivo una vez exista el nodo SICF.
"""
import json
import os

import httpx
from fastmcp import FastMCP

SSM_ABAP_PREFIX = os.environ.get("SSM_ABAP_PREFIX", "/fabrica-sw/abap")
DEFAULT_SYSTEM = os.environ.get("DEFAULT_SAP_SYSTEM_ID", "")
SICF_BASE = os.environ.get("SAPCFG_SICF_BASE", "/sap/bc/z7bs/sapcfg")
SSL_VERIFY = os.environ.get("SSL_VERIFY", "false").lower() == "true"

mcp = FastMCP("SAPCFG")
_cache: dict = {}


def _resolve_system(system_id: str) -> dict:
    """Bundle de conexión {host,client,username,password,...} desde SSM, cacheado."""
    sid = system_id or DEFAULT_SYSTEM
    if not sid:
        raise RuntimeError("falta sap_system_id y no hay DEFAULT_SAP_SYSTEM_ID")
    if sid in _cache:
        return _cache[sid]
    import boto3  # import perezoso

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    client = boto3.client("ssm", region_name=region)
    raw = (client.get_parameter(Name=f"{SSM_ABAP_PREFIX}/{sid}", WithDecryption=True)
           ["Parameter"]["Value"]).strip()
    cfg = json.loads(raw) if raw.startswith("{") else {"password": raw}
    if not (cfg.get("host") and cfg.get("username") and cfg.get("password")):
        raise RuntimeError(f"bundle SSM incompleto para '{sid}' (faltan host/username/password)")
    _cache[sid] = cfg
    return cfg


async def _call(system_id: str, path: str, body: dict) -> dict:
    """POST autenticado al handler ABAP en SICF y devuelve su JSON."""
    cfg = _resolve_system(system_id)
    host = cfg["host"]
    sap_client = str(cfg.get("client", "100"))
    url = f"https://{host}{SICF_BASE}{path}?sap-client={sap_client}"
    auth = (cfg["username"], cfg["password"])
    async with httpx.AsyncClient(verify=SSL_VERIFY, timeout=180) as h:
        r = await h.post(url, json=body, auth=auth, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}", "raw": r.text[:800]}
        try:
            return r.json()
        except Exception:
            return {"ok": False, "error": "respuesta no-JSON", "raw": r.text[:800]}


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


@mcp.tool()
async def sapcfg_ping(sap_system_id: str = "") -> str:
    """Sanity: confirma que el server llega al handler ABAP en SAP."""
    return _j(await _call(sap_system_id, "/ping", {}))


@mcp.tool()
async def sapcfg_ddic(tablename: str, sap_system_id: str = "") -> str:
    """Campos DDIC de una tabla/estructura de customizing (insumo para mapear spec -> campos)."""
    return _j(await _call(sap_system_id, "/ddic", {"tablename": tablename.upper()}))


@mcp.tool()
async def sapcfg_read_table(tablename: str, where: str = "", max_rows: int = 200,
                            sap_system_id: str = "") -> str:
    """Lee entradas actuales de una tabla de customizing (para el diff del preview)."""
    return _j(await _call(sap_system_id, "/read_table",
                          {"tablename": tablename.upper(), "where": where, "max_rows": max_rows}))


@mcp.tool()
async def sapcfg_bcset_read(bcset_id: str, sap_system_id: str = "") -> str:
    """Lee un BC-Set existente (sus valores) — diff/idempotencia y aprender formato."""
    return _j(await _call(sap_system_id, "/bcset_read", {"bcset_id": bcset_id.upper()}))


@mcp.tool()
async def sapcfg_preview(bcset_id: str, text: str, blocks: list,
                         component: str = "", category: str = "", transport: str = "",
                         sap_system_id: str = "") -> str:
    """GATE: construye el BC-Set value-based y ACTIVA EN SIMULACIÓN (dry-run). Devuelve qué
    cambiaría, SIN escribir. `blocks` = [{tablename, records:[{recnumber, fields:[{name,value}]}]}]."""
    w = await _call(sap_system_id, "/bcset_write",
                    {"bcset_id": bcset_id.upper(), "text": text,
                     "component": component, "category": category, "blocks": blocks})
    if not w.get("ok"):
        return _j({"step": "write", **w})
    sim = await _call(sap_system_id, "/bcset_activate",
                      {"bcset_id": bcset_id.upper(), "simulate": True, "transport": transport})
    return _j({"write": w, "simulate": sim})


@mcp.tool()
async def sapcfg_apply(bcset_id: str, transport: str = "", sap_system_id: str = "") -> str:
    """Activa DE VERDAD un BC-Set ya construido (tras aprobar el preview). Escribe en las tablas
    destino y registra en la orden de customizing indicada."""
    return _j(await _call(sap_system_id, "/bcset_activate",
                          {"bcset_id": bcset_id.upper(), "simulate": False, "transport": transport}))


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("SERVER_HOST", "0.0.0.0"),
        port=int(os.environ.get("SERVER_PORT", "8000")),
    )
