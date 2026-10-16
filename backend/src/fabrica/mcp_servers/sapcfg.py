from __future__ import annotations

import json
import os
from typing import Any, cast

import httpx
from mcp.server.mcpserver import MCPServer

from fabrica.mcp_servers.common import auth_kwargs, serve

server = MCPServer(
    name="sapcfg",
    instructions=(
        "Configuración automatizada de SAP mediante BC-Sets (SCPR) a través de SICF. "
        "Soporta simulación (dry-run) y activación controlada en órdenes de customizing."
    ),
    **auth_kwargs(8105),
)

_SYSTEMS_REGISTRY: dict[str, dict[str, Any]] = {}


def register_sapcfg_system(system_id: str, cfg: dict[str, Any]) -> None:
    _SYSTEMS_REGISTRY[system_id] = cfg


def _resolve_system(system_id: str) -> dict[str, Any]:
    sid = system_id or os.environ.get("DEFAULT_SAP_SYSTEM_ID", "")
    if sid in _SYSTEMS_REGISTRY:
        return _SYSTEMS_REGISTRY[sid]
    raw_env = os.environ.get("SAPCFG_SYSTEMS_JSON")
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                if sid in parsed:
                    return cast(dict[str, Any], parsed[sid])
                if not sid and parsed:
                    return cast(dict[str, Any], next(iter(parsed.values())))
        except Exception:
            pass
    try:
        import boto3

        prefix = os.environ.get("SSM_ABAP_PREFIX", "/fabrica-sw/abap")
        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        client = boto3.client("ssm", region_name=region)
        param = client.get_parameter(Name=f"{prefix}/{sid}", WithDecryption=True)
        raw = param["Parameter"]["Value"].strip()
        data = json.loads(raw) if raw.startswith("{") else {"password": raw}
        result = cast(dict[str, Any], data)
        _SYSTEMS_REGISTRY[sid] = result
        return result
    except Exception:
        pass
    return {
        "host": os.environ.get("SAP_HOST", "localhost"),
        "client": os.environ.get("SAP_CLIENT", "100"),
        "username": os.environ.get("SAP_USERNAME", "developer"),
        "password": os.environ.get("SAP_PASSWORD", "secret"),
    }


async def _call(system_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    cfg = _resolve_system(system_id)
    host = str(cfg.get("host", "localhost"))
    sap_client = str(cfg.get("client", "100"))
    sicf_base = os.environ.get("SAPCFG_SICF_BASE", "/sap/bc/z7bs/sapcfg")
    url = f"https://{host}{sicf_base}{path}?sap-client={sap_client}"
    auth = (str(cfg.get("username", "")), str(cfg.get("password", "")))
    verify = os.environ.get("SSL_VERIFY", "false").lower() == "true"
    timeout = float(os.environ.get("SAPCFG_TIMEOUT", "180"))
    async with httpx.AsyncClient(verify=verify, timeout=timeout) as h:
        r = await h.post(
            url, json=body, auth=auth, headers={"Accept": "application/json"}
        )
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}", "raw": r.text[:800]}
        try:
            parsed = r.json()
            return parsed if isinstance(parsed, dict) else {"ok": True, "data": parsed}
        except Exception:
            return {"ok": False, "error": "respuesta no-JSON", "raw": r.text[:800]}


@server.tool(description="Sanity: confirma que el server llega al handler ABAP en SAP.")
async def sapcfg_ping(sap_system_id: str = "") -> dict[str, Any]:
    return await _call(sap_system_id, "/ping", {})


@server.tool(
    description=(
        "Campos DDIC de una tabla/estructura de customizing "
        "(insumo para mapear spec -> campos)."
    )
)
async def sapcfg_ddic(tablename: str, sap_system_id: str = "") -> dict[str, Any]:
    return await _call(sap_system_id, "/ddic", {"tablename": tablename.upper()})


@server.tool(
    description="Lee entradas actuales de una tabla de customizing (para el diff del preview)."
)
async def sapcfg_read_table(
    tablename: str,
    where: str = "",
    max_rows: int = 200,
    sap_system_id: str = "") -> dict[str, Any]:
    return await _call(
        sap_system_id,
        "/read_table",
        {"tablename": tablename.upper(), "where": where, "max_rows": max_rows},
    )


@server.tool(
    description="Lee un BC-Set existente (sus valores) — diff/idempotencia y aprender formato."
)
async def sapcfg_bcset_read(
    bcset_id: str, sap_system_id: str = ""
) -> dict[str, Any]:
    return await _call(
        sap_system_id, "/bcset_read", {"bcset_id": bcset_id.upper()}
    )


@server.tool(
    description="GATE: construye el BC-Set value-based y ACTIVA EN SIMULACIÓN (dry-run)."
)
async def sapcfg_preview(
    bcset_id: str,
    text: str,
    blocks: list[dict[str, Any]],
    component: str = "",
    category: str = "",
    transport: str = "",
    sap_system_id: str = "",
) -> dict[str, Any]:
    w = await _call(
        sap_system_id,
        "/bcset_write",
        {
            "bcset_id": bcset_id.upper(),
            "text": text,
            "component": component,
            "category": category,
            "blocks": blocks,
        },
    )
    if not w.get("ok"):
        return {"step": "write", **w}
    sim = await _call(
        sap_system_id,
        "/bcset_activate",
        {
            "bcset_id": bcset_id.upper(),
            "simulate": True,
            "transport": transport,
        },
    )
    return {"write": w, "simulate": sim}


@server.tool(
    description="Activa DE VERDAD un BC-Set ya construido (tras aprobar el preview)."
)
async def sapcfg_apply(
    bcset_id: str, transport: str = "", sap_system_id: str = ""
) -> dict[str, Any]:
    return await _call(
        sap_system_id,
        "/bcset_activate",
        {"bcset_id": bcset_id.upper(), "simulate": False, "transport": transport},
    )


def run() -> None:
    serve(server, default_port=8105)
