"""Servidor MCP `fabrica`: el puente entre Claude Code/Codex LOCAL y la dark
factory para la fase de DESARROLLO (rediseño "repo por requerimiento").

El humano clona el repo del requerimiento (creado por la activity
`provisionar_repo`), lo trabaja localmente, y usa estas herramientas para:

  LECTURA (lo que NO está copiado en el repo, para no duplicar):
    - arquitectura()  -> arquitectura.md del repo de arquitectura
    - estandares()    -> estándares de codificación + stack (dev skill del perfil)
    - diseno()        -> spec.md + assertions.json + test-plan.md (contrato a cumplir)
    - contexto()      -> wp/org/repo/cliente/stack del requerimiento

  COMPUERTA:
    - liberar_a_pruebas(actor, nota) -> firma la compuerta del workflow y manda
      el requerimiento a "Lista para pruebas".

  Entrega (paquete de diseño + código + entregables): por GIT PUSH desde el clon
  local del dev, NO por este MCP. Las viejas tools publicar_diseno /
  publicar_codigo / publicar_version_entregables se eliminaron (subir por API
  desincronizaba el clon local y, en diseño, escribía en OTRO repo que el que el
  dev clonaba). Convención en el repo del requerimiento:
    diseno/spec.md, diseno/assertions.json, diseno/prototipo.html,
    diseno/test-plan.md, diseno/soporte/<doc>     (paquete de diseño)
    code/<objeto>.zip (export abapGit)             (código)
    entregables/<categoria>/<nombre>               (entregables)
  Se publican con `git add diseno/ … && git commit && git push`; git lleva el
  historial. El inbox lee el paquete de `diseno/` del repo del requerimiento.

Transporte: stdio. Lo lanza Claude Code via `docker run -i` (ver el .mcp.json
sembrado en el repo del requerimiento, o el de la raíz del workspace).

Variables de entorno:
  GITEA_BASE_URL              p.ej. http://host.docker.internal:3000
  GITEA_API_TOKEN             token Gitea (read arquitectura + read/write del repo)
  FACTORY_ARCH_REPO           owner/repo del repo de arquitectura (ej. mcp-agent/fabrica-arquitectura)
  FACTORY_ARCH_BRANCH         rama del repo de arquitectura (def. main)
  FACTORY_ARCH_PATH           ruta de arquitectura.md (def. arquitectura.md)
  FACTORY_SKILLS_CATALOG_PATH ruta del catálogo (def. skills/catalog.yaml)
  FACTORY_WEBHOOK_URL         base del webhook de la fábrica (def. http://host.docker.internal:8000)
  FABRICA_WP                  id del work package del requerimiento
  FABRICA_ORG                 org (cliente) del repo del requerimiento
  FABRICA_REPO                nombre del repo del requerimiento
  FABRICA_BRANCH              rama del repo del requerimiento (def. main)
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

GITEA_BASE_URL = os.environ.get("GITEA_BASE_URL", "").rstrip("/")
GITEA_API_TOKEN = os.environ.get("GITEA_API_TOKEN", "")
ARCH_REPO = os.environ.get("FACTORY_ARCH_REPO", "")
ARCH_BRANCH = os.environ.get("FACTORY_ARCH_BRANCH", "main")
ARCH_PATH = os.environ.get("FACTORY_ARCH_PATH", "arquitectura.md")
CATALOG_PATH = os.environ.get("FACTORY_SKILLS_CATALOG_PATH", "skills/catalog.yaml")
WEBHOOK_URL = os.environ.get("FACTORY_WEBHOOK_URL",
                             "http://host.docker.internal:8000").rstrip("/")
# Repo de la FÁBRICA (p.ej. mcp-agent/fabrica): repo interno con las ramas
# `req-N` (sidecars de la maquinaria). Lo provee env_file=.env (mismas vars que
# el orquestador). Lo usa artefactos() como fallback; el paquete de diseño YA NO
# vive aquí (ahora en diseno/ del repo del requerimiento, publicado por git).
FABRICA_OWNER = os.environ.get("FACTORY_GITEA_OWNER", "mcp-agent").strip()
FABRICA_REPO_NAME = os.environ.get("FACTORY_GITEA_REPO", "fabrica").strip()
SPEC_TEMPLATE_PATH = os.environ.get("FACTORY_SPEC_TEMPLATE_PATH",
                                    "spec_template.md")

# Contexto por defecto del requerimiento (uso single-req local por env). En
# modo hosted (servicio compartido) el cliente pasa org/repo/wp por argumento;
# estos quedan como fallback si no se pasan.
WP = os.environ.get("FABRICA_WP", "").strip()
ORG = os.environ.get("FABRICA_ORG", "").strip()
REPO = os.environ.get("FABRICA_REPO", "").strip()
BRANCH = os.environ.get("FABRICA_BRANCH", "main").strip() or "main"

# Transporte y auth — AGNOSTICO AL AMBIENTE (mismo codigo local y AWS, solo
# cambia el .env). stdio = dev local con Claude Code por docker -i; http =
# servicio hosted (detras de Caddy en AWS, o en 127.0.0.1 para probar el flujo
# remoto en local). Con http, si hay FABRICA_MCP_TOKEN se EXIGE Bearer.
TRANSPORT = os.environ.get("FABRICA_MCP_TRANSPORT", "stdio").strip().lower()
HOST = os.environ.get("FABRICA_MCP_HOST", "0.0.0.0").strip()
PORT = int(os.environ.get("FABRICA_MCP_PORT", "8080"))
PATH = os.environ.get("FABRICA_MCP_PATH", "/mcp").strip() or "/mcp"
TOKEN = os.environ.get("FABRICA_MCP_TOKEN", "").strip()

# Read-model de proyecto (solo lectura) para el asistente generativo de estado.
try:
    import asyncpg  # type: ignore
except ImportError:  # noqa: BLE001
    asyncpg = None  # type: ignore
PROY_DSN = os.environ.get("PROYECTO_DB_DSN", "").strip()
_pg_pool = None

if not GITEA_BASE_URL or not GITEA_API_TOKEN:
    sys.exit("Faltan GITEA_BASE_URL / GITEA_API_TOKEN (ver mcp/.env.example).")

mcp = FastMCP("fabrica")

_gitea = httpx.Client(
    base_url=f"{GITEA_BASE_URL}/api/v1",
    headers={"Authorization": f"token {GITEA_API_TOKEN}",
             "Accept": "application/json"},
    timeout=httpx.Timeout(30.0),
)


# --------------------------------------------------------------------------
# Gitea helpers
# --------------------------------------------------------------------------
def _gitea_get_text(owner: str, repo: str, path: str, ref: str) -> str | None:
    """Contenido de texto de un archivo, o None si no existe."""
    r = _gitea.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise ToolError(f"Gitea GET {owner}/{repo}/{path}: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    return base64.b64decode(data["content"]).decode("utf-8", "replace")


def _gitea_get_bytes(owner: str, repo: str, path: str, ref: str) -> bytes | None:
    r = _gitea.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise ToolError(f"Gitea GET (bin) {owner}/{repo}/{path}: HTTP {r.status_code} {r.text[:200]}")
    return base64.b64decode(r.json()["content"])


def _gitea_list(owner: str, repo: str, path: str, ref: str) -> list[dict]:
    r = _gitea.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
    if r.status_code == 404:
        return []
    if r.status_code >= 400:
        raise ToolError(f"Gitea listar {owner}/{repo}/{path}: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    return data if isinstance(data, list) else [data]


def _gitea_put_text(owner: str, repo: str, branch: str, path: str,
                    content: str, message: str) -> dict:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    body = {"content": b64, "message": message, "branch": branch}
    existing = _gitea.get(f"/repos/{owner}/{repo}/contents/{path}",
                          params={"ref": branch})
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
        r = _gitea.put(f"/repos/{owner}/{repo}/contents/{path}", json=body)
    else:
        r = _gitea.post(f"/repos/{owner}/{repo}/contents/{path}", json=body)
    if r.status_code not in (200, 201):
        raise ToolError(f"Gitea escribir {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def _gitea_put_binary(owner: str, repo: str, branch: str, path: str,
                      content_b64: str, message: str) -> dict:
    """Como _gitea_put_text pero el contenido ya viene en base64 (binarios: ZIP).
    Sobreescribe si el archivo existe (git guarda el histórico)."""
    body = {"content": content_b64, "message": message, "branch": branch}
    existing = _gitea.get(f"/repos/{owner}/{repo}/contents/{path}",
                          params={"ref": branch})
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
        r = _gitea.put(f"/repos/{owner}/{repo}/contents/{path}", json=body)
    else:
        r = _gitea.post(f"/repos/{owner}/{repo}/contents/{path}", json=body)
    if r.status_code not in (200, 201):
        raise ToolError(f"Gitea escribir binario {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def _hdr() -> dict:
    """Headers de la request HTTP en curso (modo http), en minúsculas. {} en
    stdio o fuera de contexto de request. Sirve para que el .mcp.json del repo
    inyecte el contexto del requerimiento por conexión (X-Fabrica-*)."""
    try:
        from fastmcp.server.dependencies import get_http_headers
        return {k.lower(): v for k, v in (get_http_headers() or {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def _require_repo(org: str = "", repo: str = "",
                  branch: str = "") -> tuple[str, str, str]:
    """Resuelve (org, repo, branch). Precedencia: argumento explícito > header
    de conexión (X-Fabrica-Org/Repo/Branch) > env (single-req local)."""
    h = _hdr()
    o = (org or h.get("x-fabrica-org") or ORG).strip()
    r = (repo or h.get("x-fabrica-repo") or REPO).strip()
    b = (branch or h.get("x-fabrica-branch") or BRANCH).strip() or "main"
    if not o or not r:
        raise ToolError(
            "No sé sobre qué requerimiento trabajar: pasa org y repo "
            "(o configura el contexto en el .mcp.json del repo / FABRICA_ORG/REPO).")
    return o, r, b


def _require_wp(wp: str | int = "") -> str:
    """Resuelve el work package: argumento > header X-Fabrica-Wp > env FABRICA_WP."""
    w = str(wp or _hdr().get("x-fabrica-wp") or WP).strip()
    if not w or not w.isdigit():
        raise ToolError("Falta el work package (pasa wp o configura el contexto).")
    return w


def _assemble_skill_bundle(data: bytes) -> str:
    """Descomprime un bundle .skill (ZIP) a TEXTO: SKILL.md primero, luego el
    resto de archivos de texto. Ignora binarios."""
    _TEXT_EXT = (".md", ".markdown", ".txt", ".yaml", ".yml", ".json")
    skill_md: str | None = None
    others: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith("/") or not name.lower().endswith(_TEXT_EXT):
                continue
            try:
                content = zf.read(name).decode("utf-8")
            except Exception:  # noqa: BLE001
                continue
            if name.rsplit("/", 1)[-1].lower() == "skill.md":
                skill_md = content
            else:
                others.append((name, content))
    parts = [skill_md] if skill_md else []
    parts += [f"\n\n--- {n} ---\n\n{c}" for n, c in others]
    return "".join(parts).strip()


def _load_profile(org: str = "", repo: str = "", branch: str = "") -> dict:
    """Perfil de stack del requerimiento. Precedencia:
      1) header `X-Fabrica-Stack` (lo hornea el .mcp.json al provisionar; es el
         stack elegido al validar) → se resuelve contra el catálogo.
      2) `diseno/profile.json` del repo (compat con repos previos).
    {} si no hay (los consumidores caen al default_profile del catálogo)."""
    stack = (_hdr().get("x-fabrica-stack") or "").strip()
    if stack:
        cat = _load_catalog()
        profiles = {p.get("key"): p for p in (cat.get("profiles") or [])}
        p = profiles.get(stack)
        if p:
            return {"key": p.get("key"), "title": p.get("title"),
                    "design_skill": p.get("design_skill"),
                    "dev_skill_key": p.get("dev_skill_key")}
    try:
        o, r, b = _require_repo(org, repo, branch)
    except ToolError:
        return {}
    txt = _gitea_get_text(o, r, "diseno/profile.json", b)
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except ValueError:
        return {}


@mcp.tool
def generar_zip(src_path: str = "", out_path: str = "",
                org: str = "", repo: str = "", branch: str = "") -> dict:
    """Genera ON-DEMAND un ZIP del código serializado (backup abapGit) del requerimiento,
    SIN meter payload grande por el MCP. Dos modos:
      - `src_path` vacío  -> devuelve la URL del archive nativo de Gitea (todo el repo,
        zip al vuelo, no se almacena nada).
      - `src_path` (carpeta) -> empaqueta solo ese subárbol y commitea el .zip en
        `out_path` (def: dist/<repo>.zip), devolviendo la URL de descarga.
    Pensado para reusar el backup luego en otro cliente (recrear con aws_abapgit_crear_objeto)."""
    o, r, b = _require_repo(org, repo, branch)
    base = (src_path or "").strip("/")

    if not base:
        # archive nativo: Gitea arma el zip del repo completo al vuelo (sin almacenar)
        return {"ok": True, "repo": f"{o}/{r}", "branch": b, "modo": "archive",
                "download_url": f"{GITEA_BASE_URL}/{o}/{r}/archive/{b}.zip"}

    # subárbol: recorrer recursivo por la API de contenidos y empaquetar
    files: list[tuple[str, bytes]] = []
    stack = [base]
    while stack:
        cur = stack.pop()
        for e in _gitea_list(o, r, cur, b):
            if e.get("type") == "dir":
                stack.append(e["path"])
            elif e.get("type") == "file":
                data = _gitea_get_bytes(o, r, e["path"], b)
                if data is not None:
                    files.append((e["path"], data))
    if not files:
        raise ToolError(f"No hay archivos bajo '{base}' en {o}/{r}@{b}.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files:
            arc = path[len(base):].lstrip("/")
            zf.writestr(arc, data)
    zip_bytes = buf.getvalue()
    out = (out_path or f"dist/{r}.zip").strip("/")
    _gitea_put_binary(o, r, b, out,
                      base64.b64encode(zip_bytes).decode("ascii"),
                      f"zip backup de {base} ({len(files)} archivos)")
    return {"ok": True, "repo": f"{o}/{r}", "branch": b, "modo": "subarbol",
            "src": base, "zip_path": out, "files": len(files), "bytes": len(zip_bytes),
            "download_url": f"{GITEA_BASE_URL}/{o}/{r}/raw/branch/{b}/{out}"}


def _load_catalog() -> dict:
    if not ARCH_REPO:
        return {}
    owner, _, repo = ARCH_REPO.partition("/")
    txt = _gitea_get_text(owner, repo, CATALOG_PATH, ARCH_BRANCH)
    if not txt:
        return {}
    try:
        return yaml.safe_load(txt) or {}
    except yaml.YAMLError:
        return {}


def _dev_skill_path(org: str = "", repo: str = "", branch: str = "") -> str | None:
    """Ruta (en el repo de arquitectura) del dev skill del perfil del
    requerimiento: del profile.json, o del default del catálogo."""
    prof = _load_profile(org, repo, branch)
    cat = _load_catalog()
    skills = {s.get("key"): s for s in (cat.get("skills") or [])}
    dev_key = prof.get("dev_skill_key")
    if not dev_key:
        profiles = {p.get("key"): p for p in (cat.get("profiles") or [])}
        defp = profiles.get(cat.get("default_profile")) or {}
        dev_key = defp.get("dev_skill_key") or cat.get("default")
    sk = skills.get(dev_key)
    return sk.get("path") if sk else None


def _design_skill_path(org: str = "", repo: str = "", branch: str = "") -> str | None:
    """Ruta (en el repo de arquitectura) del DESIGN skill del perfil del
    requerimiento (gobierna spec/prototipo/casos: convenciones de UI del stack).
    Cae al design_skill del perfil default si no se resuelve el del requisito."""
    prof = _load_profile(org, repo, branch)
    cat = _load_catalog()
    profiles = {p.get("key"): p for p in (cat.get("profiles") or [])}
    p = (profiles.get(prof.get("key"))
         or profiles.get(cat.get("default_profile")) or {})
    return p.get("design_skill")


# --------------------------------------------------------------------------
# Tools — LECTURA
# --------------------------------------------------------------------------
@mcp.tool
def contexto(org: str = "", repo: str = "", wp: str = "",
             branch: str = "") -> dict:
    """Contexto del requerimiento en el que se está trabajando: id del work
    package, org (cliente), repo, stack y si es ABAP/SAP. Úsalo primero para
    saber sobre qué estás trabajando. En modo hosted (compartido) pasa
    org/repo/wp; en local single-req se toman del entorno."""
    o, r, b = _require_repo(org, repo, branch)
    prof = _load_profile(o, r, b)
    w = (wp or _hdr().get("x-fabrica-wp") or WP).strip()
    return {
        "work_package": int(w) if w.isdigit() else w,
        "org_cliente": o,
        "repo": r,
        "branch": b,
        "perfil": prof.get("key") or "(default del catálogo)",
        "dev_skill_key": prof.get("dev_skill_key"),
        "abapgit": bool(_gitea_get_text(o, r, ".abapgit.xml", b)),
        # Memoria del requerimiento (handoff): notas/decisiones/gotchas que dejó
        # quien trabajó antes. Se restablece aquí al leer contexto; se escribe con
        # guardar_memoria/anotar_memoria. "" si aún no hay.
        "memoria": _gitea_get_text(o, r, ".claude/memoria.md", b) or "",
    }


@mcp.tool
def guardar_memoria(contenido: str, org: str = "", repo: str = "",
                    branch: str = "") -> dict:
    """Guarda la MEMORIA del requerimiento (notas, decisiones, gotchas, estado,
    pendientes) en su repo, en `.claude/memoria.md`, para que CUALQUIER consultor
    que retome este requerimiento la herede (se restablece al leer `contexto`).
    `contenido` = el documento completo de memoria en Markdown; REEMPLAZA el anterior
    (git conserva el historial). Flujo: lee la memoria actual (de `contexto`),
    actualízala y guárdala completa. Para agregar solo una nota usa `anotar_memoria`."""
    o, r, b = _require_repo(org, repo, branch)
    contenido = contenido or ""
    _gitea_put_text(o, r, b, ".claude/memoria.md", contenido,
                    "memoria del requerimiento (handoff entre consultores)")
    return {"ok": True, "repo": f"{o}/{r}", "branch": b,
            "path": ".claude/memoria.md",
            "bytes": len(contenido.encode("utf-8"))}


@mcp.tool
def anotar_memoria(nota: str, org: str = "", repo: str = "",
                   branch: str = "") -> dict:
    """Agrega una anotación (línea con fecha) a la memoria del requerimiento
    (`.claude/memoria.md`) SIN reescribir todo el documento. Útil para dejar un
    apunte rápido (decisión, pendiente, gotcha) en el handoff. Para reescribir el
    documento completo usa `guardar_memoria`."""
    import datetime
    o, r, b = _require_repo(org, repo, branch)
    nota = (nota or "").strip()
    if not nota:
        raise ToolError("La nota está vacía.")
    prev = _gitea_get_text(o, r, ".claude/memoria.md", b) or "# Memoria del requerimiento\n"
    stamp = datetime.date.today().isoformat()
    nuevo = prev.rstrip() + f"\n- {stamp} — {nota}\n"
    _gitea_put_text(o, r, b, ".claude/memoria.md", nuevo,
                    f"anotación a la memoria del requerimiento ({stamp})")
    return {"ok": True, "repo": f"{o}/{r}", "branch": b,
            "path": ".claude/memoria.md", "agregado": nota}


@mcp.tool
def arquitectura() -> str:
    """Arquitectura objetivo de la fábrica (arquitectura.md del repo de
    arquitectura): principios, capas, restricciones (p.ej. Clean-Core)."""
    if not ARCH_REPO:
        raise ToolError("FACTORY_ARCH_REPO no configurado.")
    owner, _, repo = ARCH_REPO.partition("/")
    txt = _gitea_get_text(owner, repo, ARCH_PATH, ARCH_BRANCH)
    if not txt:
        raise ToolError(f"No se encontró {ARCH_PATH} en {ARCH_REPO}@{ARCH_BRANCH}.")
    return txt


@mcp.tool
def estandares(org: str = "", repo: str = "", branch: str = "") -> str:
    """Estándares de codificación + convenciones del STACK de desarrollo para
    este requerimiento (el 'dev skill' del perfil: ABAP Cloud/RAP, etc.).
    Es la guía a seguir al escribir el código."""
    path = _dev_skill_path(org, repo, branch)
    if not path or not ARCH_REPO:
        raise ToolError("No hay dev skill resoluble (revisa profile.json / catálogo).")
    owner, _, repo = ARCH_REPO.partition("/")
    if path.lower().endswith(".skill"):
        data = _gitea_get_bytes(owner, repo, path, ARCH_BRANCH)
        if not data:
            raise ToolError(f"No se encontró el bundle {path}.")
        return _assemble_skill_bundle(data)
    txt = _gitea_get_text(owner, repo, path, ARCH_BRANCH)
    if not txt:
        raise ToolError(f"No se encontró {path}.")
    return txt


@mcp.tool
def diseno(org: str = "", repo: str = "", branch: str = "") -> dict:
    """Contrato a implementar: spec.md, assertions.json (aseveraciones AS
    verificables) y test-plan.md del requerimiento. Tu código y tus evidencias
    deben cubrir todas las AS."""
    o, r, b = _require_repo(org, repo, branch)
    out: dict[str, Any] = {}
    out["spec_md"] = _gitea_get_text(o, r, "diseno/spec.md", b) or ""
    raw = _gitea_get_text(o, r, "diseno/assertions.json", b)
    try:
        out["assertions"] = json.loads(raw) if raw else None
    except ValueError:
        out["assertions"] = raw
    out["test_plan_md"] = _gitea_get_text(o, r, "diseno/test-plan.md", b) or ""
    return out


@mcp.tool
def guia_diseno(org: str = "", repo: str = "", branch: str = "") -> str:
    """Guía de DISEÑO del stack (design skill del perfil del requerimiento):
    convenciones de la spec, fidelidad de UI del prototipo (Fiori Elements /
    Dynpro), formato de casos de prueba. Síguela al generar el paquete técnico."""
    path = _design_skill_path(org, repo, branch)
    if not path or not ARCH_REPO:
        raise ToolError("No hay design skill resoluble (revisa profile.json / catálogo).")
    owner, _, arepo = ARCH_REPO.partition("/")
    if path.lower().endswith(".skill"):
        data = _gitea_get_bytes(owner, arepo, path, ARCH_BRANCH)
        if not data:
            raise ToolError(f"No se encontró el bundle {path}.")
        return _assemble_skill_bundle(data)
    txt = _gitea_get_text(owner, arepo, path, ARCH_BRANCH)
    if not txt:
        raise ToolError(f"No se encontró {path}.")
    return txt


@mcp.tool
def plantilla_spec() -> str:
    """Plantilla canónica de la ESPECIFICACIÓN (spec_template.md del repo de
    arquitectura): la estructura de secciones que la spec debe seguir."""
    if not ARCH_REPO:
        raise ToolError("FACTORY_ARCH_REPO no configurado.")
    owner, _, arepo = ARCH_REPO.partition("/")
    txt = _gitea_get_text(owner, arepo, SPEC_TEMPLATE_PATH, ARCH_BRANCH)
    if not txt:
        raise ToolError(f"No se encontró {SPEC_TEMPLATE_PATH} en {ARCH_REPO}.")
    return txt


# --------------------------------------------------------------------------
# Tools — ESCRITURA
# --------------------------------------------------------------------------
# NOTA: las antiguas tools de ESCRITURA `publicar_codigo` y
# `publicar_version_entregables` se ELIMINARON a propósito. El código y los
# entregables se publican por **git push** desde el clon local del dev (única
# fuente de verdad), no por la API de Gitea: subir por API creaba commits del
# lado del servidor que dejaban el clon local desincronizado. El MCP `fabrica`
# queda solo de lectura (contexto/arquitectura/estandares/diseno) + compuerta
# (liberar_a_pruebas) + señales de flujo del blog (enviar_a_revision, aprobar…).
# Convención en el repo del requerimiento: paquete de diseño en `diseno/`
# (spec.md, assertions.json, prototipo.html, test-plan.md, soporte/<doc>),
# código en `code/<objeto>.zip` (export abapGit), entregables en
# `entregables/<categoria>/<nombre>`. TODO se publica por git push; git lleva el
# historial. Ver el CLAUDE.md sembrado por provisionar_repo.


@mcp.tool
def liberar_a_pruebas(actor: str, nota: str = "", wp: str = "") -> dict:
    """Libera el requerimiento a PRUEBAS: firma la compuerta del workflow de la
    fábrica y el requerimiento avanza a 'Lista para pruebas'. Hazlo cuando el
    código esté en SAP/repo y los entregables publicados. `actor` = tu nombre."""
    w = _require_wp(wp)
    try:
        r = httpx.post(
            f"{WEBHOOK_URL}/factory/{w}/decision",
            json={"decision": "approved", "actor": actor or "desarrollador",
                  "note": nota or "Liberado a pruebas desde desarrollo local."},
            timeout=httpx.Timeout(30.0))
    except httpx.HTTPError as exc:
        raise ToolError(f"No se pudo contactar la fábrica en {WEBHOOK_URL}: {exc}") from exc
    if r.status_code >= 400:
        raise ToolError(f"Fábrica HTTP {r.status_code}: {r.text[:200]}")
    return {"liberado": True, "work_package": int(w), "respuesta": r.json()}


def _incoming_bearer() -> str:
    """El token del usuario de ESTA petición: header Authorization en modo hosted
    (varios usuarios, cada uno su token); en stdio (single-req) cae al token del
    env. De aquí el webhook resuelve el ACTOR automáticamente."""
    try:
        from fastmcp.server.dependencies import get_http_headers
        auth = (get_http_headers() or {}).get("authorization", "")
        if auth:
            return auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    except Exception:  # noqa: BLE001
        pass
    return TOKEN  # stdio: el token personal viaja por env (FABRICA_MCP_TOKEN)


def _factory_blog(wp: str, accion: str, *, rol: str = "", usuario: str = "",
                  actor: str = "", texto: str = "") -> dict:
    """Postea una acción del blog a la fábrica (modelo dirigido por blog). El
    webhook registra el comentario en proyecto-db, resuelve el ACTOR del token y,
    si dirige el flujo, señala al workflow. Ver [[workflow-dirigido-por-blog]]."""
    headers = {}
    bt = _incoming_bearer()
    if bt:
        headers["Authorization"] = f"Bearer {bt}"
    try:
        r = httpx.post(
            f"{WEBHOOK_URL}/factory/{wp}/blog-action",
            json={"accion": accion, "destino_rol": rol, "destino_usuario": usuario,
                  "actor": actor, "texto": texto},
            headers=headers, timeout=httpx.Timeout(30.0))
    except httpx.HTTPError as exc:
        raise ToolError(f"No se pudo contactar la fábrica en {WEBHOOK_URL}: {exc}") from exc
    if r.status_code >= 400:
        raise ToolError(f"Fábrica HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


@mcp.tool
def enviar_a_revision(rol: str = "funcional", actor: str = "", nota: str = "",
                      wp: str = "") -> dict:
    """Pasa la PELOTA a otro rol para que revise (delegar) — el flujo cambia de
    responsable en el blog. Úsalo tras publicar el paquete de diseño por git
    (`git push` de `diseno/`) para que el consultor funcional o el usuario lo
    revisen. `rol`: funcional | usuario-clave | swf-funcional | project-manager |
    swf-desarrollador. `actor` = tu nombre."""
    w = _require_wp(wp)
    return _factory_blog(str(w), "delegar", rol=rol, actor=actor, texto=nota)


@mcp.tool
def aprobar(actor: str = "", nota: str = "", wp: str = "") -> dict:
    """Aprueba la fase actual y AVANZA a la siguiente. Solo drivers (funcional de
    fábrica/admin en Diseño; desarrollador en Desarrollo/Pruebas). `actor`=tu nombre."""
    w = _require_wp(wp)
    return _factory_blog(str(w), "aprobar", actor=actor, texto=nota)


@mcp.tool
def rechazar(actor: str = "", nota: str = "", wp: str = "") -> dict:
    """Rechaza el requerimiento y TERMINA el flujo. Solo drivers. `actor`=tu nombre."""
    w = _require_wp(wp)
    return _factory_blog(str(w), "rechazar", actor=actor, texto=nota)


@mcp.tool
def devolver(actor: str = "", nota: str = "", wp: str = "") -> dict:
    """Devuelve el requerimiento a la FASE ANTERIOR (p.ej. de Desarrollo a Diseño
    si cambia el contrato/AS — documentos vivos). Solo drivers. `actor`=tu nombre."""
    w = _require_wp(wp)
    return _factory_blog(str(w), "devolver", actor=actor, texto=nota)


@mcp.tool
def comentar(texto: str, actor: str = "", wp: str = "") -> dict:
    """Agrega un comentario al blog del requerimiento, SIN mover el flujo."""
    w = _require_wp(wp)
    if not (texto or "").strip():
        raise ToolError("texto vacío")
    return _factory_blog(str(w), "responder", actor=actor, texto=texto)


# --------------------------------------------------------------------------
# Read-model de proyecto (SOLO LECTURA): para que un asistente generativo
# responda sobre el estado de los requerimientos. Ver [[workflow-dirigido-por-blog]].
# --------------------------------------------------------------------------
async def _pg():
    global _pg_pool
    if not (PROY_DSN and asyncpg):
        raise ToolError("La DB de proyecto no está configurada (PROYECTO_DB_DSN).")
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(PROY_DSN, min_size=1, max_size=4)
    return _pg_pool


def _ser(v):
    import datetime
    import decimal
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


_SQL_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_SQL_BAD = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|do|merge|comment|reindex|vacuum|set\s)\b", re.IGNORECASE)


@mcp.tool
async def esquema_datos() -> str:
    """Describe el read-model de proyecto (tablas/vistas + columnas + semántica)
    para que escribas consultas con `consulta(sql)`. Léelo PRIMERO."""
    return (
        "BASE DE PROYECTO (read-model, Postgres). Consulta con `consulta(sql)` "
        "(solo SELECT). Vista principal:\n\n"
        "v_requisito_panel (una fila por requerimiento):\n"
        "  id, wp_id, cliente, proyecto, titulo, stack, talla, prioridad,\n"
        "  prioridad_label, prioridad_orden (1=urgente..4=baja),\n"
        "  horas_estimadas (del plan o camisas), horas_plan, inicio_plan, fin_plan,\n"
        "  horas_trabajo, horas_espera, tiempo_invertido (horas LABORALES),\n"
        "  etapa_actual, workflow_iniciado (bool), avance_pct,\n"
        "  pelota_rol, pelota_usuario, pelota_desde, comentarios (conteo),\n"
        "  repo_org, repo_name, repo_branch.\n\n"
        "Tablas de apoyo (JOIN por id):\n"
        "  requisito(id, wp_id, proyecto_id, capacidad_id, titulo, etapa_actual, ...)\n"
        "  capacidad(id, proyecto_id, nombre)  -- para 'por capacidad': "
        "JOIN requisito r ON r.id=vp.id JOIN capacidad c ON c.id=r.capacidad_id\n"
        "  proyecto(id, cliente_id, nombre), cliente(id, nombre, slug)\n"
        "  etapa(codigo, label, orden)  -- orden: recepcion1<diseno2<desarrollo3<"
        "pruebas_funcionales4<pruebas_usuario5<cerrado6\n"
        "  fase(codigo, hub_rol, avanza_a, devuelve_a)\n"
        "  tiempo_etapa(requisito_id, etapa, entrada, salida, horas_laborales, "
        "es_espera)  -- timeline real por etapa; salida NULL = etapa en curso\n"
        "  comentario(requisito_id, autor, cuerpo, accion, etapa, created_at)  -- blog\n"
        "  requisito_relacion(origen_id, destino_id, tipo, hereda_repo)  -- "
        "dependencias/CR (para ruta crítica)\n"
        "  subtarea(requisito_id, tipo front/back, stack, talla)\n\n"
        "SEMÁNTICA útil:\n"
        "  - ATRASADO: fin_plan IS NOT NULL AND fin_plan < CURRENT_DATE AND "
        "etapa_actual <> 'cerrado'.\n"
        "  - EN TIEMPO: lo contrario (o sin fin_plan).\n"
        "  - SIN INICIAR: workflow_iniciado = false.\n"
        "  - DE QUIÉN ES LA PELOTA: pelota_usuario (persona) o, si NULL, "
        "pelota_rol (en el POOL del rol, nadie la ha tomado).\n"
        "  - Roles: swf-funcional, swf-desarrollador, funcional, usuario-clave, "
        "swf-tester, project-manager, admin.\n"
        "  - Para inputs/diseño/código de un requerimiento usa la tool "
        "`artefactos(wp)` (eso vive en Gitea, no en la DB)."
    )


@mcp.tool
async def consulta(sql: str, limite: int = 200) -> dict:
    """Ejecuta UNA consulta SQL de SOLO LECTURA (SELECT/WITH) sobre el read-model
    de proyecto y devuelve las filas. Úsalo para responder lo que sea: en tiempo /
    atrasados, por proyecto / por capacidad / por requerimiento, de quién es la
    pelota, en qué etapa están, ruta crítica, etc. Corre `esquema_datos()` antes.
    Cap ~200 filas (param `limite`, máx 1000). No accede a tokens."""
    s = (sql or "").strip().rstrip(";")
    if not s or not _SQL_OK.match(s) or ";" in s or _SQL_BAD.search(s):
        raise ToolError("Solo se permite UNA consulta SELECT/WITH de lectura.")
    if "mcp_token" in s.lower():
        raise ToolError("Tabla no permitida.")
    lim = max(1, min(int(limite or 200), 1000))
    pool = await _pg()
    async with pool.acquire() as c:
        async with c.transaction(readonly=True):
            await c.execute("SET LOCAL statement_timeout = '8s'")
            try:
                rows = await c.fetch(f"SELECT * FROM ({s}) _q LIMIT {lim}")
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"SQL inválido: {str(exc)[:200]}") from exc
    filas = [{k: _ser(v) for k, v in r.items()} for r in rows]
    return {"n": len(filas), "filas": filas}


@mcp.tool
async def tablero(cliente: str = "", proyecto: str = "", capacidad: str = "",
                  etapa: str = "", solo_atrasados: bool = False,
                  limite: int = 100) -> dict:
    """Lista requerimientos con su estado (etapa, pelota, fechas plan, estimado,
    tiempo invertido, avance, prioridad, atraso) filtrable por cliente/proyecto/
    capacidad/etapa y `solo_atrasados`. Para algo más libre, usa `consulta(sql)`."""
    conds, args = [], []

    def add(expr: str, val):
        args.append(val)
        conds.append(expr.format(len(args)))

    if cliente:
        add("vp.cliente ILIKE ${}", f"%{cliente}%")
    if proyecto:
        add("vp.proyecto ILIKE ${}", f"%{proyecto}%")
    if capacidad:
        add("cap.nombre ILIKE ${}", f"%{capacidad}%")
    if etapa:
        add("vp.etapa_actual = ${}", etapa)
    if solo_atrasados:
        conds.append("vp.fin_plan IS NOT NULL AND vp.fin_plan < CURRENT_DATE "
                     "AND vp.etapa_actual <> 'cerrado'")
    where = (" WHERE " + " AND ".join(f"({c})" for c in conds)) if conds else ""
    lim = max(1, min(int(limite or 100), 500))
    q = (
        "SELECT vp.wp_id, vp.cliente, vp.proyecto, cap.nombre AS capacidad, "
        "vp.titulo, vp.prioridad, vp.etapa_actual, vp.workflow_iniciado, "
        "vp.pelota_rol, vp.pelota_usuario, vp.pelota_desde, vp.horas_estimadas, "
        "vp.tiempo_invertido, vp.avance_pct, vp.inicio_plan, vp.fin_plan, "
        "(vp.fin_plan IS NOT NULL AND vp.fin_plan < CURRENT_DATE AND "
        "vp.etapa_actual <> 'cerrado') AS atrasado "
        "FROM v_requisito_panel vp JOIN requisito r ON r.id = vp.id "
        "LEFT JOIN capacidad cap ON cap.id = r.capacidad_id"
        + where +
        " ORDER BY vp.prioridad_orden, vp.fin_plan NULLS LAST LIMIT " + str(lim))
    pool = await _pg()
    async with pool.acquire() as c:
        rows = await c.fetch(q, *args)
    return {"n": len(rows), "filas": [{k: _ser(v) for k, v in r.items()}
                                      for r in rows]}


@mcp.tool
def artefactos(wp: str = "") -> dict:
    """Qué tiene el requerimiento en Gitea: INPUTS (cuáles), DISEÑO (spec/
    assertions/prototipo/casos) y CÓDIGO (repo del cliente). Para responder
    'ya tiene inputs/diseño/código'."""
    w = _require_wp(wp)
    branch = f"req-{w}"
    o, r = FABRICA_OWNER, FABRICA_REPO_NAME

    def _names(path: str) -> list[str]:
        return [it.get("name") for it in _gitea_list(o, r, path, branch)
                if it.get("type") == "file"]

    inputs = _names("inputs")
    diseno = {
        "spec": _gitea_get_text(o, r, "especificacion/spec.md", branch) is not None,
        "assertions": _gitea_get_text(o, r, "especificacion/assertions.json", branch) is not None,
        "prototipo": _gitea_get_text(o, r, "prototipo/prototipo.html", branch) is not None,
        "casos_prueba": _gitea_get_text(o, r, "test-plan/test-plan.md", branch) is not None,
        "soporte": _names("diseno-soporte"),
    }
    # Código: el repo del requerimiento (org del cliente), resuelto por contexto
    # (.mcp.json del repo). El código es el ZIP de abapGit en code/<...>.zip; las
    # versiones están en el historial de git (no en carpetas vN).
    codigo = {"repo": None, "zips": [], "entregables": []}
    try:
        dorg, drepo, dbranch = _require_repo("", "", "")
        codigo["repo"] = f"{dorg}/{drepo}"
        try:
            codigo["zips"] = [x.get("name") for x in
                              _gitea_list(dorg, drepo, "code", dbranch)
                              if x.get("type") == "file"
                              and x.get("name", "").lower().endswith(".zip")]
        except Exception:  # noqa: BLE001
            pass
        try:
            codigo["entregables"] = [x.get("name") for x in
                                     _gitea_list(dorg, drepo, "entregables", dbranch)
                                     if x.get("type") == "dir"]
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return {
        "work_package": int(w),
        "inputs": inputs, "tiene_inputs": bool(inputs),
        "diseno": diseno,
        "tiene_diseno": diseno["spec"] or diseno["prototipo"] or diseno["casos_prueba"],
        "codigo": codigo,
        "tiene_codigo": bool(codigo["repo"] and codigo["zips"]),
    }


# --------------------------------------------------------------------------
# Arranque: transporte seleccionable por env (mismo código local y AWS).
#   stdio  -> Claude Code lo lanza por `docker run -i` (dev local single-req).
#   http   -> servicio hosted (Caddy en AWS, o 127.0.0.1 para probar remoto).
#            Si hay FABRICA_MCP_TOKEN, se EXIGE 'Authorization: Bearer <token>'.
# --------------------------------------------------------------------------
def _bearer_middleware():
    """Middleware Starlette que exige un bearer válido: el TOKEN compartido
    (admin/legacy) O un token PERSONAL de usuario (validado contra la fábrica vía
    /factory/mcp-validate, con caché TTL). Así varios funcionales/desarrolladores
    usan su propio token. La raíz/health se deja pasar."""
    import time
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    cache: dict[str, float] = {}   # token -> expiry epoch (solo tokens OK)
    TTL = 300.0

    async def _valido(tok: str) -> bool:
        if not tok:
            return False
        if TOKEN and tok == TOKEN:
            return True
        now = time.time()
        exp = cache.get(tok)
        if exp and exp > now:
            return True
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as cli:
                r = await cli.post(f"{WEBHOOK_URL}/factory/mcp-validate",
                                   headers={"Authorization": f"Bearer {tok}"})
            if r.status_code == 200:
                cache[tok] = now + TTL
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    class _Bearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path.rstrip("/") in ("", "/health"):
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not await _valido(tok):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    return _Bearer


# --------------------------------------------------------------------------
# VPN del cliente (openfortivpn corre en el HOST). El MCP está en un contenedor
# sin privilegios, así que ejecuta `vpnctl.sh` EN EL HOST por SSM (el contenedor
# toma el rol de instancia por IMDS; el rol tiene ssm:SendCommand acotado).
# --------------------------------------------------------------------------
_AWS_REGION = (os.environ.get("AWS_REGION")
               or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
_VPNCTL = "/opt/fabrica/infra/scripts/vpn/vpnctl.sh"
_SISTEMA_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_OTP_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")


def _vpn_sistema(sistema: str = "") -> str:
    """Nombre de la VPN del cliente (clave SSM /fabrica-sw/vpn/<S>). Default: el
    cliente del repo (X-Fabrica-Org) en MAYÚSCULAS."""
    s = (sistema or _hdr().get("x-fabrica-org") or ORG or "").strip().upper()
    if not s or not _SISTEMA_RE.match(s):
        raise ToolError("Pasa 'sistema' válido (la VPN del cliente, p.ej. TAILOY).")
    return s


def _imds_instance_id() -> str:
    import urllib.request as u
    try:
        tok = u.urlopen(u.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}),
            timeout=3).read().decode()
        return u.urlopen(u.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": tok}),
            timeout=3).read().decode().strip()
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"No pude obtener el instance-id por IMDS: {e}")


def _ssm_run(commands: list[str], timeout: int = 60) -> str:
    """Corre comandos en el HOST por SSM (AWS-RunShellScript) y devuelve la salida."""
    import time
    try:
        import boto3
    except ImportError:
        raise ToolError("boto3 no está instalado en el MCP fabrica.")
    iid = _imds_instance_id()
    ssm = boto3.client("ssm", region_name=_AWS_REGION)
    cid = ssm.send_command(
        InstanceIds=[iid], DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands})["Command"]["CommandId"]
    waited = 0
    while waited < timeout:
        time.sleep(2); waited += 2
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except Exception:  # noqa: BLE001 (InvocationDoesNotExist al inicio)
            continue
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return ((inv.get("StandardOutputContent") or "")
                    + (inv.get("StandardErrorContent") or "")).strip()
    raise ToolError("timeout esperando el comando en el host")


@mcp.tool
def vpn_status(sistema: str = "") -> str:
    """¿Está arriba el túnel VPN del cliente? Hazlo ANTES de crear objetos ABAP.
    `sistema` = la VPN (p.ej. TAILOY); si se omite, usa el cliente del repo."""
    return _ssm_run([f"bash {_VPNCTL} status {_vpn_sistema(sistema)}"], timeout=30)


@mcp.tool
def vpn_connect(sistema: str = "") -> str:
    """Levanta el túnel VPN del cliente si no está arriba. Si la VPN usa OTP,
    dispara el código por correo y queda esperando `vpn_token(code, sistema)`.
    Si la VPN NO usa OTP (otp=false en su config SSM), sube directo y NO pide
    código. Si ya está arriba, no hace nada."""
    s = _vpn_sistema(sistema)
    out = _ssm_run([f"bash {_VPNCTL} status {s}"], timeout=30)
    if out.startswith("UP"):
        return f"Ya está arriba: {out}"
    res = _ssm_run([f"bash {_VPNCTL} up {s}"], timeout=60)
    # VPN sin OTP: vpnctl ya dejó el túnel arriba y devuelve "UP system=..."
    if "UP system=" in res:
        return res
    # VPN con OTP: quedó esperando el código del correo.
    return (f"{res}\n\n→ Revisa el correo: llegó un código OTP. Pásamelo y llamo "
            f"vpn_token(code, '{s}').")


@mcp.tool
def vpn_token(code: str, sistema: str = "") -> str:
    """Entrega el código OTP (del correo) para completar la conexión iniciada con
    `vpn_connect`. Deja el túnel arriba y agrega las rutas al SAP del cliente."""
    s = _vpn_sistema(sistema)
    c = (code or "").strip()
    if not _OTP_RE.match(c):
        raise ToolError("Código OTP inválido (4-12 alfanuméricos).")
    return _ssm_run([f"bash {_VPNCTL} otp {s} {c}"], timeout=45)


if __name__ == "__main__":
    if TRANSPORT == "stdio":
        mcp.run()
    else:
        from starlette.middleware import Middleware
        # SIEMPRE exigir bearer en http: el middleware valida el token PERSONAL
        # contra la fábrica (/factory/mcp-validate), no depende del TOKEN
        # compartido (que ya no se usa). Antes era `if TOKEN` → al vaciar el
        # compartido el MCP quedaba ABIERTO. Ver [[unificacion-auth-mcp-por-token-personal]].
        mw = [Middleware(_bearer_middleware())]
        # transport 'http' = streamable-http. Bind configurable; auth por token.
        mcp.run(transport="http", host=HOST, port=PORT, path=PATH, middleware=mw)
