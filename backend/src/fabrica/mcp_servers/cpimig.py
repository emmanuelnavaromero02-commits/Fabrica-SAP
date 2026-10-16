from __future__ import annotations

import json
import os
import time
from typing import Any, cast

import httpx
from mcp.server.mcpserver import MCPServer

from fabrica.mcp_servers.common import auth_kwargs, serve

server = MCPServer(
    name="cpimig",
    instructions=(
        "Migración e inspección de tenants SAP Cloud Integration (Neo / Cloud Foundry). "
        "Permite consultar estado, paquetes e iFlows."
    ),
    **auth_kwargs(8104),
)

_TOKEN_PATH: dict[str, str] = {"neo": "/oauth2/api/v1/token", "cf": "/oauth/token"}
_CONN_REGISTRY: dict[str, dict[str, Any]] = {}


def register_cpi_connection(connection_id: str, cfg: dict[str, Any]) -> None:
    _CONN_REGISTRY[connection_id] = cfg


def _resolve_connection(connection_id: str) -> dict[str, Any]:
    cid = (connection_id or os.environ.get("DEFAULT_CONNECTION_ID", "")).strip()
    if cid in _CONN_REGISTRY:
        return _CONN_REGISTRY[cid]
    raw_env = os.environ.get("CPI_CONNECTIONS_JSON")
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                if cid in parsed:
                    return cast(dict[str, Any], parsed[cid])
                if not cid and parsed:
                    return cast(dict[str, Any], next(iter(parsed.values())))
        except Exception:
            pass
    try:
        import boto3

        prefix = os.environ.get("SSM_CPI_PREFIX", "/fabrica-sw/cpi")
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        client = boto3.client("ssm", region_name=region)
        param = client.get_parameter(Name=f"{prefix}/{cid}", WithDecryption=True)
        data = json.loads(param["Parameter"]["Value"].strip())
        result = cast(dict[str, Any], data)
        _CONN_REGISTRY[cid] = result
        return result
    except Exception:
        pass
    return {
        "nombre": cid or "local",
        "source": {
            "host": os.environ.get("CPI_SOURCE_HOST", "localhost"),
            "token_host": os.environ.get("CPI_SOURCE_TOKEN_HOST", "localhost"),
            "oauth_id": os.environ.get("CPI_SOURCE_OAUTH_ID", "dummy"),
            "oauth_secret": os.environ.get("CPI_SOURCE_OAUTH_SECRET", "dummy"),
            "kind": "neo",
        },
        "target": {
            "host": os.environ.get("CPI_TARGET_HOST", "localhost"),
            "token_host": os.environ.get("CPI_TARGET_TOKEN_HOST", "localhost"),
            "oauth_id": os.environ.get("CPI_TARGET_OAUTH_ID", "dummy"),
            "oauth_secret": os.environ.get("CPI_TARGET_OAUTH_SECRET", "dummy"),
            "kind": "cf",
        },
    }


class CpiClient:
    def __init__(self, side_cfg: dict[str, Any]) -> None:
        self.host = str(side_cfg.get("host", "")).rstrip("/")
        self.token_host = str(side_cfg.get("token_host", "")).rstrip("/")
        self.oauth_id = str(side_cfg.get("oauth_id", ""))
        self.oauth_secret = str(side_cfg.get("oauth_secret", ""))
        self.kind = str(side_cfg.get("kind", "cf")).lower()
        self.token_path = str(
            side_cfg.get("token_path") or _TOKEN_PATH.get(self.kind, "/oauth/token")
        )
        self._token: str = ""
        self._token_exp: float = 0.0
        self._xsrf: str = ""

    @property
    def api(self) -> str:
        return f"https://{self.host}/api/v1"

    async def token(self, http: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_exp - 60:
            return self._token
        url = f"https://{self.token_host}{self.token_path}?grant_type=client_credentials"
        r = await http.post(
            url,
            auth=(self.oauth_id, self.oauth_secret),
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        self._token = str(data["access_token"])
        self._token_exp = time.monotonic() + float(data.get("expires_in", 3600))
        return self._token

    async def auth_headers(self, http: httpx.AsyncClient) -> dict[str, str]:
        tok = await self.token(http)
        return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}

    async def fetch_xsrf(self, http: httpx.AsyncClient) -> str:
        headers = await self.auth_headers(http)
        headers["X-CSRF-Token"] = "Fetch"
        r = await http.get(f"{self.api}/", headers=headers)
        r.raise_for_status()
        self._xsrf = r.headers.get("X-CSRF-Token", "")
        return self._xsrf

    async def get(
        self,
        http: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None = None,
        paginate: bool = True,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        skip = 0
        page = 500
        p = dict(params or {})
        while True:
            q = dict(p)
            if paginate:
                q["$top"] = page
                q["$skip"] = skip
            r = await http.get(
                f"{self.api}/{path.lstrip('/')}",
                headers=await self.auth_headers(http),
                params=q,
            )
            r.raise_for_status()
            body = r.json()
            d = body.get("d", body)
            results = d.get("results") if isinstance(d, dict) else None
            if results is None:
                return [d] if isinstance(d, dict) else []
            out.extend(results)
            if not paginate or len(results) < page:
                return out
            skip += page


async def _tenant_probe(http: httpx.AsyncClient, client: CpiClient) -> dict[str, Any]:
    info: dict[str, Any] = {"host": client.host, "kind": client.kind}
    try:
        await client.token(http)
        info["token"] = "ok"
    except Exception as e:
        info["token"] = f"error: {type(e).__name__}: {str(e)[:200]}"
        info["ok"] = False
        return info
    try:
        await client.fetch_xsrf(http)
        info["xsrf"] = "ok" if client._xsrf else "vacio"
    except Exception as e:
        info["xsrf"] = f"error: {str(e)[:200]}"
    try:
        pkgs = await client.get(http, "IntegrationPackages", {"$select": "Id"})
        info["integration_packages"] = len(pkgs)
        info["ok"] = True
    except Exception as e:
        info["integration_packages"] = f"error: {str(e)[:200]}"
        info["ok"] = False
    return info


@server.tool(
    description=(
        "Sanity de una conexión de migración CPI: valida tokens OAuth, "
        "XSRF y alcance OData de ambos tenants."
    )
)
async def cpi_status(connection_id: str = "") -> dict[str, Any]:
    cfg = _resolve_connection(connection_id)
    src = CpiClient(cfg["source"])
    tgt = CpiClient(cfg["target"])
    verify = os.environ.get("SSL_VERIFY", "true").lower() == "true"
    timeout = float(os.environ.get("CPI_HTTP_TIMEOUT", "60"))
    async with httpx.AsyncClient(verify=verify, timeout=timeout) as http:
        source = await _tenant_probe(http, src)
        target = await _tenant_probe(http, tgt)
    return {
        "connection": cfg.get("nombre", connection_id),
        "ok": bool(source.get("ok") and target.get("ok")),
        "source": source,
        "target": target,
    }


@server.tool(
    description=(
        "Lista paquetes de integración o artefactos (iFlows) de un inquilino de Cloud Integration."
    )
)
async def cpi_list_flows(
    connection_id: str = "", side: str = "source", package: str = ""
) -> dict[str, Any]:
    side_clean = (side or "source").lower()
    if side_clean not in ("source", "target"):
        return {"ok": False, "error": "side debe ser 'source' o 'target'"}
    cfg = _resolve_connection(connection_id)
    side_cfg = cfg.get(side_clean)
    if not isinstance(side_cfg, dict):
        return {"ok": False, "error": f"no hay configuración para side '{side_clean}'"}
    client = CpiClient(side_cfg)
    verify = os.environ.get("SSL_VERIFY", "true").lower() == "true"
    timeout = float(os.environ.get("CPI_HTTP_TIMEOUT", "60"))
    async with httpx.AsyncClient(verify=verify, timeout=timeout) as http:
        try:
            if package:
                flows = await client.get(
                    http,
                    f"IntegrationPackages('{package}')/IntegrationDesuntimeArtifacts",
                )
                return {"ok": True, "package": package, "artifacts": flows}
            pkgs = await client.get(http, "IntegrationPackages")
            return {"ok": True, "packages": pkgs}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def run() -> None:
    serve(server, default_port=8104)
