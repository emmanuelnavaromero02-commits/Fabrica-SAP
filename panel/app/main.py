"""Panel de Control de la Fábrica — esqueleto (FASE 1).

Solo el shell con SSO + restricción de acceso + navegación. Las secciones
muestran 'Próximamente' por ahora.

Próximas iteraciones:
  - Métricas (tokens consumidos + costo USD por WP/etapa/modelo)
  - Usuarios (CRUD vs Keycloak Admin API + segmentación proyecto-db + Gitea)
  - Configuración (editar pipeline.yaml, templates, arquitectura.md)
  - Auditoría (acciones recientes en la fábrica)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import auth
import clients
import config
import kc_admin
import proyecto_db

# Orden canónico de las 7 etapas de negocio (para "qué falta").
_ETAPAS_ORDER = [
    ("recibido", "📥 Recibido"), ("generando", "🤖 Generando"),
    ("revision_fabrica", "🔍 Revisión Fábrica"),
    ("validacion_cliente", "👥 Validación Cliente"),
    ("desarrollo", "🛠 Desarrollo"), ("pruebas", "🧪 Pruebas"),
    ("cerrado", "✅ Cerrado"),
]


def _hours_between(a: str, b: str) -> float:
    try:
        return round((datetime.fromisoformat(b) - datetime.fromisoformat(a))
                     .total_seconds() / 3600.0, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _analyze_timeline(tl: list[dict]) -> dict:
    """De timeline.json [{etapa,etapa_key,ts}] saca: duración por etapa, etapa
    actual, horas en la etapa actual y qué etapas faltan."""
    now = datetime.now(timezone.utc).isoformat()
    etapas = []
    for i, e in enumerate(tl):
        nxt = tl[i + 1]["ts"] if i + 1 < len(tl) else now
        etapas.append({"etapa": e.get("etapa") or e.get("etapa_key"),
                       "etapa_key": e.get("etapa_key"),
                       "desde": e.get("ts"),          # fecha-hora de llegada a la etapa
                       "horas": _hours_between(e.get("ts"), nxt)})
    cur = tl[-1] if tl else None
    cur_key = cur.get("etapa_key") if cur else None
    falta, seen = [], False
    if cur_key:
        for k, label in _ETAPAS_ORDER:
            if seen:
                falta.append(label)
            if k == cur_key:
                seen = True
    return {"etapas": etapas,
            "current": (cur.get("etapa") if cur else "(sin iniciar)"),
            "current_key": cur_key,
            "horas_total": round(sum(e["horas"] for e in etapas), 1),
            "horas_etapa_actual": (etapas[-1]["horas"] if etapas else 0.0),
            "falta": falta}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("panel")

app = FastAPI(title=config.PANEL_TITLE)
app.include_router(auth.router)

# Líder de proyecto de CLIENTE = `project-manager` SIN ningún rol de fábrica.
# Queda restringido a SOLO el reporte de avance (el shell se auto-limita y las
# APIs del panel se bloquean; el reporte en sí se sirve desde el inbox).
_FABRICA_ROLES = {"admin", "swf-project-manager", "swf-funcional",
                  "swf-desarrollador", "swf-tester"}


def _es_lider_cliente_solo(roles) -> bool:
    roles = set(roles or [])
    return "project-manager" in roles and not (roles & _FABRICA_ROLES)


@app.middleware("http")
async def require_auth_for_panel(request: Request, call_next):
    """El panel entero requiere sesión + rol de fábrica (ver
    config.ALLOWED_ROLES). Los endpoints /auth/* (login, callback, logout,
    sso-logout, me) son públicos."""
    path = request.url.path
    needs_auth = path == "/panel" or path.startswith("/panel/") or path.startswith("/api/")
    if needs_auth and config.OIDC_ISSUER:
        user = auth.get_current_user(request)
        if not user:
            accept = request.headers.get("accept", "")
            if "text/html" in accept and request.method == "GET":
                return RedirectResponse(f"/auth/login?next={path}",
                                        status_code=302)
            return JSONResponse({"detail": "not authenticated"},
                                status_code=401)
        # Rol check
        roles = set(user.get("roles") or [])
        if not (roles & config.ALLOWED_ROLES):
            return JSONResponse(
                {"detail": f"requiere rol: {' | '.join(config.ALLOWED_ROLES)}",
                 "your_roles": list(roles)},
                status_code=403)
        # Líder de CLIENTE: solo el shell (/panel, que se auto-restringe al
        # reporte) + su identidad (/api/me). Las demás APIs del panel → 403.
        if (_es_lider_cliente_solo(roles) and path.startswith("/api/")
                and path != "/api/me"):
            return JSONResponse(
                {"detail": "restringido al reporte de avance"}, status_code=403)
    response = await call_next(request)
    # No cachear respuestas de API (datos en vivo: dashboard, usuarios, etc.)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/")
async def root():
    return RedirectResponse("/panel", status_code=302)


@app.get("/panel", response_class=HTMLResponse)
async def panel_home(request: Request):
    user = auth.require_panel_access(request)
    html = _SHELL_HTML.replace(
        "{{USER_JSON}}",
        f'{{"username": "{user["username"]}", "name": "{user["name"]}", "roles": {list(user["roles"])}}}'.replace("'", '"')
    ).replace("{{INBOX_URL}}", config.INBOX_BASE_URL)
    # No cachear el HTML (lleva el JS embebido) → el navegador siempre toma la
    # última versión tras un deploy (evita tener que hacer Ctrl+Shift+R).
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# Placeholder de las secciones (devuelven JSON para que el frontend muestre
# un mensaje "próximamente").
@app.get("/api/me")
async def api_me(request: Request) -> dict:
    return auth.require_panel_access(request)


@app.get("/api/proyectos")
async def api_proyectos(request: Request) -> dict:
    """Lista proyectos desde proyecto-db (ya NO OpenProject) para la segmentación
    de Usuarios. Filtrado por seguridad (admin = todos)."""
    user = auth.require_panel_access(request)
    es_admin = "admin" in set(user.get("roles") or [])
    return {"projects": await proyecto_db.listar_proyectos(
        user.get("username", ""), es_admin)}


@app.get("/api/clientes")
async def api_clientes(request: Request) -> dict:
    """Clientes existentes (para el alta de proyectos). Solo admin."""
    _require_admin(request)
    return {"clientes": await proyecto_db.listar_clientes()}


@app.get("/api/capacidades")
async def api_capacidades(request: Request, cliente: str = "") -> dict:
    """Capacidades (nombres distintos) de los proyectos de un cliente, para
    clonarlas al crear un proyecto nuevo del mismo cliente. Solo admin."""
    _require_admin(request)
    if not cliente.strip():
        return {"capacidades": []}
    return {"capacidades": await proyecto_db.capacidades_de_cliente(cliente)}


@app.post("/api/proyectos")
async def api_proyectos_create(request: Request, body: dict) -> dict:
    """Crea un proyecto en proyecto-db: cliente (existente o nuevo) + nombre +
    capacidades + usuarios con acceso. Solo admin."""
    _require_admin(request)
    if not proyecto_db.habilitada():
        raise HTTPException(status_code=503,
                            detail="DB de proyecto no configurada (PROYECTO_DB_DSN)")
    try:
        p = await proyecto_db.crear_proyecto(
            body.get("cliente", ""), body.get("proyecto", ""),
            body.get("capacidades") or [], body.get("usuarios") or [])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "proyecto": p}


@app.get("/api/sap-systems")
async def api_sap_systems(request: Request) -> dict:
    """Sistemas SAP del MCP ABAP (espejo no-secreto). Solo admin."""
    _require_admin(request)
    return {"clientes": await proyecto_db.listar_clientes(),
            "systems": await proyecto_db.listar_sap_systems()}


@app.post("/api/sap-systems")
async def api_sap_systems_create(request: Request, body: dict) -> dict:
    """Alta/edición de un sistema SAP: credencial a SSM + espejo en proyecto-db.
    Solo admin."""
    _require_admin(request)
    if not proyecto_db.habilitada():
        raise HTTPException(status_code=503,
                            detail="DB de proyecto no configurada (PROYECTO_DB_DSN)")
    try:
        s = await proyecto_db.crear_sap_system(
            body.get("cliente", ""), body.get("system_id", ""),
            body.get("host", ""), body.get("sap_client", "100"),
            body.get("username", ""), body.get("password", ""),
            body.get("description", ""), bool(body.get("secure", True)),
            body.get("ssm_param") or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 (SSM/boto3/permisos)
        raise HTTPException(status_code=502, detail=f"no se pudo guardar en SSM: {e}")
    return {"ok": True, "system": s}


@app.delete("/api/sap-systems/{system_id}")
async def api_sap_systems_delete(system_id: str, request: Request) -> dict:
    """Baja de un sistema SAP (BD + SSM). Solo admin."""
    _require_admin(request)
    try:
        ok = await proyecto_db.eliminar_sap_system(system_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"no se pudo borrar en SSM: {e}")
    if not ok:
        raise HTTPException(status_code=404, detail="sistema no encontrado")
    return {"ok": True}


@app.get("/api/dashboard")
async def api_dashboard(request: Request) -> dict:
    """KPIs del estado de requerimientos desde proyecto-db (ya NO OpenProject):
    total, atrasados, en pool, sin iniciar, por etapa y por proyecto. Filtrado
    por seguridad (roles×proyectos×capacidades)."""
    user = auth.require_panel_access(request)
    roles = set(user.get("roles") or [])
    es_admin = "admin" in roles
    d = await proyecto_db.dashboard(user.get("username", ""), es_admin)
    if d is None:
        return {"detail": "DB de proyecto no configurada (PROYECTO_DB_DSN)."}
    return d


_DEV_PHASES = {"desarrollo", "pruebas_funcionales", "pruebas_usuario"}


@app.get("/api/proyectos/{project_id}/reporte")
async def api_proyecto_reporte(project_id: int, request: Request) -> dict:
    """Reporte de control = Gantt del proyecto: por cada requerimiento, fechas
    plan (inicio/fin), horas estimadas, funcional/desarrollador/asignado y etapa.
    Agrupado por capacidad. funcional = asignaciones.json (Gitea); desarrollador =
    pelota_usuario cuando está en fase de desarrollo; asignado = pelota actual."""
    user = auth.require_panel_access(request)
    es_admin = "admin" in set(user.get("roles") or [])
    reqs = await proyecto_db.requisitos_de_proyecto(
        user.get("username", ""), es_admin, project_id)
    caps: dict = {}
    async with clients.Gitea() as gt:
        for w in reqs:
            asig = await gt.read_asignacion(w["id"])
            # Columnas del board (editables) con fallback a asignaciones.json / pelota.
            funcional = w.get("funcional") or (asig.get("funcional") or "").strip() or None
            usuario_clave = w.get("usuario_clave") or (asig.get("usuario_clave") or "").strip() or None
            desarrollador = w.get("desarrollador") or (
                w.get("asignado") if w.get("etapa") in _DEV_PHASES else None)
            caps.setdefault(w["capability"], []).append(
                {**w, "funcional": funcional, "usuario_clave": usuario_clave,
                 "desarrollador": desarrollador})
    proj = set(await proyecto_db.usuarios_de_proyecto(project_id))
    uf, ud, uc = [], [], []
    try:
        async with kc_admin.KCAdmin() as kc:
            async def _rl(*roles):
                seen, out = set(), []
                for role in roles:
                    for u in await kc.list_users_with_role(role):
                        un = u["username"]
                        if un in proj and un not in seen:
                            seen.add(un); out.append({"u": un, "n": u["name"]})
                return sorted(out, key=lambda x: (x["n"] or "").lower())
            uf = await _rl("funcional", "swf-funcional")
            ud = await _rl("swf-desarrollador")
            uc = await _rl("usuario-clave")
    except Exception:  # noqa: BLE001
        pass
    return {"project_id": project_id,
            "prioridades": await proyecto_db.listar_prioridades(),
            "can_set_prioridad": True,   # el acceso al Panel ya es admin/PM
            "inbox_url": config.INBOX_BASE_URL,
            "usuarios_funcional": uf, "usuarios_desarrollador": ud,
            "usuarios_clave": uc,
            "capabilities": [{"capability": k, "requisitos": v}
                             for k, v in sorted(caps.items())]}


@app.post("/api/proyectos/wp/{wp_id}/campo")
async def api_editar_campo(wp_id: int, request: Request, body: dict) -> dict:
    """Edita un campo del requerimiento desde el Gantt: fin (recalcula inicio),
    funcional, desarrollador o usuario_clave."""
    auth.require_panel_access(request)
    field = (body.get("field") or "").strip()
    if field not in ("fin", "horas", "horas_tecnicas", "horas_funcionales",
                     "funcional", "desarrollador", "usuario_clave"):
        raise HTTPException(status_code=400, detail="campo inválido")
    if not await proyecto_db.editar_campo(wp_id, field, body.get("value") or ""):
        raise HTTPException(status_code=400, detail="no se pudo guardar")
    return {"ok": True}


@app.post("/api/proyectos/wp/{wp_id}/prioridad")
async def api_set_prioridad(wp_id: int, request: Request, body: dict) -> dict:
    """Fija la prioridad de un requerimiento desde el Gantt del Panel."""
    auth.require_panel_access(request)
    code = (body.get("prioridad") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="prioridad requerida")
    if not await proyecto_db.set_prioridad(wp_id, code):
        raise HTTPException(status_code=400, detail="no se pudo fijar la prioridad")
    return {"ok": True, "prioridad": code}


@app.get("/api/metricas")
async def api_metricas(request: Request) -> dict:
    auth.require_panel_access(request)
    return {"status": "coming_soon",
            "description": "Tokens consumidos + costo USD por WP / iteración "
                           "/ etapa / modelo / rol revisor. Lee metricas.json "
                           "de cada branch req-X en Gitea."}


def _require_admin(request: Request) -> dict:
    user = auth.require_panel_access(request)
    if "admin" not in set(user.get("roles") or []):
        raise HTTPException(status_code=403,
                            detail="solo el rol admin administra usuarios")
    return user


@app.get("/api/usuarios")
async def api_usuarios(request: Request) -> dict:
    """Lista usuarios del realm + roles asignables (Keycloak = fuente de
    verdad de identidad + roles para SSO)."""
    _require_admin(request)
    async with kc_admin.KCAdmin() as kc:
        return {"users": await kc.list_users(),
                "roles": await kc.list_realm_roles(),
                "access_map": {k: sorted(v) for k, v in config.ACCESS_MAP.items()}}


@app.post("/api/usuarios")
async def api_usuarios_create(request: Request, body: dict) -> dict:
    _require_admin(request)
    if not body.get("username"):
        raise HTTPException(status_code=400, detail="username requerido")
    async with kc_admin.KCAdmin() as kc:
        r = await kc.create_user(
            username=body["username"], email=body.get("email", ""),
            first=body.get("first", ""), last=body.get("last", ""),
            password=body.get("password") or "Cambiar-2026!",
            roles=body.get("roles") or [])
    return {"ok": True, **r,
            "nota": "Creado en Keycloak (SSO + roles). Provisioning en "
                    "Gitea (org del cliente) con pre-link OIDC."}


@app.patch("/api/usuarios/{user_id}/roles")
async def api_usuarios_roles(user_id: str, request: Request, body: dict) -> dict:
    _require_admin(request)
    roles = body.get("roles") or []
    async with kc_admin.KCAdmin() as kc:
        await kc.set_roles(user_id, roles)
        username = (await kc.get_user(user_id)).get("username")
    # Propaga los roles a los tokens MCP vigentes del usuario (no quedan obsoletos).
    n = await proyecto_db.actualizar_roles_tokens(username, roles) if username else 0
    return {"ok": True, "tokens_actualizados": n}


@app.patch("/api/usuarios/{user_id}/enabled")
async def api_usuarios_enabled(user_id: str, request: Request, body: dict) -> dict:
    _require_admin(request)
    async with kc_admin.KCAdmin() as kc:
        await kc.set_enabled(user_id, bool(body.get("enabled", True)))
    return {"ok": True}


@app.patch("/api/usuarios/{user_id}/password")
async def api_usuarios_password(user_id: str, request: Request,
                                body: dict) -> dict:
    """Cambia la contraseña de un usuario (acción de admin). `temporary`
    (default True) fuerza el cambio en el próximo login."""
    _require_admin(request)
    pwd = (body.get("password") or "").strip()
    if len(pwd) < 8:
        raise HTTPException(status_code=400,
                            detail="la contraseña debe tener al menos 8 caracteres")
    temporary = bool(body.get("temporary", True))
    async with kc_admin.KCAdmin() as kc:
        await kc.set_password(user_id, pwd, temporary)
    return {"ok": True, "temporary": temporary}


@app.patch("/api/usuarios/{user_id}/proyectos")
async def api_usuarios_proyectos(user_id: str, request: Request,
                                 body: dict) -> dict:
    """Asigna proyectos a un usuario (segmentación por seguridad). Escribe el
    atributo `proyectos` en Keycloak (identidad) Y la tabla usuario_proyecto en
    proyecto-db (fuente de verdad que lee el Inbox/dashboard para filtrar)."""
    _require_admin(request)
    project_ids = [str(p) for p in (body.get("project_ids") or [])]
    # 1) Fuente de verdad: Keycloak.
    async with kc_admin.KCAdmin() as kc:
        await kc.set_user_projects(user_id, project_ids)
        user = await kc.get_user(user_id)
    login = user.get("username") or ""
    # 2) Segmentación efectiva en proyecto-db (lo que lee la seguridad del
    # inbox/dashboard). Reemplaza el sync de membresías OpenProject.
    n = await proyecto_db.set_usuario_proyectos(login, project_ids)
    return {"ok": True, "proyectos": project_ids,
            "op_sync": "ok", "detail": f"proyecto-db: {n} proyecto(s) asignados"}


@app.get("/api/usuarios/{user_id}/capacidades-disponibles")
async def api_usuarios_caps_disp(user_id: str, request: Request) -> dict:
    """Capacidades elegibles para el usuario = las de sus proyectos asignados,
    agrupadas por proyecto. Más las que ya tiene seleccionadas."""
    _require_admin(request)
    async with kc_admin.KCAdmin() as kc:
        user = await kc.get_user(user_id)
    attrs = user.get("attributes") or {}
    proj_ids = [str(p) for p in (attrs.get("proyectos") or [])]
    selected = [str(c) for c in (attrs.get("capacidades") or [])]
    grupos = await proyecto_db.capacidades_por_proyecto(proj_ids)
    return {"grupos": grupos, "selected": selected,
            "sin_proyectos": not proj_ids}


@app.patch("/api/usuarios/{user_id}/capacidades")
async def api_usuarios_caps_set(user_id: str, request: Request,
                                body: dict) -> dict:
    """Asigna capacidades (IDs de WP Capability) al usuario en Keycloak."""
    _require_admin(request)
    cap_ids = [str(c) for c in (body.get("capability_ids") or [])]
    async with kc_admin.KCAdmin() as kc:
        await kc.set_user_capabilities(user_id, cap_ids)
        u = await kc.get_user(user_id)
    # Segmentación efectiva en proyecto-db (seguridad del inbox/dashboard).
    n = await proyecto_db.set_usuario_capacidades(u.get("username") or "", cap_ids)
    return {"ok": True, "capacidades": cap_ids,
            "detail": f"proyecto-db: {n} capacidad(es)"}


@app.post("/api/usuarios/{username}/mcp-token")
async def api_mcp_token_crear(username: str, request: Request,
                             body: dict) -> dict:
    """Genera un token personal de MCP para el usuario (Claude Code). Devuelve el
    texto plano UNA sola vez; guarda solo su hash. roles = snapshot para permisos.
    Ver [[workflow-dirigido-por-blog]]."""
    _require_admin(request)
    roles = [str(r) for r in (body.get("roles") or [])]
    token = await proyecto_db.crear_mcp_token(
        username, roles, body.get("descripcion") or "")
    if token is None:
        raise HTTPException(status_code=503, detail="DB de proyecto no disponible")
    return {"ok": True, "token": token, "usuario": username,
            "nota": "Guárdalo ahora: no se vuelve a mostrar. Va en el env del "
                    "usuario como FABRICA_MCP_TOKEN."}


@app.get("/api/usuarios/{username}/mcp-tokens")
async def api_mcp_token_listar(username: str, request: Request) -> dict:
    _require_admin(request)
    return {"tokens": await proyecto_db.listar_mcp_tokens(username)}


@app.post("/api/usuarios/mcp-token/{token_id}/revoke")
async def api_mcp_token_revocar(token_id: int, request: Request) -> dict:
    _require_admin(request)
    ok = await proyecto_db.revocar_mcp_token(token_id)
    return {"ok": ok}


@app.post("/api/usuarios/{username}/gitea-provision")
async def api_gitea_provision(username: str, request: Request,
                             body: dict) -> dict:
    """Provisiona acceso Gitea por usuario: garantiza su cuenta Gitea y lo agrega
    (team con write) a las ORGS de los clientes que tiene asignados (segmentación).
    Así clona/empuja SOLO los repos de su cliente. Devuelve el password una vez si
    creó la cuenta. Ver [[gestion-usuarios-sso]] y [[workflow-dirigido-por-blog]]."""
    _require_admin(request)
    import secrets
    roles = [str(r) for r in (body.get("roles") or [])]
    email = body.get("email") or ""
    es_admin = "admin" in roles
    clientes = await proyecto_db.clientes_de_usuario(username, es_admin)
    orgs = sorted({clients.slug_org(n) for n in clientes if n})
    password = "Gitea-" + secrets.token_urlsafe(12)
    concedidas, faltantes = [], []
    gitea_user = username
    async with clients.Gitea() as gt:
        try:
            estado, gitea_user = await gt.ensure_user(username, email, password)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502,
                                detail=f"Gitea (crear usuario): {exc}")
        # Solo se puede dar membresía si la cuenta existe. Se usa el username REAL
        # de Gitea (resuelto), que puede diferir del de KC por el saneo del '@'.
        if estado in ("created", "exists"):
            for org in orgs:
                try:
                    tid = await gt.ensure_team(org)
                    if tid:
                        await gt.add_team_member(tid, gitea_user)
                        concedidas.append(org)
                    else:
                        faltantes.append(org)   # la org aún no existe en Gitea
                except Exception as exc:  # noqa: BLE001
                    log.warning("gitea grant %s/%s falló: %s", username, org, exc)
                    faltantes.append(org)
    if estado == "no_admin":
        nota = ("⚠ No existe la cuenta Gitea y el token no es site-admin. "
                "Crea la cuenta (login SSO en Gitea o define GITEA_ADMIN_TOKEN) "
                "y vuelve a provisionar para conceder el acceso a sus orgs.")
    elif not orgs:
        nota = "Cuenta lista, pero sin clientes asignados todavía (nada que conceder)."
    else:
        nota = "Acceso de escritura a los repos de sus clientes."
    return {"ok": True, "usuario": username, "gitea_usuario": gitea_user,
            "estado": estado,
            "password": password if estado == "created" else None,
            "orgs_concedidas": concedidas, "orgs_pendientes": faltantes,
            "nota": nota}


@app.get("/api/proyecto-board")
async def api_proyecto_board(request: Request) -> dict:
    """Vista de proyecto (reemplazo de OpenProject): requisitos con estimado
    (camisas), tiempo invertido (horas laborales), avance y front/back. Filtra
    por seguridad = roles × proyectos × capacidades. Lee la DB de proyecto;
    si la DB no está configurada (`PROYECTO_DB_DSN`), devuelve vacío."""
    user = auth.require_panel_access(request)
    roles = set(user.get("roles") or [])
    es_admin = "admin" in roles
    filas = await proyecto_db.listar_requisitos(user.get("username", ""), es_admin)
    return {"requisitos": filas, "db": proyecto_db.habilitada()}


@app.get("/api/configuracion")
async def api_configuracion(request: Request) -> dict:
    auth.require_panel_access(request)
    return {"status": "coming_soon",
            "description": "Editar pipeline.yaml, arquitectura.md, "
                           "spec_template.md en Gitea con commit diff."}


# ----------------------------------------------------------------------
_SHELL_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<title>Panel de Control · Fábrica</title>
<style>
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    color: #1a1a1a; background: #f5f5f5;
  }
  header {
    background: #1a3a52; color: #fff; padding: 12px 20px;
    display: flex; align-items: center; gap: 16px;
  }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header .sub { font-size: 12px; color: #b0c4d4; }
  header .spacer { flex: 1; }
  header .user-badge {
    font-size: 12px; background: rgba(255,255,255,0.15); padding: 4px 10px;
    border-radius: 12px;
  }
  header button {
    background: rgba(255,255,255,0.15); color: #fff; border: 1px solid rgba(255,255,255,0.3);
    padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
  }
  header button:hover { background: rgba(255,255,255,0.25); }
  main { display: grid; grid-template-columns: 220px 1fr; height: calc(100vh - 50px); }
  main.nav-collapsed { grid-template-columns: 1fr; }
  main.nav-collapsed aside { display: none; }
  aside {
    background: #fff; border-right: 1px solid #e0e0e0;
    padding: 16px 0;
  }
  aside .nav-item {
    padding: 10px 20px; cursor: pointer; border-left: 3px solid transparent;
    color: #555;
  }
  aside .nav-item:hover { background: #f5f8fc; color: #1a3a52; }
  aside .nav-item.active {
    background: #e3f2fd; border-left-color: #1a3a52;
    color: #1a3a52; font-weight: 600;
  }
  aside .nav-item .icon { margin-right: 8px; }
  section#content { padding: 24px 32px; overflow: auto; }
  .panel {
    background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
    padding: 24px; margin-bottom: 16px;
  }
  .panel h2 { margin: 0 0 12px 0; font-size: 18px; }
  .panel .soon {
    background: #fff8e1; border: 1px solid #f0d060; border-radius: 4px;
    padding: 16px; color: #6b5500; font-size: 13px;
  }
  .panel .soon strong { color: #4a3800; }
  pre.json {
    background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px;
    padding: 12px; font-size: 12px; overflow: auto;
  }
  select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
  table.rep { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0 18px; table-layout: fixed; }
  table.rep th, table.rep td { border: 1px solid #e0e0e0; padding: 6px 8px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
  table.rep th { background: #f5f8fc; font-weight: 600; }
  /* Botones compactos dentro de la tabla de usuarios (que no se desborden). */
  table.rep td button { font-size: 11px; padding: 3px 7px; border: 1px solid #cdd6e4;
    background: #fff; border-radius: 4px; cursor: pointer; white-space: nowrap; }
  table.rep td button:hover { background: #eef4fc; }
  #content h3 { margin: 16px 0 6px; font-size: 14px; color: #1a3a52; }
  /* Picklist múltiple compacto (roles / proyectos): colapsado ahorra espacio. */
  .ms { position: relative; display: inline-block; }
  .ms > summary { list-style: none; cursor: pointer; padding: 4px 8px; border: 1px solid #cdd6e4;
    border-radius: 4px; background: #fff; min-width: 140px; max-width: 230px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
  .ms > summary::-webkit-details-marker { display: none; }
  .ms > summary::after { content: " ▾"; color: #888; }
  .ms[open] > summary { border-color: #0a6ed1; }
  .ms .ms-pop { position: absolute; z-index: 50; top: 100%; left: 0; margin-top: 2px; background: #fff;
    border: 1px solid #cdd6e4; border-radius: 6px; box-shadow: 0 4px 14px rgba(0,0,0,.12); padding: 6px;
    max-height: 230px; overflow: auto; min-width: 180px; }
  .ms .ms-pop label { display: block; white-space: nowrap; padding: 3px 6px; border-radius: 3px; cursor: pointer; }
  .ms .ms-pop label:hover { background: #eef4fc; }
</style>
</head>
<body>
<header>
  <button id="nav-toggle" title="Mostrar/ocultar menú" style="font-size:18px;line-height:1">☰</button>
  <div>
    <h1>Panel de Control</h1>
    <div class="sub">Fábrica de Software</div>
  </div>
  <div class="spacer"></div>
  <span class="user-badge" id="user-badge">cargando...</span>
  <button id="logout-btn">Logout</button>
</header>
<main>
  <aside>
    <div class="nav-item active" data-section="dashboard"><span class="icon">📊</span>Dashboard</div>
    <div class="nav-item" data-section="avance"><span class="icon">📋</span>Estado del proyecto</div>
    <div class="nav-item" data-section="reporte"><span class="icon">📈</span>Avance (presentación)</div>
    <div class="nav-item" data-section="usuarios"><span class="icon">👥</span>Usuarios</div>
    <div class="nav-item" data-section="gestproy"><span class="icon">🗂</span>Proyectos</div>
    <div class="nav-item" data-section="sapsys"><span class="icon">🛢</span>Sistemas SAP</div>
  </aside>
  <section id="content">
    <div class="panel"><div class="empty">cargando...</div></div>
  </section>
</main>
<script>
const currentUser = {{USER_JSON}};
const INBOX_URL = "{{INBOX_URL}}";
document.getElementById("user-badge").textContent =
  currentUser.username + " · " + (currentUser.roles || []).join(", ");
document.getElementById("logout-btn").onclick = () => { window.location = "/auth/logout"; };
document.getElementById("nav-toggle").onclick = () => document.querySelector("main").classList.toggle("nav-collapsed");

const titles = {
  dashboard: "📊 Dashboard",
  avance: "📋 Avance del proyecto",
  reporte: "📈 Avance del proyecto (presentación)",
  metricas: "💰 Métricas de uso",
  usuarios: "👥 Gestión de usuarios",
  configuracion: "⚙️ Configuración de la fábrica",
};

async function loadSection(name) {
  if (name === "dashboard") return renderDashboard();
  if (name === "proyectos") return renderProyectos();
  if (name === "avance") return renderAvance();
  if (name === "reporte") return renderReporte();
  if (name === "usuarios") return renderUsuarios();
  if (name === "gestproy") return renderGestionProyectos();
  if (name === "sapsys") return renderSapSystems();
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === name));
  const $c = document.getElementById("content");
  $c.innerHTML = `<div class="panel"><div class="empty">cargando...</div></div>`;
  try {
    const r = await fetch("/api/" + name);
    const d = await r.json();
    $c.innerHTML = `
      <div class="panel">
        <h2>${titles[name] || name}</h2>
        ${d.status === "coming_soon"
          ? `<div class="soon"><strong>🚧 Próximamente.</strong> ${escapeHtml(d.description || "")}</div>`
          : `<pre class="json">${escapeHtml(JSON.stringify(d, null, 2))}</pre>`}
      </div>`;
  } catch (e) {
    $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`;
  }
}
function renderReporte() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "reporte"));
  const $c = document.getElementById("content");
  // Reutiliza el reporte del Inbox (misma vista, no se duplica) embebido.
  $c.innerHTML = `<div class="panel" style="padding:0;overflow:hidden">
    <iframe src="${INBOX_URL}/reporte-avance" title="Avance del proyecto"
      style="width:100%;height:calc(100vh - 120px);border:none;display:block"></iframe></div>`;
}
async function renderAvance() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "avance"));
  const $c = document.getElementById("content");
  // Estado del proyecto + Gantt se unificaron en el Inbox (vista única; no se
  // mantiene en dos lados). El Panel enlaza a esa misma vista.
  $c.innerHTML = `<div class="panel"><h2>📋 Estado del proyecto</h2>
    <p style="color:#555;max-width:640px">El estado del proyecto y el Gantt ahora viven en una sola vista, en el <b>Inbox</b> (así no se mantienen en dos lados). Desde ahí ves el resumen, las horas por etapa y el Gantt, y descargas el Excel.</p>
    <p><a href="${INBOX_URL}/inbox#proyecto" target="_blank" rel="noopener" style="display:inline-block;background:#0a6ed1;color:#fff;text-decoration:none;border-radius:4px;padding:9px 18px;font-weight:600">📊 Abrir Estado del proyecto en el Inbox ↗</a></p></div>`;
  return;
  let d;
  try { d = await (await fetch("/api/proyecto-board")).json(); }
  catch (e) { $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`; return; }
  if (!d.db) {
    $c.innerHTML = `<div class="panel"><h2>${titles.avance}</h2>
      <div class="soon"><strong>🚧 DB de proyecto no configurada.</strong> Define <code>PROYECTO_DB_DSN</code> y corre <code>db/schema.sql</code> + <code>db/funciones.sql</code>.</div></div>`;
    return;
  }
  const reqs = d.requisitos || [];
  const num = (v) => (v == null ? "—" : Number(v).toLocaleString("es", {maximumFractionDigits: 1}));
  const bar = (pct) => `<div style="background:#e3e9f2;border-radius:3px;height:14px;width:90px;display:inline-block;vertical-align:middle;overflow:hidden"><div style="background:#107e3e;height:100%;width:${pct||0}%"></div></div> <span style="font-size:11px">${pct||0}%</span>`;
  // Badge de prioridad por color (urgente=rojo … baja=gris).
  const PRIO_COLOR = {urgente:"#bb0000", alta:"#d97400", media:"#0a6ed1", baja:"#6a6d70"};
  const prio = (r) => {
    const code = r.prioridad || "media";
    const col = PRIO_COLOR[code] || "#6a6d70";
    const lbl = r.prioridad_label || code;
    return `<span style="display:inline-block;font-size:11px;font-weight:600;color:#fff;background:${col};border-radius:10px;padding:1px 9px">${escapeHtml(lbl)}</span>`;
  };
  // Etiquetas legibles + "la pelota" (responsable actual de la fase).
  const ROL_LBL = {"swf-funcional":"Funcional Fábrica","admin":"Admin","swf-desarrollador":"Desarrollador","funcional":"Funcional","usuario-clave":"Usuario","project-manager":"Líder Proyecto","swf-project-manager":"Líder Proyecto","swf-tester":"Tester"};
  const ETAPA_LBL = {recepcion:"Recepción",diseno:"Diseño",desarrollo:"Desarrollo",pruebas_funcionales:"Pruebas Func.",pruebas_usuario:"Pruebas Usuario",cerrado:"Cerrado"};
  const sinceTxt = (s) => { if (!s) return ""; try { return new Date(s).toLocaleDateString("es"); } catch(e){ return ""; } };
  const pelota = (r) => {
    if (!r.workflow_iniciado) return `<span style="color:#888">⚪ sin iniciar</span>`;
    if (!r.pelota_usuario) return `<span style="color:#d97400">🎾 pool: ${escapeHtml(ROL_LBL[r.pelota_rol] || r.pelota_rol || "—")}</span>`;
    const desde = sinceTxt(r.pelota_desde);
    return `🎾 ${escapeHtml(r.pelota_usuario)}${desde?`<div style="font-size:10px;color:#888">desde ${desde}</div>`:""}`;
  };
  const subRow = (s) => `<tr style="background:#f7f9fc;font-size:12px;color:#555">
      <td colspan="3" style="padding-left:24px">↳ ${escapeHtml(s.tipo)} <span style="color:#999">(${escapeHtml(s.stack||"—")})</span></td>
      <td>${escapeHtml(s.talla||"—")}</td><td>${num(s.horas_estimadas)} h</td>
      <td colspan="2">${escapeHtml(s.responsable||"")}</td><td colspan="3">${escapeHtml(s.estado||"")}</td></tr>`;
  const rows = reqs.map(r => `
      <tr data-uid="${r.id}">
        <td>${prio(r)}</td>
        <td><b>${escapeHtml(r.titulo)}</b><div style="font-size:11px;color:#888">${escapeHtml(r.cliente)} · ${escapeHtml(r.proyecto)}${Number(r.comentarios)>0?` · 💬 ${r.comentarios}`:""}</div></td>
        <td style="font-size:11px">${escapeHtml(r.repo_name||"")}</td>
        <td>${escapeHtml(r.talla||"—")}</td>
        <td>${num(r.horas_estimadas)} h</td>
        <td>${num(r.tiempo_invertido)} h<div style="font-size:10px;color:#888">trab ${num(r.horas_trabajo)} · esp ${num(r.horas_espera)}</div></td>
        <td style="font-size:11px">${escapeHtml(r.inicio_plan||"—")}<br>${escapeHtml(r.fin_plan||"—")}</td>
        <td style="font-size:11px">${escapeHtml(ETAPA_LBL[r.etapa_actual]||r.etapa_actual||"—")}</td>
        <td style="font-size:11px">${pelota(r)}</td>
        <td>${bar(r.avance_pct)}</td>
      </tr>
      ${(r.subtareas||[]).map(subRow).join("")}`).join("");
  $c.innerHTML = `<div class="panel">
      <h2>${titles.avance}</h2>
      <div style="font-size:12px;color:#888;margin-bottom:8px">Ordenado por prioridad · 🎾 = quién tiene la pelota (responsable actual de la fase) · estimado por camisas · tiempo en horas laborales. Filtrado por tus proyectos/capacidades.</div>
      ${reqs.length ? `<table class="rep">
        <tr><th>Prioridad</th><th>Requisito</th><th>Repo</th><th>Talla</th><th>Estimado</th><th>Tiempo invertido</th><th>Inicio / Fin</th><th>Etapa</th><th>Pelota</th><th>Avance</th></tr>
        ${rows}
      </table>` : `<div class="empty">No hay requisitos visibles para ti todavía.</div>`}
    </div>`;
}

async function renderDashboard() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "dashboard"));
  const $c = document.getElementById("content");
  $c.innerHTML = `<div class="panel"><h2>📊 Dashboard</h2><div class="empty">cargando indicadores…</div></div>`;
  let d;
  try { d = await (await fetch("/api/dashboard", { cache: "no-store" })).json(); }
  catch (e) { $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`; return; }
  if (d.detail) { $c.innerHTML = `<div class="panel"><div class="soon">${escapeHtml(d.detail)}</div></div>`; return; }
  const ETAPA_LBL = {recepcion:"Recepción",diseno:"Diseño",desarrollo:"Desarrollo",pruebas_funcionales:"Pruebas Func.",pruebas_usuario:"Pruebas Usuario",cerrado:"Cerrado"};
  const card = (num, lbl, color) => `<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.05)">
      <div style="font-size:34px;font-weight:700;color:${color};line-height:1">${num}</div>
      <div style="font-size:13px;color:#666;margin-top:6px">${lbl}</div></div>`;
  const etapas = d.por_etapa || {};
  const etapaRows = Object.keys(ETAPA_LBL).filter(k => etapas[k]).map(k =>
    `<tr><td>${ETAPA_LBL[k]}</td><td style="text-align:right;font-weight:600">${etapas[k]}</td></tr>`).join("")
    || `<tr><td colspan="2" style="color:#888">—</td></tr>`;
  const proy = d.por_proyecto || [];
  const proyRows = proy.length ? proy.map(p => `<tr>
      <td>${escapeHtml(p.cliente)} · ${escapeHtml(p.proyecto)}</td>
      <td style="text-align:right">${p.n}</td>
      <td style="text-align:right;color:${p.atrasados>0?'#c0392b':'#107e3e'};font-weight:700">${p.atrasados}</td></tr>`).join("")
    : `<tr><td colspan="3" style="color:#888">Sin requerimientos visibles.</td></tr>`;
  $c.innerHTML = `
    <div class="panel">
      <h2 style="margin-bottom:14px">📊 Dashboard</h2>
      <div style="display:flex;gap:14px;flex-wrap:wrap">
        ${card(d.total ?? "—", "Requerimientos", "#0a6ed1")}
        ${card(d.atrasados ?? 0, "Atrasados", (d.atrasados>0)?"#c0392b":"#107e3e")}
        ${card(d.en_pool ?? 0, "En pool (sin tomar)", (d.en_pool>0)?"#d97400":"#107e3e")}
        ${card(d.sin_iniciar ?? 0, "Sin iniciar", "#6a6d70")}
      </div>
      <div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:22px">
        <div style="flex:1;min-width:260px">
          <h3>Por etapa</h3>
          <table class="rep"><tr><th>Etapa</th><th style="text-align:right">#</th></tr>${etapaRows}</table>
        </div>
        <div style="flex:2;min-width:340px">
          <h3>Por proyecto</h3>
          <table class="rep"><tr><th>Cliente · Proyecto</th><th style="text-align:right">Total</th><th style="text-align:right">Atrasados</th></tr>${proyRows}</table>
        </div>
      </div>
    </div>`;
}

async function renderProyectos() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "proyectos"));
  const $c = document.getElementById("content");
  $c.innerHTML = `<div class="panel"><h2>📁 Control de proyectos</h2><div class="empty">cargando proyectos...</div></div>`;
  let projects = [];
  try { projects = ((await (await fetch("/api/proyectos")).json()).projects) || []; }
  catch (e) { $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`; return; }
  // Selector único de proyecto (proyecto-db = lista plana) → dispara el Gantt.
  const opts = `<option value="">— Elige un proyecto —</option>` +
    projects.slice().sort((a, b) => a.name.localeCompare(b.name))
      .map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
  $c.innerHTML = `
    <div class="panel">
      <h2 style="margin-bottom:12px">📁 Control de proyectos</h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px">
        <select id="cb-proyecto" style="font-size:13px;padding:6px 9px;border:1px solid #cdd6e4;border-radius:4px;min-width:280px">${opts}</select>
      </div>
      <div id="proj-report"><div class="empty">Elige un proyecto para ver el Gantt.</div></div>
    </div>`;
  document.getElementById("cb-proyecto").onchange = (e) => {
    if (e.target.value) loadReporte(e.target.value);
    else document.getElementById("proj-report").innerHTML =
      `<div class="empty">Elige un proyecto para ver el Gantt.</div>`;
  };
}

// Gantt editable del proyecto: columnas (WP/Prioridad/Req/Funcional/Desarrollador/
// Usuario clave/Inicio/Fin/Horas) editables, filtros (prioridad/inicio/fin),
// total de esfuerzo y grid semanal con barras por etapa.
async function loadReporte(pid) {
  const $r = document.getElementById("proj-report");
  if (!pid) { $r.innerHTML = ""; return; }
  $r.innerHTML = `<div class="empty">cargando Gantt...</div>`;
  let data;
  try { data = await (await fetch(`/api/proyectos/${pid}/reporte`)).json(); }
  catch (e) { $r.innerHTML = `<div class="soon">Error: ${escapeHtml(e.message)}</div>`; return; }
  const caps = data.capabilities || [];
  if (!caps.length) { $r.innerHTML = `<div class="empty">Sin requerimientos en este proyecto.</div>`; return; }
  const INBOX = data.inbox_url || "";
  const UF = data.usuarios_funcional || [], UD = data.usuarios_desarrollador || [], UC = data.usuarios_clave || [];
  const listFor = (field) => field==="funcional"?UF:(field==="desarrollador"?UD:UC);
  const NAMEOF = {}; [...UF, ...UD, ...UC].forEach(x => { NAMEOF[x.u] = x.n; });
  const prios = data.prioridades || [], canPrio = data.can_set_prioridad && prios.length;
  const byWp = {}; caps.forEach(c => c.requisitos.forEach(r => byWp[r.id] = r));

  const WK = 66, ROWH = 26, DAY = 86400000;
  const MES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
  const ETAPA_LBL = {recepcion:"Recepción",diseno:"Diseño",desarrollo:"Desarrollo",pruebas_funcionales:"Pruebas Func.",pruebas_usuario:"Pruebas Usuario",cerrado:"Cerrado"};
  const ETAPA_COL = {recepcion:"#6a6d70",diseno:"#0a6ed1",desarrollo:"#d97400",pruebas_funcionales:"#8a5cd1",pruebas_usuario:"#c0398a",cerrado:"#107e3e"};
  const PRIO_COL = {urgente:"#bb0000",alta:"#d97400",media:"#0a6ed1",baja:"#6a6d70"};
  const pISO = (s) => { const [y,m,d] = s.split("-").map(Number); return new Date(y, m-1, d); };
  const isoOf = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
  const fmtD = (iso) => { if (!iso) return "—"; const d = pISO(iso); return `${String(d.getDate()).padStart(2,"0")} ${MES[d.getMonth()]}`; };
  const monday = (iso) => { const d = pISO(iso); const wd = (d.getDay()+6)%7; d.setDate(d.getDate()-wd); d.setHours(0,0,0,0); return d; };
  const wdBack = (finIso, n) => { const d = pISO(finIso); let s = Math.max(0,(n||1)-1); while(s>0){ d.setDate(d.getDate()-1); if(d.getDay()!==0&&d.getDay()!==6) s--; } return isoOf(d); };
  const userOpts = (sel, list) => `<option value="">—</option>` + (list||[]).map(x => `<option value="${escapeHtml(x.u)}" ${x.u===sel?"selected":""}>${escapeHtml(x.n)}</option>`).join("");
  const ediCell = (r, field) => `<select class="gedit" data-wp="${r.id}" data-field="${field}" title="Asignar ${field}" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px;background:#fff;cursor:pointer">${userOpts(r[field], listFor(field))}</select>`;
  const prioCell = (r) => {
    const cur = r.prioridad || "media", col = PRIO_COL[cur] || "#6a6d70";
    if (!canPrio) { const lbl = (prios.find(p => p.codigo === cur) || {}).label || cur;
      return `<span style="font-size:11px;font-weight:600;color:#fff;background:${col};border-radius:10px;padding:1px 8px">${escapeHtml(lbl)}</span>`; }
    return `<select class="gprio" data-wp="${r.id}" style="font-size:11px;font-weight:600;border:none;border-radius:10px;padding:2px 6px;color:#fff;background:${col};cursor:pointer">`
      + prios.map(p => `<option value="${p.codigo}" ${p.codigo===cur?"selected":""}>${escapeHtml(p.label)}</option>`).join("") + `</select>`;
  };

  // Estado de filtros (persisten entre re-renders).
  let fPrio = "", fIni = "", fFin = "";
  const passes = (r) => (!fPrio || (r.prioridad||"media") === fPrio)
    && (!fIni || (r.inicio && r.inicio >= fIni)) && (!fFin || (r.fin && r.fin <= fFin));

  const W = {wp:40, pr:86, req:180, fu:112, de:112, uc:112, ini:64, fin:110, ht:58, hf:58};
  const LW = W.wp+W.pr+W.req+W.fu+W.de+W.uc+W.ini+W.fin+W.ht+W.hf;

  function render() {
    const fcaps = caps.map(c => ({capability:c.capability, requisitos:c.requisitos.filter(passes)})).filter(c => c.requisitos.length);
    const visible = fcaps.flatMap(c => c.requisitos);
    const totalH = visible.reduce((s,r) => s + (+r.horas || 0), 0);
    const dated = visible.filter(r => r.inicio && r.fin);
    let weeks = [], gridStart = null, nW = 0;
    if (dated.length) {
      const minI = dated.reduce((m,r) => r.inicio < m ? r.inicio : m, dated[0].inicio);
      const maxF = dated.reduce((m,r) => r.fin > m ? r.fin : m, dated[0].fin);
      gridStart = monday(minI); const lastMon = monday(maxF);
      nW = Math.round((lastMon - gridStart)/DAY/7) + 1;
      weeks = Array.from({length:nW}, (_,i) => { const d = new Date(gridStart); d.setDate(d.getDate()+i*7); return d; });
    }
    const TRACK = nW*WK, TOT = LW+TRACK;
    const colHdr = (w,t) => `<div style="width:${w}px;padding:6px 6px;box-sizing:border-box">${t}</div>`;
    const wpCell = (r) => INBOX
      ? `<a href="${INBOX}/inbox#wp=${r.id}" target="_blank" rel="noopener" title="Abrir en el inbox" style="color:#0a6ed1;text-decoration:none;font-weight:600">#${r.id}</a>` : `#${r.id}`;
    const bar = (r) => {
      if (!r.inicio || !r.fin || !gridStart) return "";
      const s = pISO(r.inicio), e = pISO(r.fin);
      const left = (s-gridStart)/DAY/7*WK, w = Math.max(12, ((e-s)/DAY+1)/7*WK);
      const col = ETAPA_COL[r.etapa] || "#9aa7b8", pct = Math.max(0, Math.min(100, Math.round(r.avance_pct||0)));
      return `<div title="#${r.id} ${escapeHtml(r.subject||"")} · ${r.inicio} → ${r.fin} · ${r.horas||0} h · ${ETAPA_LBL[r.etapa]||r.etapa||""} (${pct}%)" style="position:absolute;left:${left}px;width:${w}px;top:5px;height:15px;background:${col}26;border:1px solid ${col};border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${col}"></div></div>`;
    };
    const header = `<div style="display:flex;position:sticky;top:0;z-index:3;background:#eef2f8;border-bottom:2px solid #c9d4e4;font-size:11px;font-weight:700;color:#33415c">
        <div style="display:flex;flex-shrink:0;position:sticky;left:0;z-index:4;background:#eef2f8;width:${LW}px">
          ${colHdr(W.wp,"WP")}${colHdr(W.pr,"Prioridad")}${colHdr(W.req,"Requerimiento")}${colHdr(W.fu,"Funcional")}${colHdr(W.de,"Desarrollador")}${colHdr(W.uc,"Usuario clave")}${colHdr(W.ini,"Inicio")}${colHdr(W.fin,"Fin")}<div style="width:${W.ht}px;padding:6px 4px;box-sizing:border-box;text-align:right" title="Horas técnicas">H.Téc</div><div style="width:${W.hf}px;padding:6px 4px;box-sizing:border-box;text-align:right" title="Horas funcionales">H.Func</div>
        </div>
        <div style="position:relative;width:${TRACK}px;flex-shrink:0">
          ${weeks.map((d,i) => `<div style="position:absolute;left:${i*WK}px;width:${WK}px;box-sizing:border-box;border-left:1px solid #c9d4e4;padding:3px 2px;text-align:center;line-height:1.25">Sem ${i+1}<div style="color:#8693a8;font-weight:400">${d.getDate()} ${MES[d.getMonth()]}</div></div>`).join("")}
        </div></div>`;
    const rowHtml = (r) => `<div style="display:flex;border-bottom:1px solid #eef1f6;height:${ROWH}px;align-items:center;font-size:11px">
        <div style="display:flex;flex-shrink:0;position:sticky;left:0;z-index:2;background:#fff;width:${LW}px;height:100%;align-items:center">
          <div style="width:${W.wp}px;padding:0 6px;box-sizing:border-box;color:#8693a8">${wpCell(r)}</div>
          <div style="width:${W.pr}px;padding:0 5px;box-sizing:border-box">${prioCell(r)}</div>
          <div style="width:${W.req}px;padding:0 6px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escapeHtml(r.subject||"")}"><b>${escapeHtml(r.subject||"")}</b></div>
          <div style="width:${W.fu}px;padding:0 4px;box-sizing:border-box">${ediCell(r,"funcional")}</div>
          <div style="width:${W.de}px;padding:0 4px;box-sizing:border-box">${ediCell(r,"desarrollador")}</div>
          <div style="width:${W.uc}px;padding:0 4px;box-sizing:border-box">${ediCell(r,"usuario_clave")}</div>
          <div style="width:${W.ini}px;padding:0 6px;box-sizing:border-box;color:#556">${fmtD(r.inicio)}</div>
          <div style="width:${W.fin}px;padding:0 4px;box-sizing:border-box"><input type="date" class="gfin" data-wp="${r.id}" value="${r.fin||""}" title="Cambiar fecha final (recalcula el inicio)" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px"></div>
          <div style="width:${W.ht}px;padding:0 3px;box-sizing:border-box"><input type="number" class="ghtec" data-wp="${r.id}" value="${r.horas_tecnicas!=null?r.horas_tecnicas:""}" min="0" step="4" title="Horas técnicas (suma con funcionales → recalcula el inicio)" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px;text-align:right;font-weight:600"></div>
          <div style="width:${W.hf}px;padding:0 3px;box-sizing:border-box"><input type="number" class="ghfunc" data-wp="${r.id}" value="${r.horas_funcionales!=null?r.horas_funcionales:""}" min="0" step="4" title="Horas funcionales (suma con técnicas → recalcula el inicio)" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px;text-align:right;font-weight:600"></div>
        </div>
        <div style="position:relative;width:${TRACK}px;height:100%;flex-shrink:0;background:repeating-linear-gradient(to right,transparent 0 ${WK-1}px,#c9d4e4 ${WK-1}px ${WK}px)">${bar(r)}</div>
      </div>`;
    const band = (c) => `<div style="width:${TOT}px;background:#e7edf6;border-top:1px solid #d3dceb;border-bottom:1px solid #d3dceb;font-weight:700;font-size:12px;color:#1a3a52;padding:4px 8px;box-sizing:border-box;position:sticky;left:0">🎯 ${escapeHtml(c.capability)} <span style="color:#8693a8;font-weight:400;margin-left:6px">(${c.requisitos.length})</span></div>`;
    const legend = Object.keys(ETAPA_LBL).map(k => `<span style="display:inline-flex;align-items:center;gap:4px;margin-right:8px"><span style="width:11px;height:11px;border-radius:2px;background:${ETAPA_COL[k]};display:inline-block"></span>${ETAPA_LBL[k]}</span>`).join("");
    const prioFilterOpts = `<option value="">Prioridad: todas</option>` + prios.map(p => `<option value="${p.codigo}" ${fPrio===p.codigo?"selected":""}>${escapeHtml(p.label)}</option>`).join("");
    $r.innerHTML = `
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px;font-size:12px">
        <select id="f-prio" style="font-size:12px;padding:5px 8px;border:1px solid #cdd6e4;border-radius:4px">${prioFilterOpts}</select>
        <label style="color:#667">Inicio ≥ <input type="date" id="f-ini" value="${fIni}" style="font-size:12px;padding:4px 6px;border:1px solid #cdd6e4;border-radius:4px"></label>
        <label style="color:#667">Fin ≤ <input type="date" id="f-fin" value="${fFin}" style="font-size:12px;padding:4px 6px;border:1px solid #cdd6e4;border-radius:4px"></label>
        <button id="f-clear" style="font-size:12px;padding:5px 10px;border:1px solid #cdd6e4;border-radius:4px;background:#fff;cursor:pointer">Limpiar</button>
        <span style="margin-left:auto;font-weight:700;color:#1a3a52">💪 Esfuerzo: ${totalH.toLocaleString("es")} h <span style="font-weight:400;color:#8693a8">· ${visible.length} req</span></span>
      </div>
      <div style="font-size:11px;color:#8693a8;margin-bottom:6px">${legend}</div>
      ${visible.length ? `<div style="overflow:auto;max-height:66vh;border:1px solid #d3dceb;border-radius:6px"><div style="min-width:${TOT}px">${header}${fcaps.map(c => band(c) + c.requisitos.map(rowHtml).join("")).join("")}</div></div>`
        : `<div class="empty" style="padding:18px">Ningún requerimiento cumple los filtros.</div>`}`;
    wire();
  }

  function wire() {
    const $p = document.getElementById("f-prio"), $i = document.getElementById("f-ini"), $f = document.getElementById("f-fin");
    if ($p) $p.onchange = () => { fPrio = $p.value; render(); };
    if ($i) $i.onchange = () => { fIni = $i.value; render(); };
    if ($f) $f.onchange = () => { fFin = $f.value; render(); };
    const $c = document.getElementById("f-clear");
    if ($c) $c.onclick = () => { fPrio = ""; fIni = ""; fFin = ""; render(); };
    const save = async (wp, field, value) => {
      const res = await fetch(`/api/proyectos/wp/${wp}/campo`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({field, value}) });
      if (!res.ok) throw new Error((await res.json().catch(()=>({}))).detail || res.status);
    };
    $r.querySelectorAll(".gprio").forEach(sel => sel.onchange = async () => {
      const wp = sel.dataset.wp; sel.style.background = PRIO_COL[sel.value] || "#6a6d70";
      try { const res = await fetch(`/api/proyectos/wp/${wp}/prioridad`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prioridad:sel.value})});
        if (!res.ok) throw new Error((await res.json()).detail||res.status); byWp[wp].prioridad = sel.value; if (fPrio) render(); }
      catch(e){ alert("No se pudo fijar la prioridad: "+e.message); }
    });
    $r.querySelectorAll(".gedit").forEach(sel => sel.onchange = async () => {
      const wp = sel.dataset.wp, field = sel.dataset.field;
      try { await save(wp, field, sel.value); byWp[wp][field] = sel.value || null; }
      catch(e){ alert("No se pudo guardar: "+e.message); }
    });
    $r.querySelectorAll(".gfin").forEach(inp => inp.onchange = async () => {
      const wp = sel_wp(inp), v = inp.value; if (!v) return;
      try { await save(wp, "fin", v); const r = byWp[wp]; r.fin = v;
        const n = Math.max(1, Math.round((+r.horas||8)/8)); r.inicio = wdBack(v, n); render(); }
      catch(e){ alert("No se pudo guardar la fecha: "+e.message); }
    });
    // Horas técnicas / funcionales (mig-014): guarda la componente, recompone
    // r.horas = suma (mueve la barra) y recalcula el inicio.
    const saveHoras = async (wp, field, v) => {
      await save(wp, field, v);
      const r = byWp[wp];
      if (field === "horas_tecnicas") r.horas_tecnicas = v ? +v : null;
      else r.horas_funcionales = v ? +v : null;
      const tot = (+r.horas_tecnicas||0) + (+r.horas_funcionales||0);
      r.horas = tot || null;
      if (r.fin && r.horas) { const n = Math.max(1, Math.round(r.horas/8)); r.inicio = wdBack(r.fin, n); }
      render();
    };
    $r.querySelectorAll(".ghtec").forEach(inp => inp.onchange = async () => {
      try { await saveHoras(sel_wp(inp), "horas_tecnicas", inp.value); }
      catch(e){ alert("No se pudo guardar horas técnicas: "+e.message); }
    });
    $r.querySelectorAll(".ghfunc").forEach(inp => inp.onchange = async () => {
      try { await saveHoras(sel_wp(inp), "horas_funcionales", inp.value); }
      catch(e){ alert("No se pudo guardar horas funcionales: "+e.message); }
    });
    function sel_wp(el){ return el.dataset.wp; }
  }

  render();
}

async function renderGestionProyectos() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "gestproy"));
  const $c = document.getElementById("content");
  $c.innerHTML = `<div class="panel"><h2>🗂 Proyectos</h2><div class="empty">cargando...</div></div>`;
  let clientes = [], projects = [], users = [];
  try {
    clientes = ((await (await fetch("/api/clientes")).json()).clientes) || [];
    projects = ((await (await fetch("/api/proyectos")).json()).projects) || [];
    users = ((await (await fetch("/api/usuarios")).json()).users) || [];
  } catch (e) {
    $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`; return;
  }
  const userItems = users.map(u => ({ value: u.username, label: u.name ? `${u.name} (${u.username})` : u.username }));
  const multiPick = (cls, items, sel) => {
    const s = (sel || []).map(String);
    const n = s.length;
    const summary = n ? `${n} seleccionado${n > 1 ? "s" : ""}` : "— ninguno —";
    const opts = items.map(it =>
      `<label><input type="checkbox" value="${escapeHtml(String(it.value))}" ${s.includes(String(it.value)) ? "checked" : ""}/> ${escapeHtml(String(it.label))}</label>`).join("");
    return `<details class="ms ${cls}"><summary>${summary}</summary><div class="ms-pop">${opts}</div></details>`;
  };
  $c.innerHTML = `
    <div class="panel">
      <h2>🗂 Proyectos</h2>
      <div class="soon" style="margin-bottom:14px">Crea un proyecto en <b>proyecto-db</b>: el <b>cliente</b> (su slug = la org de Gitea, igual que provisionar_repo), <b>capacidades</b> (segmentación fina, opcional) y los <b>usuarios</b> con acceso. Si el cliente ya existe se reutiliza.</div>
      <h3>Nuevo proyecto</h3>
      <form id="new-proj" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;max-width:680px">
        <input name="cliente" list="cli-list" placeholder="cliente (existente o nuevo)" required/>
        <datalist id="cli-list">${clientes.map(c => `<option value="${escapeHtml(c.nombre)}"></option>`).join("")}</datalist>
        <input name="proyecto" placeholder="nombre del proyecto" required/>
        <input name="capacidades" placeholder="capacidades NUEVAS separadas por coma (FI, MM, …) — opcional" style="grid-column:1/3"/>
        <div id="clone-caps" style="grid-column:1/3"></div>
        <div style="grid-column:1/3"><b>Usuarios con acceso:</b> ${multiPick("np-users", userItems, [])}</div>
        <button type="submit" style="grid-column:1/3;justify-self:start;background:#1a3a52;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">Crear proyecto</button>
      </form>
      <h3 style="margin-top:16px">Proyectos existentes (${projects.length})</h3>
      <table class="rep">
        <tr><th>Cliente · Proyecto</th><th>ID</th></tr>
        ${projects.map(p => `<tr><td>${escapeHtml(String(p.name))}</td><td style="color:#888">${p.id}</td></tr>`).join("")}
      </table>
    </div>`;
  // Al elegir un cliente que YA existe, ofrece clonar sus capacidades.
  const cliNames = new Set(clientes.map(c => (c.nombre || "").trim().toLowerCase()));
  const $cli = document.querySelector('#new-proj [name=cliente]');
  const $clone = document.getElementById("clone-caps");
  const hint = (txt) => `<div style="font-size:11px;color:#888;margin-top:2px">${txt}</div>`;
  let _lastCli = null;
  const refreshClonables = async () => {
    const v = ($cli.value || "").trim();
    if (v === _lastCli) return;        // evita refetch en cada tecla sin cambio real
    _lastCli = v;
    if (!v) { $clone.innerHTML = hint("💡 Elige un <b>cliente existente</b> (de la lista) para clonar sus capacidades."); return; }
    if (!cliNames.has(v.toLowerCase())) { $clone.innerHTML = hint(`Cliente nuevo «${escapeHtml(v)}»: no hay capacidades previas; escríbelas arriba.`); return; }
    $clone.innerHTML = hint("cargando capacidades…");
    let caps = [];
    try { caps = ((await (await fetch(`/api/capacidades?cliente=${encodeURIComponent(v)}`)).json()).capacidades) || []; }
    catch (e) { caps = []; }
    if (!caps.length) { $clone.innerHTML = hint(`El cliente <b>${escapeHtml(v)}</b> no tiene capacidades previas para clonar.`); return; }
    const items = caps.map(n => ({ value: n, label: n }));
    $clone.innerHTML = `<b>Clonar capacidades de ${escapeHtml(v)}</b> (${caps.length}): ${multiPick("np-clonecaps", items, [])}`;
    $clone.querySelectorAll(".ms").forEach(ms => ms.addEventListener("change", () => {
      const n = ms.querySelectorAll('input:checked').length;
      ms.querySelector("summary").textContent = n ? `${n} seleccionado${n > 1 ? "s" : ""}` : "— ninguno —";
    }));
  };
  $cli.addEventListener("input", refreshClonables);
  $cli.addEventListener("change", refreshClonables);
  refreshClonables();   // pista inicial visible
  document.getElementById("new-proj").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const usuarios = [...f.querySelectorAll('.np-users input:checked')].map(c => c.value);
    const nuevas = (f.capacidades.value || "").split(",").map(s => s.trim()).filter(Boolean);
    const clonadas = [...f.querySelectorAll('.np-clonecaps input:checked')].map(c => c.value);
    const capacidades = [...new Set([...clonadas, ...nuevas])];
    const body = { cliente: f.cliente.value, proyecto: f.proyecto.value, capacidades, usuarios };
    const r = await (await fetch("/api/proyectos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    if (r.ok) {
      const p = r.proyecto || {};
      alert(`Proyecto creado: ${p.cliente} · ${p.proyecto}\n` +
            `${(p.capacidades || []).length} capacidad(es), ${(p.usuarios || []).length} usuario(s).`);
      renderGestionProyectos();
    } else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  };
  // Resumen en vivo del picklist de usuarios.
  $c.querySelectorAll(".ms").forEach(ms => ms.addEventListener("change", () => {
    const n = ms.querySelectorAll('input:checked').length;
    ms.querySelector("summary").textContent = n ? `${n} seleccionado${n > 1 ? "s" : ""}` : "— ninguno —";
  }));
}

async function renderSapSystems() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "sapsys"));
  const $c = document.getElementById("content");
  $c.innerHTML = `<div class="panel"><h2>🛢 Sistemas SAP</h2><div class="empty">cargando...</div></div>`;
  let clientes = [], systems = [];
  try {
    const d = await (await fetch("/api/sap-systems")).json();
    clientes = d.clientes || []; systems = d.systems || [];
  } catch (e) {
    $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`; return;
  }
  const yes = (b) => b ? "sí" : "no";
  $c.innerHTML = `
    <div class="panel">
      <h2>🛢 Sistemas SAP</h2>
      <div class="soon" style="margin-bottom:14px">Sistemas SAP del <b>MCP ABAP</b>, uno por cliente. La <b>credencial</b> se guarda cifrada en AWS SSM (<code>/fabrica-sw/abap/&lt;ID&gt;</code>); aquí solo se lista lo no-secreto. El <b>ID del sistema</b> es el que Claude Code manda en el header <code>x-sap-system-id</code>. Tras crear o borrar, <b>reinicia el contenedor <code>abap-mcp</code></b> para que re-siembre.</div>
      <h3>Nuevo / editar sistema</h3>
      <form id="new-sys" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;max-width:680px">
        <input name="cliente" list="cli-list-sap" placeholder="cliente (existente o nuevo)" required/>
        <datalist id="cli-list-sap">${clientes.map(c => `<option value="${escapeHtml(c.nombre)}"></option>`).join("")}</datalist>
        <input name="system_id" placeholder="ID del sistema (p.ej. CLIX-DEV)" required/>
        <input name="host" placeholder="host:puerto (p.ej. sap.clix.com:44300)" required/>
        <input name="sap_client" placeholder="mandante (default 100)" value="100"/>
        <input name="username" placeholder="usuario SAP" required/>
        <input name="password" type="password" placeholder="password (vacío al editar = conservar)"/>
        <input name="description" placeholder="descripción (opcional)" style="grid-column:1/3"/>
        <label style="grid-column:1/3"><input type="checkbox" name="secure" checked/> HTTPS (secure)</label>
        <button type="submit" style="grid-column:1/3;justify-self:start;background:#1a3a52;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">Guardar sistema</button>
      </form>
      <h3 style="margin-top:16px">Sistemas registrados (${systems.length})</h3>
      <table class="rep">
        <tr><th>Cliente</th><th>ID</th><th>Host</th><th>Mandante</th><th>Usuario</th><th>HTTPS</th><th>Descripción</th><th></th></tr>
        ${systems.map(s => `<tr>
          <td>${escapeHtml(String(s.cliente))}</td>
          <td><b>${escapeHtml(String(s.system_id))}</b></td>
          <td>${escapeHtml(String(s.host))}</td>
          <td>${escapeHtml(String(s.sap_client))}</td>
          <td>${escapeHtml(String(s.username))}</td>
          <td>${yes(s.secure)}</td>
          <td style="color:#666">${escapeHtml(String(s.description || ""))}</td>
          <td style="white-space:nowrap">
            <button class="edit-sys" data-sys='${escapeHtml(JSON.stringify(s))}' style="cursor:pointer">✏️</button>
            <button class="del-sys" data-id="${escapeHtml(String(s.system_id))}" style="cursor:pointer;color:#b00">🗑</button>
          </td></tr>`).join("")}
      </table>
    </div>`;
  const f = document.getElementById("new-sys");
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = {
      cliente: f.cliente.value, system_id: f.system_id.value, host: f.host.value,
      sap_client: f.sap_client.value || "100", username: f.username.value,
      password: f.password.value, description: f.description.value,
      secure: f.secure.checked,
    };
    const r = await (await fetch("/api/sap-systems", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    if (r.ok) {
      alert(`Sistema guardado: ${r.system.system_id}\nCredencial en SSM: ${r.system.ssm_param}\n\nReinicia el contenedor abap-mcp para aplicarlo.`);
      renderSapSystems();
    } else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  };
  $c.querySelectorAll(".edit-sys").forEach(b => b.onclick = () => {
    const s = JSON.parse(b.dataset.sys);
    f.cliente.value = s.cliente; f.system_id.value = s.system_id; f.host.value = s.host;
    f.sap_client.value = s.sap_client; f.username.value = s.username;
    f.description.value = s.description || ""; f.secure.checked = !!s.secure; f.password.value = "";
    f.scrollIntoView({ behavior: "smooth" });
  });
  $c.querySelectorAll(".del-sys").forEach(b => b.onclick = async () => {
    const id = b.dataset.id;
    if (!confirm(`¿Borrar el sistema «${id}»? Se elimina también su credencial en SSM.`)) return;
    const r = await (await fetch(`/api/sap-systems/${encodeURIComponent(id)}`, { method: "DELETE" })).json();
    if (r.ok) { alert(`Sistema «${id}» borrado.\nReinicia abap-mcp para aplicarlo.`); renderSapSystems(); }
    else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  });
}

async function renderUsuarios() {
  document.querySelectorAll(".nav-item").forEach(i =>
    i.classList.toggle("active", i.dataset.section === "usuarios"));
  const $c = document.getElementById("content");
  $c.innerHTML = `<div class="panel"><h2>👥 Usuarios</h2><div class="empty">cargando...</div></div>`;
  let data;
  try { data = await (await fetch("/api/usuarios")).json(); }
  catch (e) { $c.innerHTML = `<div class="panel"><div class="soon">Error: ${escapeHtml(e.message)}</div></div>`; return; }
  if (data.detail) { $c.innerHTML = `<div class="panel"><div class="soon">${escapeHtml(data.detail)}</div></div>`; return; }
  const roles = data.roles || [];
  let projects = [];
  try { projects = ((await (await fetch("/api/proyectos")).json()).projects) || []; }
  catch (e) { projects = []; }
  // Picklist múltiple compacto (colapsado). `cls` = clase para leer luego los
  // checks; `items` = [{value,label}]; `sel` = valores seleccionados (string).
  const multiPick = (cls, items, sel) => {
    const s = (sel || []).map(String);
    const n = s.length;
    const summary = n ? `${n} seleccionado${n > 1 ? "s" : ""}` : "— ninguno —";
    const opts = items.map(it =>
      `<label><input type="checkbox" value="${it.value}" ${s.includes(String(it.value)) ? "checked" : ""}/> ${escapeHtml(String(it.label))}</label>`).join("");
    return `<details class="ms ${cls}"><summary>${summary}</summary><div class="ms-pop">${opts}</div></details>`;
  };
  const roleItems = roles.map(r => ({ value: r, label: r }));
  // Rol principal (por prioridad) para mostrar de un vistazo + "+N" si hay más.
  const ROLE_PRIORITY = ["admin", "swf-project-manager", "project-manager",
    "swf-funcional", "funcional", "usuario-clave", "swf-desarrollador", "swf-tester"];
  const primaryRole = (rs) => { for (const r of ROLE_PRIORITY) if ((rs || []).includes(r)) return r; return (rs || [])[0] || "—"; };
  const primaryBadge = (rs) => {
    const p = primaryRole(rs), extra = (rs || []).length - 1;
    return `<span style="background:#eef4fc;color:#0a6ed1;border-radius:10px;padding:2px 8px;font-size:11px;font-weight:600;white-space:nowrap">${escapeHtml(p)}</span>${extra > 0 ? ` <span style="color:#888;font-size:11px">+${extra}</span>` : ""}`;
  };
  // Solo proyectos de ÚLTIMO NIVEL (hoja): los que no son padre de ningún otro.
  const parentIds = new Set(projects.map(p => p.parent_id).filter(x => x != null).map(String));
  const projItems = projects.filter(p => !parentIds.has(String(p.id)))
    .map(p => ({ value: p.id, label: p.name }));
  const am = data.access_map || {};
  const accesos = (uRoles) => Object.entries(am)
    .filter(([s, rs]) => (uRoles || []).some(r => rs.includes(r)))
    .map(([s]) => s).join(" · ") || "—";
  const mapLegend = Object.entries(am)
    .map(([s, rs]) => `<b>${s}</b>: ${rs.join(", ")}`).join(" &nbsp;|&nbsp; ");
  $c.innerHTML = `
    <div class="panel">
      <h2>👥 Usuarios · Keycloak (SSO + roles)</h2>
      <div class="soon" style="margin-bottom:14px">Crear/editar aquí actualiza <b>Keycloak</b> (identidad + roles para SSO) y la segmentación (proyectos/capacidades) en <b>proyecto-db</b>. El acceso a repos se concede por usuario con <b>🐙 Gitea</b>.</div>
      <h3>Nuevo usuario</h3>
      <form id="new-user" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;max-width:680px">
        <input name="username" placeholder="usuario (login)" required/>
        <input name="email" type="email" placeholder="email"/>
        <input name="first" placeholder="nombre"/>
        <input name="last" placeholder="apellido"/>
        <input name="password" placeholder="password temporal" value="Cambiar-2026!"/>
        <div></div>
        <div style="grid-column:1/3"><b>Roles:</b> ${multiPick("nu-roles", roleItems, [])}</div>
        <button type="submit" style="grid-column:1/3;justify-self:start;background:#1a3a52;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">Crear en Keycloak</button>
      </form>
      <div style="font-size:11px;color:#666;margin:10px 0 4px">Accesos (configurable por env): ${mapLegend}</div>
      <h3 style="margin-top:14px">Usuarios (<span id="u-count">${(data.users || []).length}</span>/${(data.users || []).length})</h3>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
        <input id="f-nombre" placeholder="🔎 Nombre/usuario contiene…" style="font-size:12px;padding:5px 9px;border:1px solid #cdd6e4;border-radius:4px;min-width:200px"/>
        <input id="f-correo" placeholder="✉ Correo contiene…" style="font-size:12px;padding:5px 9px;border:1px solid #cdd6e4;border-radius:4px;min-width:200px"/>
        <select id="f-rol" style="font-size:12px;padding:5px 9px;border:1px solid #cdd6e4;border-radius:4px"><option value="">— Rol: todos —</option>${roles.map(r => `<option value="${r}">${r}</option>`).join("")}</select>
        <select id="f-estado" style="font-size:12px;padding:5px 9px;border:1px solid #cdd6e4;border-radius:4px"><option value="">— Estado: todos —</option><option value="1">Activos</option><option value="0">Inactivos</option></select>
        <select id="f-proy" style="font-size:12px;padding:5px 9px;border:1px solid #cdd6e4;border-radius:4px"><option value="">— Proyecto: todos —</option>${projItems.map(p => `<option value="${p.value}">${escapeHtml(String(p.label))}</option>`).join("")}</select>
        <select id="f-seg" style="font-size:12px;padding:5px 9px;border:1px solid #cdd6e4;border-radius:4px"><option value="">— Segmentación: todas —</option><option value="noproj">Sin proyectos</option><option value="nocap">Sin capacidades</option></select>
        <button id="f-clear" style="font-size:12px;padding:5px 10px;border:1px solid #cdd6e4;border-radius:4px;background:#fff;cursor:pointer">Limpiar</button>
      </div>
      <table class="rep">
        <tr><th>Usuario</th><th>Nombre</th><th>Email</th><th>Estado</th><th>Rol principal</th><th>Accesos</th><th>Roles (editable)</th><th>Proyectos (segmentación)</th><th></th></tr>
        ${(data.users || []).map(u => `
          <tr data-uid="${u.id}" data-username="${escapeHtml(u.username || "")}" data-name="${escapeHtml(((u.name || "") + " " + (u.username || "")).toLowerCase())}" data-email="${escapeHtml((u.email || "").toLowerCase())}" data-roles="${escapeHtml((u.roles || []).join(","))}" data-enabled="${u.enabled ? 1 : 0}" data-proyectos="${escapeHtml((u.proyectos || []).map(String).join(","))}" data-ncap="${(u.capacidades || []).length}">
            <td>${escapeHtml(u.username || "")}</td>
            <td>${escapeHtml(u.name || "")}</td>
            <td>${escapeHtml(u.email || "")}</td>
            <td>${u.enabled ? "✅" : "⛔"}</td>
            <td>${primaryBadge(u.roles || [])}</td>
            <td style="font-size:11px">${accesos(u.roles || [])}</td>
            <td>${multiPick("roles-cell", roleItems, u.roles || [])}</td>
            <td>${multiPick("proj-cell", projItems, u.proyectos || [])}
              <div style="font-size:10px;color:#888;margin-top:2px">vacío = sin restricción</div>
            </td>
            <td>
              <div style="display:flex;flex-wrap:wrap;gap:4px">
                <button class="save-roles">💾 roles</button>
                <button class="save-projs">💾 proyectos</button>
                <button class="caps-btn" data-caps="${(u.capacidades || []).length}">🎯 capac. (${(u.capacidades || []).length})</button>
                <button class="set-pwd">🔑 contraseña</button>
                <button class="mcp-token-btn">🔌 token MCP</button>
                <button class="gitea-btn">🐙 Gitea</button>
                <button class="toggle-enabled" data-en="${u.enabled ? 0 : 1}">${u.enabled ? "⛔" : "✅"}</button>
              </div>
            </td>
          </tr>`).join("")}
      </table>
    </div>`;
  document.getElementById("new-user").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const sel = [...f.querySelectorAll('input[type=checkbox]:checked')].map(c => c.value);
    const body = { username: f.username.value, email: f.email.value, first: f.first.value, last: f.last.value, password: f.password.value, roles: sel };
    const r = await (await fetch("/api/usuarios", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    if (r.ok) { alert("Usuario creado en Keycloak."); renderUsuarios(); }
    else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  };
  $c.querySelectorAll("tr[data-uid]").forEach(tr => {
    const uid = tr.dataset.uid;
    tr.querySelector(".save-roles").onclick = async () => {
      const sel = [...tr.querySelectorAll('.roles-cell input:checked')].map(c => c.value);
      await fetch(`/api/usuarios/${uid}/roles`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ roles: sel }) });
      alert("Roles actualizados en Keycloak.");
    };
    tr.querySelector(".save-projs").onclick = async () => {
      const sel = [...tr.querySelectorAll('.proj-cell input:checked')].map(c => c.value);
      const r = await (await fetch(`/api/usuarios/${uid}/proyectos`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_ids: sel }) })).json();
      alert(`Proyectos guardados (Keycloak + proyecto-db)${r.detail ? " — " + r.detail : ""}`);
    };
    tr.querySelector(".caps-btn").onclick = () => openCapsModal(uid, tr);
    tr.querySelector(".set-pwd").onclick = () => openPwdModal(uid, tr);
    tr.querySelector(".mcp-token-btn").onclick = () => openMcpTokenModal(tr.dataset.username, (tr.dataset.roles || "").split(",").filter(Boolean));
    tr.querySelector(".gitea-btn").onclick = () => openGiteaModal(tr.dataset.username, (tr.dataset.roles || "").split(",").filter(Boolean), tr.dataset.email);
    tr.querySelector(".toggle-enabled").onclick = async (ev) => {
      const en = ev.target.dataset.en === "1";
      await fetch(`/api/usuarios/${uid}/enabled`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: en }) });
      renderUsuarios();
    };
  });
  // Resumen en vivo del picklist: al marcar/desmarcar, refleja el conteo.
  $c.querySelectorAll(".ms").forEach(ms => {
    ms.addEventListener("change", () => {
      const n = ms.querySelectorAll('input:checked').length;
      const sum = ms.querySelector("summary");
      if (sum) sum.textContent = n ? `${n} seleccionado${n > 1 ? "s" : ""}` : "— ninguno —";
    });
  });
  // Filtros de usuarios (cliente): nombre/usuario, correo, rol, estado.
  const $fn = document.getElementById("f-nombre");
  const $fc = document.getElementById("f-correo");
  const $fr = document.getElementById("f-rol");
  const $fe = document.getElementById("f-estado");
  const $fp = document.getElementById("f-proy");
  const $fs = document.getElementById("f-seg");
  const $cnt = document.getElementById("u-count");
  function applyUserFilters() {
    const n = ($fn.value || "").trim().toLowerCase();
    const c = ($fc.value || "").trim().toLowerCase();
    const rol = $fr.value, est = $fe.value, proy = $fp.value, seg = $fs.value;
    let shown = 0;
    $c.querySelectorAll("tr[data-uid]").forEach(tr => {
      const proyList = (tr.dataset.proyectos || "").split(",").filter(Boolean);
      const ncap = Number(tr.dataset.ncap || 0);
      const okN = !n || (tr.dataset.name || "").includes(n);
      const okC = !c || (tr.dataset.email || "").includes(c);
      const okR = !rol || (tr.dataset.roles || "").split(",").includes(rol);
      const okE = !est || (tr.dataset.enabled || "") === est;
      const okP = !proy || proyList.includes(proy);
      const okS = !seg || (seg === "noproj" ? proyList.length === 0 : ncap === 0);
      const vis = okN && okC && okR && okE && okP && okS;
      tr.style.display = vis ? "" : "none";
      if (vis) shown++;
    });
    if ($cnt) $cnt.textContent = shown;
  }
  [$fn, $fc].forEach(el => el && el.addEventListener("input", applyUserFilters));
  [$fr, $fe, $fp, $fs].forEach(el => el && el.addEventListener("change", applyUserFilters));
  const $fclr = document.getElementById("f-clear");
  if ($fclr) $fclr.onclick = () => {
    $fn.value = ""; $fc.value = ""; $fr.value = ""; $fe.value = "";
    $fp.value = ""; $fs.value = ""; applyUserFilters();
  };
}

// Modal de capacidades por usuario: lista las capacidades de SUS proyectos
// asignados (agrupadas por proyecto) y guarda la selección en Keycloak.
async function openCapsModal(uid, tr) {
  let data;
  try { data = await (await fetch(`/api/usuarios/${uid}/capacidades-disponibles`)).json(); }
  catch (e) { alert("Error: " + e.message); return; }
  const sel = new Set((data.selected || []).map(String));
  const grupos = data.grupos || [];
  let bodyHtml;
  if (data.sin_proyectos) {
    bodyHtml = `<div style="color:#856404;background:#fff8e1;padding:10px;border-radius:4px">Este usuario no tiene proyectos asignados. Asígnale proyectos primero (💾 proyectos).</div>`;
  } else {
    const noCaps = grupos.every(g => !(g.capabilities || []).length);
    bodyHtml = noCaps
      ? `<div style="color:#666">Sus proyectos no tienen capacidades (WP tipo Capability). Vacío = ve todas las del proyecto.</div>`
      : grupos.map(g => `
        <div style="margin-bottom:10px">
          <div style="font-weight:700;color:#1a3a52;font-size:13px;margin-bottom:4px">📁 ${escapeHtml(g.project_name)}</div>
          ${(g.capabilities || []).length
            ? (g.capabilities || []).map(c => `<label style="display:block;font-size:13px;padding:2px 0"><input type="checkbox" class="cap-cb" value="${c.id}" ${sel.has(String(c.id)) ? "checked" : ""}/> 🎯 ${escapeHtml(c.subject || ("#" + c.id))}</label>`).join("")
            : `<div style="font-size:12px;color:#999;padding-left:6px">(sin capacidades)</div>`}
        </div>`).join("");
  }
  const modal = document.createElement("div");
  modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:1000";
  modal.innerHTML = `<div style="background:#fff;border-radius:8px;padding:20px;width:480px;max-width:92vw;max-height:80vh;overflow:auto;box-shadow:0 8px 30px rgba(0,0,0,.2)">
      <h3 style="margin:0 0 4px">🎯 Capacidades del usuario</h3>
      <div style="font-size:12px;color:#888;margin-bottom:12px">Vacío = ve todos los requisitos de sus proyectos. Con selección = solo esas capacidades.</div>
      <div id="caps-body">${bodyHtml}</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
        <button id="caps-cancel" style="cursor:pointer">Cancelar</button>
        <button id="caps-save" style="background:#1a3a52;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">💾 Guardar</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector("#caps-cancel").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };
  modal.querySelector("#caps-save").onclick = async () => {
    const ids = [...modal.querySelectorAll(".cap-cb:checked")].map(c => c.value);
    const r = await (await fetch(`/api/usuarios/${uid}/capacidades`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ capability_ids: ids }) })).json();
    if (r.ok) {
      const btn = tr && tr.querySelector(".caps-btn");
      if (btn) btn.textContent = `🎯 capacidades (${ids.length})`;
      close();
      alert("Capacidades guardadas en Keycloak.");
    } else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  };
}

// Modal de cambio de contraseña (acción de admin). Fija una contraseña nueva
// vía Keycloak Admin API. "Temporal" = el usuario debe cambiarla al entrar.
function openPwdModal(uid, tr) {
  const login = tr ? (tr.querySelector("td")?.textContent || "") : "";
  const modal = document.createElement("div");
  modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:1000";
  modal.innerHTML = `<div style="background:#fff;border-radius:8px;padding:20px;width:420px;max-width:92vw;box-shadow:0 8px 30px rgba(0,0,0,.2)">
      <h3 style="margin:0 0 4px">🔑 Cambiar contraseña</h3>
      <div style="font-size:12px;color:#888;margin-bottom:12px">Usuario: <b>${escapeHtml(login)}</b></div>
      <input id="pwd-input" type="password" placeholder="Contraseña nueva (mín. 8)" autocomplete="new-password" style="width:100%;box-sizing:border-box;padding:8px;border:1px solid #cdd6e4;border-radius:4px;font-size:14px"/>
      <label style="display:flex;align-items:center;gap:6px;font-size:13px;margin-top:10px">
        <input id="pwd-temp" type="checkbox" checked/> Temporal (el usuario la cambia en su próximo login)
      </label>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
        <button id="pwd-cancel" style="cursor:pointer">Cancelar</button>
        <button id="pwd-save" style="background:#1a3a52;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">💾 Guardar</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  const $inp = modal.querySelector("#pwd-input");
  $inp.focus();
  modal.querySelector("#pwd-cancel").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };
  modal.querySelector("#pwd-save").onclick = async () => {
    const pwd = $inp.value;
    if (!pwd || pwd.length < 8) { alert("La contraseña debe tener al menos 8 caracteres."); return; }
    const temporary = modal.querySelector("#pwd-temp").checked;
    const r = await (await fetch(`/api/usuarios/${uid}/password`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pwd, temporary }) })).json();
    if (r.ok) { close(); alert(`Contraseña cambiada${temporary ? " (temporal: el usuario la cambiará al entrar)" : ""}.`); }
    else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  };
  $inp.addEventListener("keydown", (e) => { if (e.key === "Enter") modal.querySelector("#pwd-save").click(); });
}

// Token personal de MCP del usuario (para Claude Code). Generar (se muestra una
// vez) / listar / revocar. El usuario lo pone en su env como FABRICA_MCP_TOKEN.
async function openMcpTokenModal(username, roles) {
  const modal = document.createElement("div");
  modal.style.cssText = "position:fixed;inset:0;background:#0008;z-index:100;display:flex;align-items:center;justify-content:center";
  modal.innerHTML = `<div style="background:#fff;border-radius:8px;padding:22px;width:560px;max-width:92vw">
    <h3 style="margin:0 0 4px">🔌 Token MCP — ${escapeHtml(username)}</h3>
    <div style="font-size:12px;color:#666;margin-bottom:12px">Para Claude Code. Roles: <b>${escapeHtml((roles||[]).join(", ") || "—")}</b>. Se muestra una sola vez; el usuario lo pone en su entorno como <code>FABRICA_MCP_TOKEN</code>.</div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
      <input id="mt-desc" placeholder="descripción (ej: laptop Juan)" style="flex:1;padding:6px 9px;border:1px solid #ccc;border-radius:5px;font-size:13px"/>
      <button id="mt-gen" style="background:#107e3e;color:#fff;border:none;padding:7px 14px;border-radius:5px;cursor:pointer;font-weight:600">Generar</button>
    </div>
    <div id="mt-new"></div>
    <h4 style="margin:14px 0 6px;font-size:13px">Tokens existentes</h4>
    <div id="mt-list" style="font-size:13px"><div style="color:#888">cargando…</div></div>
    <div style="text-align:right;margin-top:14px"><button id="mt-close" style="padding:7px 14px;border:1px solid #ccc;border-radius:5px;background:#fff;cursor:pointer">Cerrar</button></div>
  </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector("#mt-close").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };
  const $list = modal.querySelector("#mt-list");
  const refresh = async () => {
    try {
      const r = await (await fetch(`/api/usuarios/${encodeURIComponent(username)}/mcp-tokens`)).json();
      const ts = r.tokens || [];
      $list.innerHTML = ts.length ? ts.map(t => `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #f0f0f0">
        <span style="flex:1">${t.revocado ? "🚫 " : "🟢 "}${escapeHtml(t.descripcion || "(sin descripción)")} <span style="color:#999;font-size:11px">· creado ${new Date(t.creado).toLocaleDateString("es")}${t.ultimo_uso ? " · usado " + new Date(t.ultimo_uso).toLocaleDateString("es") : ""}</span></span>
        ${t.revocado ? "" : `<button data-id="${t.id}" class="mt-rev" style="font-size:11px;color:#bb0000;border:1px solid #bb0000;background:#fff;border-radius:4px;padding:2px 8px;cursor:pointer">Revocar</button>`}
      </div>`).join("") : `<div style="color:#888">Sin tokens.</div>`;
      $list.querySelectorAll(".mt-rev").forEach(b => b.onclick = async () => {
        if (!confirm("¿Revocar este token? Claude Code dejará de poder usarlo.")) return;
        await fetch(`/api/usuarios/mcp-token/${b.dataset.id}/revoke`, { method: "POST" });
        refresh();
      });
    } catch (e) { $list.innerHTML = `<div style="color:#c33">${escapeHtml(e.message)}</div>`; }
  };
  modal.querySelector("#mt-gen").onclick = async () => {
    const desc = modal.querySelector("#mt-desc").value;
    const r = await (await fetch(`/api/usuarios/${encodeURIComponent(username)}/mcp-token`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ roles, descripcion: desc }) })).json();
    if (r.token) {
      modal.querySelector("#mt-new").innerHTML = `<div style="background:#eafbef;border:1px solid #abe3bd;border-radius:6px;padding:10px;margin-bottom:8px">
        <div style="font-size:12px;color:#107e3e;font-weight:600;margin-bottom:4px">✅ Token generado (cópialo ahora, no se vuelve a mostrar):</div>
        <code style="display:block;word-break:break-all;background:#fff;border:1px solid #cde;border-radius:4px;padding:6px;font-size:12px">${escapeHtml(r.token)}</code>
        <button id="mt-copy" style="margin-top:6px;font-size:11px;border:1px solid #107e3e;color:#107e3e;background:#fff;border-radius:4px;padding:3px 10px;cursor:pointer">Copiar</button>
      </div>`;
      const cp = modal.querySelector("#mt-copy");
      if (cp) cp.onclick = () => { navigator.clipboard.writeText(r.token); cp.textContent = "Copiado ✓"; };
      modal.querySelector("#mt-desc").value = "";
      refresh();
    } else { alert("Error: " + (r.detail || JSON.stringify(r))); }
  };
  refresh();
}

// Provisioning de acceso Gitea por usuario: cuenta + membresía a las orgs de sus
// clientes (segmentado). Muestra el password una vez si crea la cuenta.
async function openGiteaModal(username, roles, email) {
  const modal = document.createElement("div");
  modal.style.cssText = "position:fixed;inset:0;background:#0008;z-index:100;display:flex;align-items:center;justify-content:center";
  modal.innerHTML = `<div style="background:#fff;border-radius:8px;padding:22px;width:540px;max-width:92vw">
    <h3 style="margin:0 0 4px">🐙 Acceso Gitea — ${escapeHtml(username)}</h3>
    <div style="font-size:12px;color:#666;margin-bottom:12px">Garantiza su cuenta Gitea y lo agrega (escritura) a las <b>orgs de los clientes que tiene asignados</b>. Así clona/empuja solo los repos de su cliente.</div>
    <button id="gp-go" style="background:#107e3e;color:#fff;border:none;padding:8px 16px;border-radius:5px;cursor:pointer;font-weight:600">Provisionar acceso</button>
    <div id="gp-out" style="margin-top:12px;font-size:13px"></div>
    <div style="text-align:right;margin-top:14px"><button id="gp-close" style="padding:7px 14px;border:1px solid #ccc;border-radius:5px;background:#fff;cursor:pointer">Cerrar</button></div>
  </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector("#gp-close").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };
  modal.querySelector("#gp-go").onclick = async () => {
    const $out = modal.querySelector("#gp-out");
    $out.innerHTML = "<span style='color:#888'>provisionando…</span>";
    try {
      const r = await (await fetch(`/api/usuarios/${encodeURIComponent(username)}/gitea-provision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ roles, email }) })).json();
      if (r.detail) { $out.innerHTML = `<span style="color:#c33">${escapeHtml(r.detail)}</span>`; return; }
      let html = "";
      if (r.estado === "created" && r.password) {
        html += `<div style="background:#eafbef;border:1px solid #abe3bd;border-radius:6px;padding:10px;margin-bottom:8px">
          <div style="font-size:12px;color:#107e3e;font-weight:600;margin-bottom:4px">✅ Cuenta Gitea creada. Password (cópialo ahora):</div>
          <code style="display:block;word-break:break-all;background:#fff;border:1px solid #cde;border-radius:4px;padding:6px;font-size:12px">${escapeHtml(r.password)}</code>
          <div style="font-size:11px;color:#666;margin-top:4px">Para <code>git clone</code> con usuario+password, o entra a Gitea y crea un token. Cámbialo luego.</div>
        </div>`;
      } else if (r.estado === "exists") {
        html += `<div style="color:#555;margin-bottom:6px">La cuenta Gitea ya existía (no se cambió el password).</div>`;
      } else if (r.estado === "no_admin") {
        html += `<div style="background:#fff4e5;border:1px solid #ffd79a;border-radius:6px;padding:10px;margin-bottom:8px;color:#8a5a00;font-size:12px">No se pudo crear la cuenta Gitea (token sin permisos de admin).</div>`;
      }
      html += `<div><b>Orgs concedidas:</b> ${(r.orgs_concedidas || []).map(escapeHtml).join(", ") || "—"}</div>`;
      if ((r.orgs_pendientes || []).length) html += `<div style="color:#d97400"><b>Pendientes</b> (la org aún no existe en Gitea, se concederá cuando se cree un repo de ese cliente): ${(r.orgs_pendientes).map(escapeHtml).join(", ")}</div>`;
      if (r.nota) html += `<div style="font-size:11px;color:#888;margin-top:6px">${escapeHtml(r.nota)}</div>`;
      $out.innerHTML = html;
    } catch (e) { $out.innerHTML = `<span style="color:#c33">${escapeHtml(e.message)}</span>`; }
  };
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  })[c]);
}
document.querySelectorAll(".nav-item").forEach(el => {
  el.onclick = () => loadSection(el.dataset.section);
});
// Líder de proyecto de CLIENTE (project-manager SIN roles de fábrica): ve SOLO
// el reporte de avance — se oculta el resto del menú y se entra directo ahí.
const _FAB_ROLES = ["admin","swf-project-manager","swf-funcional","swf-desarrollador","swf-tester"];
const CLIENT_ONLY = (currentUser.roles||[]).includes("project-manager")
  && !(currentUser.roles||[]).some(r => _FAB_ROLES.includes(r));
if (CLIENT_ONLY) {
  document.querySelectorAll(".nav-item").forEach(el => {
    if (el.dataset.section !== "reporte") el.style.display = "none";
  });
  loadSection("reporte");
} else {
  loadSection("dashboard");
}
</script>
</body>
</html>"""
