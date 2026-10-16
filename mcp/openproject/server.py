"""Servidor MCP para OpenProject Community Edition.

Expone un subconjunto curado de la API v3 de OpenProject como herramientas
MCP, de modo que Claude Code pueda gestionar proyectos, work packages y
seguimiento de tiempo de forma conversacional.

Autenticacion: HTTP Basic con usuario `apikey` y el token de API como
contrasena. Es la API v3 publica, asi que funciona en la Community Edition
(el MCP server oficial de OpenProject es un add-on Enterprise; este no).

Transporte: stdio. Lo lanza Claude Code via `docker run -i` (ver .mcp.json).

Configuracion por variables de entorno:
  OPENPROJECT_BASE_URL   p. ej. http://host.docker.internal:8080
  OPENPROJECT_API_TOKEN  token de API del usuario de servicio
  OPENPROJECT_NOTIFY     "true"/"false" (def. false): enviar notificaciones
                         por correo al crear/editar work packages.
  OPENPROJECT_HOST_HEADER  opcional. Cabecera Host a enviar. OpenProject
                         valida el Host contra su ajuste host_name; en local
                         se alcanza por host.docker.internal pero la instancia
                         espera "localhost:8080", asi que se fuerza aqui. En
                         AWS la URL ya coincide con host_name: dejar vacio.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

BASE_URL = os.environ.get("OPENPROJECT_BASE_URL", "").rstrip("/")
API_TOKEN = os.environ.get("OPENPROJECT_API_TOKEN", "")
NOTIFY = os.environ.get("OPENPROJECT_NOTIFY", "false").strip().lower() in (
    "1", "true", "yes", "si",
)
HOST_HEADER = os.environ.get("OPENPROJECT_HOST_HEADER", "").strip()

if not BASE_URL or not API_TOKEN:
    sys.exit(
        "Faltan variables de entorno: define OPENPROJECT_BASE_URL y "
        "OPENPROJECT_API_TOKEN (ver mcp/.env.example)."
    )

API = f"{BASE_URL}/api/v3"

mcp = FastMCP("openproject")

# La API v3 usa Basic auth con el usuario literal 'apikey' y el token como
# contrasena. Un unico cliente reutilizado durante toda la vida del proceso.
_headers = {"Accept": "application/json"}
if HOST_HEADER:
    _headers["Host"] = HOST_HEADER
_client = httpx.Client(
    base_url=API,
    auth=("apikey", API_TOKEN),
    headers=_headers,
    timeout=httpx.Timeout(30.0),
)

# Cache de catalogos (tipos / estados) para resolver nombres -> id.
_cache: dict[str, list[dict]] = {}


# --------------------------------------------------------------------------
# Utilidades internas
# --------------------------------------------------------------------------
def _href_id(href: str | None) -> int | None:
    """Extrae el id numerico del final de un href tipo /api/v3/types/3."""
    if not href:
        return None
    tail = href.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _link(obj: dict, name: str) -> dict | None:
    """Devuelve {id, title} de un _links.<name>, o None si no existe."""
    link = (obj.get("_links") or {}).get(name) or {}
    href = link.get("href")
    if not href:
        return None
    return {"id": _href_id(href), "title": link.get("title")}


def _format_error(status: int, data: Any, text: str) -> str:
    """Construye un mensaje legible a partir del cuerpo de error de la API."""
    if isinstance(data, dict):
        msg = data.get("message")
        embedded = (data.get("_embedded") or {}).get("errors")
        if embedded:
            extra = "; ".join(
                e.get("message", "") for e in embedded if isinstance(e, dict)
            )
            msg = f"{msg or ''} ({extra})".strip()
        if msg:
            return f"OpenProject HTTP {status}: {msg}"
    return f"OpenProject HTTP {status}: {text[:300]}"


def _api(method: str, path: str, *, params: dict | None = None,
         body: dict | None = None) -> dict:
    """Llama a la API v3 y devuelve el JSON, o lanza ToolError legible."""
    try:
        resp = _client.request(method, path, params=params, json=body)
    except httpx.HTTPError as exc:
        raise ToolError(
            f"No se pudo contactar OpenProject en {API}: {exc}"
        ) from exc
    if resp.status_code == 204:
        return {}
    try:
        data: Any = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        raise ToolError(_format_error(resp.status_code, data, resp.text))
    return data if isinstance(data, dict) else {"_raw": data}


def _collection(path: str, params: dict | None = None) -> list[dict]:
    """Devuelve los elementos de una Collection de la API v3."""
    data = _api("GET", path, params=params)
    return (data.get("_embedded") or {}).get("elements", [])


def _resolve(kind: str, value: str | int | None) -> int | None:
    """Resuelve un id a partir de un id o un nombre. kind: 'types'|'statuses'."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    if kind not in _cache:
        _cache[kind] = _collection(f"/{kind}")
    for el in _cache[kind]:
        if str(el.get("name", "")).lower() == text.lower():
            return el.get("id")
    opciones = ", ".join(str(el.get("name")) for el in _cache[kind])
    raise ToolError(f"No existe {kind[:-1]} '{value}'. Opciones: {opciones}")


def _wp_summary(wp: dict) -> dict:
    """Compacta un work package a los campos utiles (menos tokens)."""
    desc = wp.get("description") or {}
    return {
        "id": wp.get("id"),
        "subject": wp.get("subject"),
        "type": _link(wp, "type"),
        "status": _link(wp, "status"),
        "priority": _link(wp, "priority"),
        "project": _link(wp, "project"),
        "assignee": _link(wp, "assignee"),
        "author": _link(wp, "author"),
        "parent": _link(wp, "parent"),
        "percentageDone": wp.get("percentageDone"),
        "startDate": wp.get("startDate"),
        "dueDate": wp.get("dueDate"),
        "lockVersion": wp.get("lockVersion"),
        "createdAt": wp.get("createdAt"),
        "updatedAt": wp.get("updatedAt"),
        "description": desc.get("raw"),
    }


def _project_summary(p: dict) -> dict:
    """Compacta un proyecto a los campos utiles."""
    desc = p.get("description") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "identifier": p.get("identifier"),
        "active": p.get("active"),
        "public": p.get("public"),
        "parent": _link(p, "parent"),
        "description": desc.get("raw"),
        "createdAt": p.get("createdAt"),
    }


def _iso_duration(hours: float) -> str:
    """Convierte horas decimales a una duracion ISO-8601 (1.5 -> PT1H30M)."""
    minutes = round(hours * 60)
    h, m = divmod(minutes, 60)
    out = "PT"
    if h:
        out += f"{h}H"
    if m or not h:
        out += f"{m}M"
    return out


# --------------------------------------------------------------------------
# Herramientas MCP
# --------------------------------------------------------------------------
@mcp.tool
def whoami() -> dict:
    """Devuelve el usuario autenticado. Verifica el token y la conectividad."""
    u = _api("GET", "/users/me")
    return {
        "id": u.get("id"),
        "name": u.get("name"),
        "login": u.get("login"),
        "email": u.get("email"),
        "admin": u.get("admin"),
        "status": u.get("status"),
        "instance": BASE_URL,
    }


@mcp.tool
def list_projects(active_only: bool = True, page_size: int = 100) -> dict:
    """Lista los proyectos visibles para el usuario de servicio.

    active_only: si es True, omite los proyectos archivados.
    """
    params: dict[str, Any] = {"pageSize": page_size}
    if active_only:
        params["filters"] = json.dumps(
            [{"active": {"operator": "=", "values": ["t"]}}]
        )
    data = _api("GET", "/projects", params=params)
    elements = (data.get("_embedded") or {}).get("elements", [])
    return {
        "total": data.get("total"),
        "count": len(elements),
        "projects": [_project_summary(p) for p in elements],
    }


@mcp.tool
def get_project(project_id: int) -> dict:
    """Devuelve el detalle de un proyecto por id."""
    return _project_summary(_api("GET", f"/projects/{project_id}"))


@mcp.tool
def create_project(name: str, identifier: str | None = None,
                   description: str | None = None,
                   parent_id: int | None = None,
                   public: bool = False) -> dict:
    """Crea un proyecto nuevo.

    identifier: slug unico (minusculas y guiones). Si se omite, OpenProject
        lo deriva del nombre.
    parent_id: id del proyecto padre, para crear un subproyecto.
    public: si el proyecto es visible sin membresia.
    """
    body: dict[str, Any] = {"name": name, "public": public}
    if identifier:
        body["identifier"] = identifier
    if description:
        body["description"] = {"format": "markdown", "raw": description}
    if parent_id:
        body["_links"] = {"parent": {"href": f"/api/v3/projects/{parent_id}"}}
    return _project_summary(_api("POST", "/projects", body=body))


@mcp.tool
def list_types() -> list[dict]:
    """Lista los tipos de work package (Task, Requisito, Milestone, ...)."""
    return [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "isMilestone": t.get("isMilestone"),
            "isDefault": t.get("isDefault"),
        }
        for t in _collection("/types")
    ]


@mcp.tool
def list_statuses() -> list[dict]:
    """Lista los estados de work package disponibles en la instancia."""
    return [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "isClosed": s.get("isClosed"),
            "isDefault": s.get("isDefault"),
        }
        for s in _collection("/statuses")
    ]


@mcp.tool
def list_users(project_id: int | None = None, page_size: int = 100) -> list[dict]:
    """Lista usuarios y grupos visibles (principals).

    Util para obtener ids de asignacion. Si se indica project_id, se limita a
    los miembros de ese proyecto (recomendado para un usuario de servicio no
    administrador).
    """
    params: dict[str, Any] = {"pageSize": page_size}
    if project_id:
        params["filters"] = json.dumps(
            [{"member": {"operator": "=", "values": [str(project_id)]}}]
        )
    return [
        {
            "id": u.get("id"),
            "name": u.get("name"),
            "login": u.get("login"),
            "email": u.get("email"),
            "type": u.get("_type"),
            "status": u.get("status"),
        }
        for u in _collection("/principals", params)
    ]


@mcp.tool
def list_work_packages(project_id: int | None = None,
                       status: str | None = None,
                       type: str | None = None,
                       assigned_to_me: bool = False,
                       query: str | None = None,
                       page_size: int = 50,
                       offset: int = 1) -> dict:
    """Lista work packages, con filtros opcionales.

    project_id: limita a un proyecto.
    status / type: nombre o id; filtra por ese estado / tipo.
    assigned_to_me: solo los asignados al usuario de servicio.
    query: texto a buscar en asunto y descripcion.
    offset: numero de pagina (empieza en 1).
    Devuelve TODOS los estados (incluidos los cerrados) salvo que filtres.
    """
    filters: list[dict] = []
    if status:
        filters.append({"status": {"operator": "=",
                                    "values": [str(_resolve("statuses", status))]}})
    if type:
        filters.append({"type": {"operator": "=",
                                  "values": [str(_resolve("types", type))]}})
    if assigned_to_me:
        filters.append({"assignee": {"operator": "=", "values": ["me"]}})
    if query:
        filters.append({"search": {"operator": "**", "values": [query]}})
    path = (f"/projects/{project_id}/work_packages"
            if project_id else "/work_packages")
    params = {
        "filters": json.dumps(filters),
        "pageSize": page_size,
        "offset": offset,
        "sortBy": json.dumps([["updatedAt", "desc"]]),
    }
    data = _api("GET", path, params=params)
    elements = (data.get("_embedded") or {}).get("elements", [])
    return {
        "total": data.get("total"),
        "count": len(elements),
        "offset": offset,
        "pageSize": page_size,
        "workPackages": [_wp_summary(wp) for wp in elements],
    }


@mcp.tool
def get_work_package(work_package_id: int) -> dict:
    """Devuelve el detalle de un work package, incluido su lockVersion."""
    return _wp_summary(_api("GET", f"/work_packages/{work_package_id}"))


@mcp.tool
def create_work_package(project_id: int, subject: str, type: str,
                        description: str | None = None,
                        status: str | None = None,
                        assignee_id: int | None = None,
                        parent_id: int | None = None,
                        start_date: str | None = None,
                        due_date: str | None = None) -> dict:
    """Crea un work package en un proyecto.

    type: nombre o id del tipo (p. ej. 'Requisito', 'Task'). Obligatorio.
    status: nombre o id; si se omite, OpenProject usa el estado inicial.
    assignee_id: id de usuario (ver list_users).
    parent_id: id del work package padre, para crear un hijo.
    start_date / due_date: formato YYYY-MM-DD.
    """
    links: dict[str, Any] = {
        "type": {"href": f"/api/v3/types/{_resolve('types', type)}"}
    }
    if status:
        links["status"] = {
            "href": f"/api/v3/statuses/{_resolve('statuses', status)}"
        }
    if assignee_id:
        links["assignee"] = {"href": f"/api/v3/users/{assignee_id}"}
    if parent_id:
        links["parent"] = {"href": f"/api/v3/work_packages/{parent_id}"}
    body: dict[str, Any] = {"subject": subject, "_links": links}
    if description:
        body["description"] = {"format": "markdown", "raw": description}
    if start_date:
        body["startDate"] = start_date
    if due_date:
        body["dueDate"] = due_date
    wp = _api("POST", f"/projects/{project_id}/work_packages",
              params={"notify": str(NOTIFY).lower()}, body=body)
    return _wp_summary(wp)


@mcp.tool
def update_work_package(work_package_id: int,
                        subject: str | None = None,
                        description: str | None = None,
                        status: str | None = None,
                        type: str | None = None,
                        assignee_id: int | None = None,
                        percentage_done: int | None = None,
                        start_date: str | None = None,
                        due_date: str | None = None) -> dict:
    """Actualiza un work package. Solo cambia los campos que indiques.

    El lockVersion se obtiene automaticamente antes de aplicar el cambio.
    Cambiar 'status' equivale a una transicion de workflow: OpenProject la
    rechazara si el rol del usuario de servicio no la permite.
    """
    current = _api("GET", f"/work_packages/{work_package_id}")
    body: dict[str, Any] = {"lockVersion": current.get("lockVersion")}
    links: dict[str, Any] = {}
    if subject is not None:
        body["subject"] = subject
    if description is not None:
        body["description"] = {"format": "markdown", "raw": description}
    if percentage_done is not None:
        body["percentageDone"] = percentage_done
    if start_date is not None:
        body["startDate"] = start_date
    if due_date is not None:
        body["dueDate"] = due_date
    if status:
        links["status"] = {
            "href": f"/api/v3/statuses/{_resolve('statuses', status)}"
        }
    if type:
        links["type"] = {"href": f"/api/v3/types/{_resolve('types', type)}"}
    if assignee_id is not None:
        links["assignee"] = (
            {"href": f"/api/v3/users/{assignee_id}"} if assignee_id else None
        )
    if links:
        body["_links"] = links
    wp = _api("PATCH", f"/work_packages/{work_package_id}",
              params={"notify": str(NOTIFY).lower()}, body=body)
    return _wp_summary(wp)


@mcp.tool
def add_comment(work_package_id: int, comment: str) -> dict:
    """Anade un comentario (nota de actividad) a un work package."""
    data = _api("POST", f"/work_packages/{work_package_id}/activities",
                body={"comment": {"raw": comment}})
    return {
        "id": data.get("id"),
        "workPackageId": work_package_id,
        "comment": (data.get("comment") or {}).get("raw"),
        "createdAt": data.get("createdAt"),
    }


@mcp.tool
def list_time_entries(work_package_id: int | None = None,
                      project_id: int | None = None,
                      page_size: int = 100) -> dict:
    """Lista registros de tiempo, filtrables por work package o proyecto."""
    filters: list[dict] = []
    if work_package_id:
        filters.append({"workPackage": {"operator": "=",
                                         "values": [str(work_package_id)]}})
    if project_id:
        filters.append({"project": {"operator": "=",
                                     "values": [str(project_id)]}})
    params: dict[str, Any] = {"pageSize": page_size}
    if filters:
        params["filters"] = json.dumps(filters)
    data = _api("GET", "/time_entries", params=params)
    elements = (data.get("_embedded") or {}).get("elements", [])
    return {
        "total": data.get("total"),
        "count": len(elements),
        "timeEntries": [
            {
                "id": e.get("id"),
                "hours": e.get("hours"),
                "spentOn": e.get("spentOn"),
                "comment": (e.get("comment") or {}).get("raw"),
                "workPackage": _link(e, "workPackage"),
                "project": _link(e, "project"),
                "user": _link(e, "user"),
                "activity": _link(e, "activity"),
            }
            for e in elements
        ],
    }


@mcp.tool
def log_time(work_package_id: int, hours: float,
             comment: str | None = None,
             spent_on: str | None = None,
             activity_id: int | None = None) -> dict:
    """Registra tiempo sobre un work package.

    hours: horas en decimal (1.5 = 1h30m).
    spent_on: fecha YYYY-MM-DD; por defecto, la fecha del servidor.
    activity_id: id de actividad de tiempo; si se omite, OpenProject usa la
        actividad marcada como predeterminada en la instancia.
    """
    body: dict[str, Any] = {
        "hours": _iso_duration(hours),
        "_links": {
            "workPackage": {"href": f"/api/v3/work_packages/{work_package_id}"}
        },
    }
    if spent_on:
        body["spentOn"] = spent_on
    if comment:
        body["comment"] = {"raw": comment}
    if activity_id:
        body["_links"]["activity"] = {
            "href": f"/api/v3/time_entries/activities/{activity_id}"
        }
    data = _api("POST", "/time_entries", body=body)
    return {
        "id": data.get("id"),
        "hours": data.get("hours"),
        "spentOn": data.get("spentOn"),
        "comment": (data.get("comment") or {}).get("raw"),
        "workPackage": _link(data, "workPackage"),
        "activity": _link(data, "activity"),
    }


if __name__ == "__main__":
    mcp.run()
