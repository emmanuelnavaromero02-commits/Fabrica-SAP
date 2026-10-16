#!/usr/bin/env python3
"""cpimig-mcp — MCP genérico para migrar tenants SAP Cloud Integration (Neo -> CF).

El pack Postman `ci.migrationpack-1.9.1` es la especificación: cada colección MIGxxx
es un dominio de migración sobre la API OData pública de CPI (`/api/v1/`), con el
patrón GET origen -> transformar -> POST destino. Este server encapsula lo tedioso
(OAuth2 client_credentials, XSRF, paginación) en `CpiClient` y expone tools de alto
nivel que Claude Code orquesta e itera.

F0 (scaffold, SOLO LECTURA): `cpi_status` y `cpi_list_flows`. Las tools de migración
(dry_run + gate) llegan en fases posteriores. Ver docs/diseno-cpimig-mcp.md.

Conexiones (multi-cliente): cada tool recibe `connection_id`. La conexión se resuelve
desde SSM Parameter Store `/fabrica-sw/cpi/<connection_id>` (bundle JSON, SecureString),
igual que el abap-mcp resuelve sistemas SAP. En F2 el panel administra estas conexiones
(espejo no-secreto en proyecto-db + secreto en SSM).

Bundle esperado en SSM:
{
  "nombre": "cliente-x neo->cf",
  "source": {"host": "xxx.hana.ondemand.com", "token_host": "xxx.authentication...",
             "oauth_id": "...", "oauth_secret": "...", "kind": "neo"},
  "target": {"host": "yyy.it-cpi...hana.ondemand.com", "token_host": "yyy.authentication...",
             "oauth_id": "...", "oauth_secret": "...", "kind": "cf"}
}
"""
import json
import os
import time

import httpx
from fastmcp import FastMCP

SSM_CPI_PREFIX = os.environ.get("SSM_CPI_PREFIX", "/fabrica-sw/cpi")
DEFAULT_CONNECTION = os.environ.get("DEFAULT_CONNECTION_ID", "")
SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() == "true"
HTTP_TIMEOUT = float(os.environ.get("CPI_HTTP_TIMEOUT", "120"))

# Path del endpoint de token por tipo de tenant (MIG010): Neo vs Cloud Foundry.
_TOKEN_PATH = {"neo": "/oauth2/api/v1/token", "cf": "/oauth/token"}

mcp = FastMCP("CPIMIG")
_conn_cache: dict = {}


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _resolve_connection(connection_id: str) -> dict:
    """Bundle {source, target, ...} de la conexión desde SSM, cacheado."""
    cid = (connection_id or DEFAULT_CONNECTION).strip()
    if not cid:
        raise RuntimeError("falta connection_id y no hay DEFAULT_CONNECTION_ID")
    if cid in _conn_cache:
        return _conn_cache[cid]
    import boto3  # import perezoso

    region = (os.environ.get("AWS_REGION")
              or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    client = boto3.client("ssm", region_name=region)
    raw = (client.get_parameter(Name=f"{SSM_CPI_PREFIX}/{cid}", WithDecryption=True)
           ["Parameter"]["Value"]).strip()
    cfg = json.loads(raw)
    for side in ("source", "target"):
        s = cfg.get(side) or {}
        missing = [k for k in ("host", "token_host", "oauth_id", "oauth_secret")
                   if not s.get(k)]
        if missing:
            raise RuntimeError(f"bundle SSM incompleto para '{cid}': {side} sin {missing}")
    _conn_cache[cid] = cfg
    return cfg


class CpiClient:
    """Cliente OData de un tenant CPI: OAuth2 client_credentials + XSRF + GET paginado.

    Es el corazón reutilizable; los dominios de migración se orquestan encima. Un
    `CpiClient` representa UN tenant (origen o destino). No abre conexión hasta el
    primer uso; se le pasa un `httpx.AsyncClient` compartido por llamada.
    """

    def __init__(self, side_cfg: dict):
        self.host = side_cfg["host"].rstrip("/")
        self.token_host = side_cfg["token_host"].rstrip("/")
        self.oauth_id = side_cfg["oauth_id"]
        self.oauth_secret = side_cfg["oauth_secret"]
        self.kind = (side_cfg.get("kind") or "cf").lower()
        self.token_path = side_cfg.get("token_path") or _TOKEN_PATH.get(self.kind, "/oauth/token")
        self._token = ""
        self._token_exp = 0.0
        self._xsrf = ""

    @property
    def api(self) -> str:
        return f"https://{self.host}/api/v1"

    async def token(self, http: httpx.AsyncClient) -> str:
        """Access token OAuth2 (client_credentials), cacheado hasta ~60s antes de expirar."""
        if self._token and time.monotonic() < self._token_exp - 60:
            return self._token
        url = f"https://{self.token_host}{self.token_path}?grant_type=client_credentials"
        r = await http.post(url, auth=(self.oauth_id, self.oauth_secret),
                            headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.monotonic() + float(data.get("expires_in", 3600))
        return self._token

    async def _auth_headers(self, http: httpx.AsyncClient) -> dict:
        return {"Authorization": f"Bearer {await self.token(http)}",
                "Accept": "application/json"}

    async def fetch_xsrf(self, http: httpx.AsyncClient) -> str:
        """CSRF token para escrituras (X-CSRF-Token: Fetch contra /api/v1/)."""
        h = await self._auth_headers(http)
        h["X-CSRF-Token"] = "Fetch"
        r = await http.get(f"{self.api}/", headers=h)
        r.raise_for_status()
        self._xsrf = r.headers.get("X-CSRF-Token", "")
        return self._xsrf

    async def get(self, http: httpx.AsyncClient, path: str,
                  params: dict | None = None, paginate: bool = True) -> list:
        """GET OData con paginación $skip/$top automática. Devuelve la lista de results."""
        out: list = []
        skip = 0
        page = 500
        p = dict(params or {})
        while True:
            q = dict(p)
            if paginate:
                q["$top"] = page
                q["$skip"] = skip
            r = await http.get(f"{self.api}/{path.lstrip('/')}",
                               headers=await self._auth_headers(http), params=q)
            r.raise_for_status()
            body = r.json()
            # OData v2: {"d": {"results": [...]}} o {"d": {...}} para entidad única.
            d = body.get("d", body)
            results = d.get("results") if isinstance(d, dict) else None
            if results is None:
                return [d] if d else []
            out.extend(results)
            if not paginate or len(results) < page:
                return out
            skip += page


async def _tenant_probe(http: httpx.AsyncClient, client: CpiClient) -> dict:
    """Valida un tenant: token + XSRF + conteo de paquetes. Para cpi_status."""
    info: dict = {"host": client.host, "kind": client.kind}
    try:
        await client.token(http)
        info["token"] = "ok"
    except Exception as e:  # noqa: BLE001
        info["token"] = f"error: {type(e).__name__}: {str(e)[:200]}"
        return {**info, "ok": False}
    try:
        await client.fetch_xsrf(http)
        info["xsrf"] = "ok" if client._xsrf else "vacío"
    except Exception as e:  # noqa: BLE001
        info["xsrf"] = f"error: {str(e)[:200]}"
    try:
        pkgs = await client.get(http, "IntegrationPackages", {"$select": "Id"})
        info["integration_packages"] = len(pkgs)
        info["ok"] = True
    except Exception as e:  # noqa: BLE001
        info["integration_packages"] = f"error: {str(e)[:200]}"
        info["ok"] = False
    return info


@mcp.tool()
async def cpi_status(connection_id: str = "") -> str:
    """Sanity de una conexión de migración: valida tokens OAuth, XSRF y alcance OData
    de AMBOS tenants (origen y destino), y devuelve el conteo de paquetes de cada uno.
    Solo lectura. Úsalo primero para confirmar credenciales antes de cualquier migración."""
    cfg = _resolve_connection(connection_id)
    src = CpiClient(cfg["source"])
    tgt = CpiClient(cfg["target"])
    async with httpx.AsyncClient(verify=SSL_VERIFY, timeout=HTTP_TIMEOUT) as http:
        source = await _tenant_probe(http, src)
        target = await _tenant_probe(http, tgt)
    return _j({"connection": cfg.get("nombre", connection_id),
               "ok": bool(source.get("ok") and target.get("ok")),
               "source": source, "target": target})


@mcp.tool()
async def cpi_list_flows(connection_id: str = "", side: str = "source",
                         package: str = "") -> str:
    """Lista paquetes de integración de un lado (side=source|target), o los iFlows de
    un paquete si se da `package` (su Id técnico). Solo lectura — alimenta el inbox
    del panel y el diagnóstico previo a la migración."""
    side = (side or "source").lower()
    if side not in ("source", "target"):
        return _j({"ok": False, "error": "side debe ser 'source' o 'target'"})
    cfg = _resolve_connection(connection_id)
    client = CpiClient(cfg[side])
    async with httpx.AsyncClient(verify=SSL_VERIFY, timeout=HTTP_TIMEOUT) as http:
        try:
            if package:
                path = f"IntegrationPackages('{package}')/IntegrationDesigntimeArtifacts"
                rows = await client.get(http, path,
                    {"$select": "Id,Name,Version,PackageId"})
                items = [{"id": r.get("Id"), "name": r.get("Name"),
                          "version": r.get("Version")} for r in rows]
                return _j({"ok": True, "side": side, "package": package,
                           "count": len(items), "flows": items})
            rows = await client.get(http, "IntegrationPackages",
                {"$select": "Id,Name,Version,Mode,SupportedPlatform"})
            items = [{"id": r.get("Id"), "name": r.get("Name"),
                      "version": r.get("Version"), "mode": r.get("Mode")}
                     for r in rows]
            return _j({"ok": True, "side": side, "count": len(items), "packages": items})
        except httpx.HTTPStatusError as e:
            return _j({"ok": False, "side": side,
                       "error": f"HTTP {e.response.status_code}",
                       "raw": e.response.text[:500]})
        except Exception as e:  # noqa: BLE001
            return _j({"ok": False, "side": side,
                       "error": f"{type(e).__name__}: {str(e)[:300]}"})


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("SERVER_HOST", "0.0.0.0"),
        port=int(os.environ.get("SERVER_PORT", "8000")),
    )
