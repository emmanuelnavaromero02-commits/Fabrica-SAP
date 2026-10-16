"""Inbox de la fabrica — SPA web para revisar y operar Requisitos.

UI:   http://localhost:8000/inbox
API:  /api/inbox             lista pendientes
      /api/wp/{id}            detalle WP + iteraciones
      /api/artifact/{...}     archivo de Gitea (md renderizado, html, json)
      /api/wp/{id}/comment    POST comentario al WP
      /api/wp/{id}/review     POST feedback de revision (delega en /factory/{id}/review)
      /api/wp/{id}/decision   POST decision del gate (delega en /factory/{id}/decision)

Diseno: una sola pagina (HTML embebido como string para evitar fricciones
de mount de archivos staticos). CSS + JS embebidos en la misma pagina —
mismo principio que el prototipo del agente. Server-side render del
Markdown (libreria `markdown`). El HTML del prototipo va en iframe con
`sandbox="allow-scripts"` (JS si, mismo-origen no — defensa basica contra
prompt-injection que pida disparar acciones desde el prototipo).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import unicodedata
from typing import Any

log = logging.getLogger("orquestador.inbox")

import markdown as md_lib
import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from temporalio.service import RPCError

import config
import auth
import kc_admin
from clients import Gitea, S3Storage
from etapas import etapa_from_stage, all_stages_for_ui

router = APIRouter()

# Cliente Temporal compartido (lo setea webhook.py durante el lifespan).
_temporal: Any = None


def set_temporal_client(client: Any) -> None:
    global _temporal
    _temporal = client


# Repositorio de artefactos (hoy es solo uno; en el futuro per-proyecto).
# Lo leemos de config para no quedar desincronizados cuando el operador
# renombra el repo en Gitea.
_ARTIFACTS_OWNER = config.FACTORY_GITEA_OWNER or "mcp-agent"
_ARTIFACTS_REPO = config.FACTORY_GITEA_REPO or "fabrica-demo"

# Roles que pueden VER/ACTUALIZAR ítems del inbox (editar metadatos, asignar,
# adjuntar, reiniciar, estimar, etc.). Incluye a los líderes de proyecto
# (project-manager) y de fábrica (swf-project-manager) además de SwF/admin.
_EDITOR_ROLES = {"swf-funcional", "admin", "project-manager",
                 "swf-project-manager"}
# Líderes: ven TODOS los requerimientos del proyecto (como admin) en el inbox.
_LEADER_VIEW_ROLES = {"admin", "project-manager", "swf-project-manager"}


# Etapas de negocio: mapeo y resolución viven en `etapas.py` para que tanto
# inbox.py (renderizado + filtro de visibilidad C2) como workflow.py
# (detección de cambio de etapa C3) compartan la misma lógica.

def _etapa_negocio(stage: str | None, op_status: str | None,
                   running: bool) -> dict:
    """Wrapper que combina la etapa pura (de etapas.py) con runtime info
    (running, action_pending) para el response del API."""
    e = etapa_from_stage(stage, op_status)
    # action_pending = etapa actual espera intervención humana
    s = stage or ""
    action_pending = (
        "esperando_revision" in s or          # loop de diseno Y de desarrollo
        s == "esperando_estimacion" or
        s == "esperando_aprobacion_humana" or
        s == "esperando_vobo_funcional" or
        s == "esperando_vobo_usuario_clave" or
        s == "esperando_liberacion_pruebas" or
        s == "esperando_paquete_diseno" or
        s == "esperando_clarificacion" or
        s.startswith("andamiaje") or
        s.startswith("evidencia")
    )
    # D2: si la etapa es "recibido" y el workflow NO está corriendo, es
    # porque el WP fue creado por un rol cliente (funcional/usuario-clave)
    # y necesita validación de SwF Funcional/admin antes de arrancar.
    needs_validation = (e["key"] == "recibido" and not running and
                        not (s == "completado"))
    if needs_validation:
        action_pending = True
    return {
        "key": e["key"],
        "index": e["index"],
        "icon": e["icon"],
        "label": e["label"],
        "stage": s,                       # stage técnico crudo (sub-paso exacto)
        "responsable_roles": e["responsable_roles"],
        "action_pending": action_pending,
        "is_blocked": e["is_blocked"],
        "running": running,
        "needs_validation": needs_validation,
        "all_stages": all_stages_for_ui(),
    }


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@router.get("/api/inbox")
async def api_inbox(request: Request, scope: str = "mine",
                    project: str | None = None,
                    capability: str | None = None,
                    q: str | None = None) -> dict:
    """Lista los Requisitos visibles para el usuario actual (C2).

    Reglas de visibilidad (scope='mine', default):
      - admin: ve todo (independiente del scope)
      - assignee.title == user.name        → lo veo (es mío)
      - assignee vacío + mi rol primario está en etapa.responsable_roles
                                            → lo veo (en pool de mi rol)
      - assignee otro                       → NO lo veo

    Con scope='all' (solo admins): muestra todo sin filtrar."""
    user = auth.get_current_user(request) or {}
    user_roles = set(user.get("roles") or [])
    username = (user.get("username") or "").strip()
    is_admin = "admin" in user_roles
    # Líderes (admin / project-manager / swf-project-manager) ven TODOS los
    # requerimientos de sus proyectos (no solo su turno).
    is_leader = bool(user_roles & _LEADER_VIEW_ROLES)
    if scope == "all" and not is_leader:
        scope = "mine"
    show_all = (scope == "all" and is_leader)
    q_norm = (q or "").strip()

    # Lista desde proyecto-db (read-model) en vez de OpenProject. La seguridad
    # (roles×proyectos×capacidades) la aplica la propia query. La etapa y "de
    # quién es la pelota" vienen del board; ya no se consulta Temporal por item.
    import proyeccion
    _pid = int(project) if (project and str(project).isdigit()) else None
    _cid = int(capability) if (capability and str(capability).isdigit()) else None
    rows = await proyeccion.listar_inbox(
        username, is_admin, q=q_norm,
        incluir_cerrados=(show_all or bool(q_norm)),
        proyecto_id=_pid, capacidad_id=_cid)

    items = []
    for r in rows:
        # (El filtro por proyecto/capacidad del selector se realinea con
        # proyecto-db en el paso de provisioning; la seguridad ya la aplica la
        # query de listar_inbox.)
        etapa = etapa_from_stage(r.get("etapa_actual"))
        pel_user = (r.get("pelota_usuario") or "")
        pel_rol = r.get("pelota_rol")
        iniciado = bool(r.get("workflow_iniciado"))
        is_mine = bool(username and pel_user
                       and pel_user.lower() == username.lower())
        # En el POOL de mi rol (nadie la ha tomado) → es mi turno también.
        role_match = bool(not pel_user and pel_rol and pel_rol in user_roles)
        is_my_turn = bool(iniciado and (is_mine or role_match))
        # Sin iniciar: lo ve quien puede iniciarlo (Recepción = fábrica/admin).
        puede_iniciar = bool(not iniciado
                             and (_EDITOR_ROLES & user_roles))

        # Visibilidad "mine": mi turno, o sin iniciar y puedo iniciarlo.
        # Los líderes ven todo (de sus proyectos) sin el filtro de turno.
        if not show_all and not is_leader and not q_norm:
            if not (is_mine or role_match or puede_iniciar):
                continue

        pendiente = None
        if not iniciado:
            pendiente = None
        elif is_my_turn:
            pendiente = "gate"
        items.append({
            "wp_id": r.get("wp_id"),
            "subject": r.get("titulo"),
            "status": etapa.get("label"),
            "project": r.get("proyecto"),
            "capacidad": r.get("capacidad"),
            "cliente": r.get("cliente"),
            "stage": r.get("etapa_actual"),
            "pending": pendiente,
            "running": iniciado,
            "iniciado": iniciado,
            "etapa": etapa,
            "assignee": pel_user or None,
            "pelota_rol": pel_rol,
            "is_mine": is_mine,
            "is_creator": False,
            "is_my_turn": is_my_turn,
            "prioridad": r.get("prioridad"),
            "comentarios": r.get("comentarios"),
        })

    return {"items": items, "count": len(items),
            "scope": "all" if show_all else "mine",
            "can_toggle_all": is_leader,
            "project": str(project) if project else None}


@router.get("/api/mis-proyectos")
async def api_mis_proyectos(request: Request) -> dict:
    """Proyectos entre los que el usuario puede elegir en el Inbox. Si tiene
    proyectos asignados (atributo `proyectos` en KC) devuelve SOLO esos y
    `all=false` (debe elegir uno). Si es admin o no tiene asignación, devuelve
    todos y `all=true` (puede ver todo con la opción «Todos»)."""
    user = auth.get_current_user(request) or {}
    is_admin = "admin" in set(user.get("roles") or [])
    import proyeccion
    proys = await proyeccion.mis_proyectos(
        "" if is_admin else (user.get("username") or ""), is_admin)
    projects = [{"id": p["id"], "name": f"{p['cliente']} · {p['nombre']}"}
                for p in proys]
    # all=true (opción «Todos») solo para admin; el resto elige entre los suyos.
    return {"projects": projects, "all": is_admin}


@router.get("/api/mis-capacidades")
async def api_mis_capacidades(request: Request, project: str) -> dict:
    """Capacidades del proyecto entre las que el usuario puede filtrar en el
    Inbox (proyecto-db; segmentado por usuario_capacidad para no-admin)."""
    user = auth.get_current_user(request) or {}
    is_admin = "admin" in set(user.get("roles") or [])
    if not project or not str(project).isdigit():
        return {"capabilities": [], "all": True}
    import proyeccion
    caps = await proyeccion.mis_capacidades(
        "" if is_admin else (user.get("username") or ""), int(project), is_admin)
    return {"capabilities": [{"id": c["id"], "name": c["nombre"]} for c in caps],
            "all": is_admin}


# Roles que pueden asignar revisores del lado cliente a un requisito.
_ASSIGN_ROLES = {"swf-funcional", "admin", "project-manager", "swf-project-manager"}


@router.get("/api/wp/{wp_id}/asignables")
async def api_wp_asignables(wp_id: int, request: Request, role: str) -> dict:
    """Usuarios elegibles para asignar como funcional / usuario-clave de un
    requisito: tienen el ROL y (proyecto+capacidad del WP) en su segmentación
    (vacío = sin restricción)."""
    auth.require_user(request)
    if role not in ("funcional", "usuario-clave"):
        raise HTTPException(status_code=400, detail="role inválido")
    import proyeccion
    det = await proyeccion.get_requisito_detalle(wp_id)
    if not det:
        raise HTTPException(status_code=404, detail="requisito no existe")
    proj = str(det.get("proyecto_id") or "")
    cap = str(det.get("capacidad_id") or "")
    users = await kc_admin.list_users_with_role(role)

    def _ok(u: dict) -> bool:
        p_ok = (not u["proyectos"]) or (proj in u["proyectos"])
        c_ok = (not u["capacidades"]) or (cap in u["capacidades"])
        return p_ok and c_ok

    return {"users": [{"username": u["username"], "name": u["name"]}
                      for u in users if u.get("username") and _ok(u)]}


@router.post("/api/wp/{wp_id}/asignacion")
async def api_wp_asignacion(wp_id: int, request: Request, body: dict) -> dict:
    """Guarda (en Gitea) el funcional + usuario-clave asignados al requisito.
    Informativo: se muestra en el detalle y dashboards (no cambia el turno)."""
    user = auth.require_user(request)
    if not (set(user.get("roles") or []) & _ASSIGN_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo SwF Funcional / admin / PM pueden asignar")
    func = (body.get("funcional") or "").strip()
    uc = (body.get("usuario_clave") or "").strip()
    payload = {
        "funcional": func, "usuario_clave": uc,
        "set_by": user.get("username") or user.get("name"),
        "set_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    import proyeccion
    # Fuente de verdad ÚNICA = columnas de proyecto-db (funcional/usuario_clave,
    # migración 008). Ya NO se escribe asignaciones.json en el repo (migración
    # 010): el repo solo guarda artefactos reales, no objetos derivados.
    await proyeccion.editar_campo(wp_id, "funcional", func)
    await proyeccion.editar_campo(wp_id, "usuario_clave", uc)
    try:
        await proyeccion.agregar_comentario(
            wp_id, user.get("username") or "?", payload["set_by"] or "?",
            f"👥 Asignación cliente — funcional: **{func or '—'}**, "
            f"usuario clave: **{uc or '—'}**.", accion="responder")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, **payload}


async def _read_asignacion(gt: "Gitea", branch: str) -> dict:
    try:
        f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                              "asignaciones.json", ref=branch)
        return json.loads(f["text"]) or {}
    except Exception:  # noqa: BLE001
        return {}


async def _read_clarificacion(gt: "Gitea", branch: str) -> dict:
    """Preguntas de aclaración del agente (modo blocked). {} si no hay.
    Devuelve {questions: [...], model: str, wp_id}. Fuente de verdad = board
    (migración 010); cae a clarificacion/preguntas.json en Gitea solo para
    requerimientos viejos."""
    import proyeccion as _proy
    try:
        wp_id = int(branch.rsplit("-", 1)[-1])
        data = await _proy.get_clarificacion(wp_id)
        if data:
            return data
    except Exception:  # noqa: BLE001
        pass
    try:
        f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                              "clarificacion/preguntas.json", ref=branch)
        return json.loads(f["text"]) or {}
    except Exception:  # noqa: BLE001
        return {}


# ----------------------------------------------------------------------
# Vista "Estado del proyecto": resumen de TODOS los desarrollos de un
# proyecto (como el reporte del Panel) + grandes números (KPIs). Es una
# vista NUEVA del Inbox; la página de bandeja no se toca.
# ----------------------------------------------------------------------
_RESUMEN_ETAPAS_ORDER = [
    ("recibido", "📥 Recibido"), ("generando", "🤖 Generando"),
    ("revision_fabrica", "🔍 Revisión Fábrica"),
    ("validacion_cliente", "👥 Validación Cliente"),
    ("desarrollo", "🛠 Desarrollo"), ("pruebas", "🧪 Pruebas"),
    ("cerrado", "✅ Cerrado"),
]
# Roles que ven TODOS los requisitos del proyecto (líder de proyecto).
_LEADER_ROLES = {"admin", "project-manager", "swf-project-manager"}

# Cache de lo CARO por requerimiento (Temporal + artefactos Gitea: horas
# invertidas, spec/transcripción, observaciones). Cambia poco → TTL corto para
# que la vista "Estado del proyecto" repetida (refrescos, varios usuarios) no
# vuelva a golpear Temporal/Gitea. El avance/fechas se leen SIEMPRE de la BD
# (frescos), así que un cambio de avance se refleja al instante.
_ARTIF_CACHE: dict = {}
_ARTIF_TTL = 90.0
# Roles internos de FÁBRICA. El líder de CLIENTE (project-manager) es el único
# rol de líder que NO es de fábrica → acceso restringido a solo el reporte.
_FABRICA_ROLES = {"admin", "swf-project-manager", "swf-funcional",
                  "swf-desarrollador", "swf-tester"}


def es_lider_cliente_solo(roles) -> bool:
    """True si es líder de proyecto de CLIENTE (`project-manager`) SIN ningún rol
    de fábrica. Ese usuario queda restringido a SOLO el reporte de avance
    (lo aplica el guard del inbox y el shell del panel)."""
    roles = set(roles or [])
    return "project-manager" in roles and not (roles & _FABRICA_ROLES)


def _hours_between(a: str, b: str) -> float:
    """Horas invertidas 'limpias': máximo 8 por día calendario (lunes a domingo).
    Una etapa que quedó abierta varios días NO acumula 24h/día sino 8h/día, así el
    tiempo invertido refleja jornada laboral y no reloj de pared."""
    from datetime import datetime, timedelta
    try:
        sa = datetime.fromisoformat(a)
        sb = datetime.fromisoformat(b)
    except Exception:  # noqa: BLE001
        return 0.0
    if sb <= sa:
        return 0.0
    total = 0.0
    cur = sa
    while cur < sb:
        day_end = cur.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        seg_end = sb if sb < day_end else day_end
        hrs = (seg_end - cur).total_seconds() / 3600.0
        total += hrs if hrs < 8.0 else 8.0
        cur = seg_end
    return round(total, 1)


def _analyze_timeline(tl: list[dict]) -> dict:
    """De timeline.json [{etapa,etapa_key,ts}] saca duración por etapa, etapa
    actual y horas totales (mismo análisis que el Panel)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    etapas = []
    for i, e in enumerate(tl):
        nxt = tl[i + 1]["ts"] if i + 1 < len(tl) else now
        etapas.append({"etapa_key": e.get("etapa_key"),
                       "etapa": e.get("etapa") or e.get("etapa_key"),
                       "desde": e.get("ts"),
                       "horas": _hours_between(e.get("ts"), nxt)})
    cur = tl[-1] if tl else None
    return {"etapas": etapas,
            "current_key": cur.get("etapa_key") if cur else None,
            "horas_total": round(sum(x["horas"] for x in etapas), 1)}


@router.post("/api/export/xlsx")
async def api_export_xlsx(request: Request, body: dict) -> Response:
    """Genera un .xlsx a partir de {filename, sheet, headers, rows}. Genérico:
    el frontend arma las filas (mismo dato que ve en pantalla) y aquí lo
    serializamos a Excel real con openpyxl."""
    auth.require_user(request)
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    headers = [str(h) for h in (body.get("headers") or [])]
    rows = body.get("rows") or []
    wb = Workbook()
    ws = wb.active
    ws.title = (str(body.get("sheet") or "Datos"))[:31]
    if headers:
        ws.append(headers)
        fill = PatternFill("solid", fgColor="0A6ED1")
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = fill
        ws.freeze_panes = "A2"
    for row in rows:
        ws.append(["" if v is None else v for v in row])
    # Celdas de datos: wrap (para textos multilínea como los comentarios).
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2 if headers else 1):
        for cell in row:
            cell.alignment = wrap
    # Ancho ≈ línea más larga de la celda (no el largo total con saltos).
    def _cellw(v):
        return max((len(ln) for ln in str(v).split("\n")), default=0)
    for col in ws.columns:
        width = max((_cellw(c.value) for c in col if c.value is not None),
                    default=8)
        ws.column_dimensions[col[0].column_letter].width = min(60, width + 2)
    buf = io.BytesIO()
    wb.save(buf)
    fname = (str(body.get("filename") or "export"))[:80] + ".xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument."
                   "spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/api/proyecto/{project_id}/resumen")
async def api_proyecto_resumen(project_id: int, request: Request,
                               capability: str | None = None,
                               light: int = 0) -> dict:
    """Estado de todos los desarrollos de un proyecto + KPIs. Visibilidad:
    líder de proyecto ve todos; el resto solo los requisitos en los que
    participó (aunque ya no esté asignado). Respeta la misma segmentación del
    Inbox: por proyecto, por capacidad (atributo KC) y el filtro `capability`
    elegido en la barra de herramientas."""
    import asyncio
    import json as _json
    import time as _time
    user = auth.get_current_user(request) or {}
    roles = set(user.get("roles") or [])
    is_admin = "admin" in roles
    is_leader = bool(roles & _LEADER_ROLES) or is_admin
    username = (user.get("username") or "").strip()
    # Modo LIGERO (reporte / líder cliente): salta el enriquecimiento caro por
    # requerimiento (Temporal + varias lecturas a Gitea). Deja solo lo que el
    # reporte usa: etapa, avance, fechas, horas del plan y nombres. El inbox
    # principal (sin ?light) sigue con el enriquecimiento completo.
    _light = bool(light) or es_lider_cliente_solo(roles)

    from workflow import FactoryWorkflow
    import proyeccion

    # Mapa username→nombre para mostrar el NOMBRE del consultor (no el username/
    # correo). Se arma una vez con los usuarios de los roles cliente.
    name_map: dict[str, str] = {}
    try:
        # Cubre consultor (funcional), usuario clave y asignado (desarrollador/
        # tester/PM) para mostrar el NOMBRE, no el correo/username, en todos.
        for _role in ("funcional", "usuario-clave", "swf-funcional",
                      "swf-desarrollador", "swf-tester",
                      "project-manager", "swf-project-manager"):
            for u in await kc_admin.list_users_with_role(_role):
                un = (u.get("username") or "").strip()
                if un and un.lower() not in name_map:
                    name_map[un.lower()] = u.get("name") or un
    except Exception:  # noqa: BLE001
        name_map = {}

    # Lista desde proyecto-db (read-model). listar_inbox ya aplica la cota de
    # seguridad roles×proyectos×capacidades: si el usuario no tiene acceso al
    # proyecto, viene vacío. El filtro de la barra (capability) se pasa abajo.
    # Líder (fábrica o cliente) ve TODAS las capacidades de su proyecto.
    _cap = int(capability) if (capability and str(capability).isdigit()) else None
    rows = await proyeccion.listar_inbox(
        username, is_admin, incluir_cerrados=True,
        proyecto_id=project_id, capacidad_id=_cap,
        ver_todas_capacidades=is_leader)

    async with Gitea() as gt:
        async def enrich(r: dict) -> dict | None:
            wp_id = r.get("wp_id")
            etapa = etapa_from_stage(r.get("etapa_actual"))
            running = bool(r.get("workflow_iniciado"))
            pel_user = (r.get("pelota_usuario") or "").strip()
            # --- Lo CARO (Temporal + artefactos Gitea) con cache por wp_id.
            # Cambia poco; el avance/fechas van FRESCOS más abajo. En modo light
            # no se calcula ni se cachea.
            _ck = _ARTIF_CACHE.get(wp_id)
            if not _light and _ck and (_time.time() - _ck[0] < _ARTIF_TTL):
                _a = _ck[1]
                stage = _a["stage"]; analysis = _a["analysis"]
                has_spec = _a["has_spec"]; has_transcript = _a["has_transcript"]
                observaciones = _a["observaciones"]
            else:
                # Stage técnico (Temporal) → sub-estado. Best-effort.
                stage = None
                if not _light and _temporal is not None and running:
                    try:
                        handle = _temporal.get_workflow_handle(f"requisito-{wp_id}")
                        desc = await handle.describe()
                        if (str(desc.status).endswith("RUNNING") or
                                getattr(desc.status, "value", 0) == 1):
                            stage = await handle.query(FactoryWorkflow.current_stage)
                    except RPCError:
                        pass
                    except Exception:  # noqa: BLE001
                        pass
                # Timeline (horas invertidas) desde Gitea.
                tl = []
                if not _light:
                    try:
                        f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                              "timeline.json", ref=f"req-{wp_id}")
                        tl = (_json.loads(f["text"]) or {}).get("timeline") or []
                    except Exception:  # noqa: BLE001
                        tl = []
                analysis = _analyze_timeline(tl)
                # ¿Qué le falta? spec, transcripción + observaciones.
                has_spec = has_transcript = True
                observaciones = 0
                if not _light:
                    has_spec = False
                    try:
                        await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                          "especificacion/meta.json", ref=f"req-{wp_id}")
                        has_spec = True
                    except Exception:  # noqa: BLE001
                        has_spec = False
                    has_transcript = False
                    try:
                        mf = _json.loads((await gt.get_file(
                            _ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                            "inputs/manifest.json", ref=f"req-{wp_id}"))["text"])
                        has_transcript = bool(mf.get("files") or mf.get("pdfs")
                                              or mf.get("images") or mf.get("documents"))
                    except Exception:  # noqa: BLE001
                        has_transcript = False
                    try:
                        af = await gt.get_file(
                            _ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                            "especificacion/anotaciones-pendientes.json",
                            ref=f"req-{wp_id}")
                        al = _json.loads(af["text"])
                        observaciones = len(al) if isinstance(al, list) else 0
                    except Exception:  # noqa: BLE001
                        observaciones = 0
                    _ARTIF_CACHE[wp_id] = (_time.time(), {
                        "stage": stage, "analysis": analysis, "has_spec": has_spec,
                        "has_transcript": has_transcript,
                        "observaciones": observaciones})
            # Sub-estado (pendiente humano) = stage + estado FRESCO (pelota).
            pending = None
            if stage:
                if "esperando_revision" in stage:
                    pending = "review"
                elif stage == "esperando_clarificacion":
                    pending = "clarification"
                elif stage.startswith("esperando_"):
                    pending = "gate"
            if pending is None and running and pel_user:
                pending = "gate"
            pct = r.get("avance_pct")
            if pct is None:
                pct = etapa.get("progress", 0)
            # Fechas planeadas desde el board (SIEMPRE frescas).
            plan = await proyeccion.get_plan(wp_id) or {}
            ini = plan.get("inicio_plan")
            fin = plan.get("fin_plan")
            # Asignación: columnas (mig-008); sidecar solo fallback (mig-010).
            func_user = (r.get("funcional") or "").strip()
            uc_user = (r.get("usuario_clave") or "").strip()
            if not _light and not func_user and not uc_user:
                _asig = await _read_asignacion(gt, f"req-{wp_id}")
                func_user = (_asig.get("funcional") or "").strip()
                uc_user = (_asig.get("usuario_clave") or "").strip()
            # Nombre del consultor (no el username/correo).
            func_name = name_map.get(func_user.lower(), func_user)
            uc_name = name_map.get(uc_user.lower(), uc_user)
            falta = []
            if not has_transcript:
                falta.append("transcript")
            if not has_spec:
                falta.append("spec")
            _dev_phases = {"desarrollo", "pruebas_funcionales", "pruebas_usuario"}
            _hp = plan.get("horas_plan")
            _htec = plan.get("horas_tecnicas")
            _hfun = plan.get("horas_funcionales")
            _dev_user = (r.get("desarrollador")
                         or (pel_user if etapa.get("key") in _dev_phases else ""))
            _dev_nombre = (name_map.get((_dev_user or "").lower(), _dev_user)
                           if _dev_user else None)
            return {
                "id": wp_id,
                "subject": r.get("titulo"),
                # username (no display name) para que el picklist del Gantt
                # haga match con las opciones (que son usernames).
                "funcional": r.get("funcional") or func_user or "",
                "usuario_clave": r.get("usuario_clave") or uc_user or "",
                # Nombres resueltos (no correo) para mostrar/exportar.
                "funcional_nombre": func_name,
                "usuario_clave_nombre": uc_name,
                "assignee_nombre": (name_map.get(pel_user.lower(), pel_user)
                                    if pel_user else None),
                "desarrollador": (r.get("desarrollador")
                                  or (pel_user if etapa.get("key") in _dev_phases
                                      else None)),
                "desarrollador_nombre": _dev_nombre,
                "falta": falta,
                "observaciones": observaciones,
                "status": etapa.get("label"),
                "capability": r.get("capacidad") or "(sin capacidad)",
                "etapa": etapa.get("label"),
                "etapa_key": etapa.get("key"),
                "etapa_icon": etapa.get("icon"),
                "is_blocked": etapa.get("is_blocked", False),
                "pending": pending,
                "assignee": pel_user or None,
                "pelota_rol": r.get("pelota_rol"),
                "prioridad": r.get("prioridad") or "media",
                # Avance del WORKFLOW (por etapa, NO editable) vs el del LÍDER
                # (avance_manual, editable). `percentage` = efectivo (líder si lo
                # capturó, si no el del workflow) → es la base de los acumulados.
                "avance_workflow": int(etapa.get("progress", 0) or 0),
                "avance_lider": (int(r.get("avance_manual"))
                                 if r.get("avance_manual") is not None else None),
                "percentage": int(pct or 0),
                "start_date": ini.isoformat() if ini else None,
                "due_date": fin.isoformat() if fin else None,
                "horas": (float(_hp) if _hp is not None else None),
                # Horas partidas capturables (mig-014). horas = suma (mueve la barra).
                "horas_tecnicas": (float(_htec) if _htec is not None else None),
                "horas_funcionales": (float(_hfun) if _hfun is not None else None),
                "etapas": analysis["etapas"],
                "current_key": analysis["current_key"] or etapa.get("key"),
                "horas_total": analysis["horas_total"],
            }

        enriched = [r for r in await asyncio.gather(*[enrich(r) for r in rows])
                    if r is not None]

    # Etiqueta de prioridad + flag de desestimado por fila (para el Excel del
    # Resumen): la columna Prioridad muestra "Desestimado" en los descartados.
    _prios_cat = await proyeccion.listar_prioridades()
    _prio_lbl = {p["codigo"]: p["label"] for p in _prios_cat}
    for r in enriched:
        r["es_desestimado"] = (r.get("etapa_key") == "desestimado")
        r["prioridad_label"] = _prio_lbl.get(r.get("prioridad"), r.get("prioridad") or "")

    # Agrupar por capacidad + % de avance.
    caps: dict[str, list] = {}
    for r in enriched:
        caps.setdefault(r["capability"], []).append(r)

    def _avg(rows: list) -> int:
        return round(sum(x["percentage"] for x in rows) / len(rows)) if rows else 0

    # Los DESESTIMADOS no cuentan en los KPIs (avance/total/estado): un GAP
    # descartado no es progreso del proyecto. Siguen en `capabilities` para que
    # el reporte pueda mostrarlos bajo demanda (con el filtro).
    _activos = [r for r in enriched if r.get("etapa_key") != "desestimado"]
    terminados = sum(1 for r in _activos if r["etapa_key"] in ("pruebas", "cerrado"))
    esperando = sum(1 for r in _activos if r["pending"] in ("gate", "review", "clarification"))
    en_curso = sum(1 for r in _activos
                   if r["etapa_key"] not in ("pruebas", "cerrado"))
    capabilities = [{"capability": k, "avance": _avg(v),
                     "requisitos": sorted(v, key=lambda x: x["id"])}
                    for k, v in sorted(caps.items())]
    # Picklists por rol (∩ proyecto): funcional, desarrollador, usuario clave.
    _proj = set(await proyeccion.usuarios_de_proyecto(project_id))

    async def _rl(*rls):
        seen, out = set(), []
        for rol in rls:
            for u in await kc_admin.list_users_with_role(rol):
                un = u.get("username")
                if un and un in _proj and un not in seen:
                    seen.add(un)
                    out.append({"u": un, "n": u.get("name") or un})
        return sorted(out, key=lambda x: (x["n"] or "").lower())

    _u_func = await _rl("funcional", "swf-funcional")
    _u_dev = await _rl("swf-desarrollador")
    _u_uc = await _rl("usuario-clave")
    # Líder de CLIENTE: recorta la respuesta a lo estrictamente presentable
    # (sin horas de Temporal, responsables internos, pelota ni picklists). El
    # reporte solo necesita: capacidad, GAP, fase, avance efectivo, fechas.
    if es_lider_cliente_solo(roles):
        _ck = {"id", "subject", "capability", "etapa", "etapa_key",
               "percentage", "avance_lider", "prioridad_label",
               "start_date", "due_date", "horas_tecnicas", "horas_funcionales",
               "funcional_nombre", "desarrollador_nombre"}
        for _c in capabilities:
            _c["requisitos"] = [{k: r.get(k) for k in _ck}
                                for r in _c["requisitos"]]
        _u_func = _u_dev = _u_uc = []
    return {
        "project_id": project_id,
        "is_leader": is_leader,
        "prioridades": _prios_cat,
        "can_set_prioridad": bool(roles & {"admin", "project-manager",
                                           "swf-project-manager"}),
        "usuarios_funcional": _u_func, "usuarios_desarrollador": _u_dev,
        "usuarios_clave": _u_uc,
        "can_edit": bool(roles & _EDITOR_ROLES),
        "etapas_order": [{"key": k, "label": l} for k, l in _RESUMEN_ETAPAS_ORDER],
        "kpis": {
            "total": len(_activos),
            "en_curso": en_curso,
            "esperando_humano": esperando,
            "terminados": terminados,
            "avance_proyecto": _avg(_activos),
        },
        "capabilities": capabilities,
    }


# --- Fotos de avance para facturación por delta (solo líder de FÁBRICA) -----
_FAB_LEADER_ROLES = {"admin", "swf-project-manager"}


@router.post("/api/proyecto/{project_id}/snapshot")
async def api_snapshot_crear(project_id: int, request: Request,
                             body: dict | None = None) -> dict:
    """Congela el avance actual del proyecto en una foto (historial). Solo
    líder de FÁBRICA (facturación); el líder de cliente no debe congelar."""
    import proyeccion
    user = auth.get_current_user(request) or {}
    if not (set(user.get("roles") or []) & _FAB_LEADER_ROLES):
        raise HTTPException(status_code=403, detail="solo líder de fábrica")
    etiqueta = ((body or {}).get("etiqueta") or "").strip() or None
    sid = await proyeccion.guardar_snapshot_avance(
        project_id, user.get("username") or user.get("name") or "?", etiqueta)
    if sid is None:
        raise HTTPException(status_code=500, detail="no se pudo guardar la foto")
    return {"ok": True, "id": sid}


@router.get("/api/proyecto/{project_id}/snapshots")
async def api_snapshots_listar(project_id: int, request: Request) -> dict:
    import proyeccion
    user = auth.get_current_user(request) or {}
    if not (set(user.get("roles") or []) & _FAB_LEADER_ROLES):
        raise HTTPException(status_code=403, detail="solo líder de fábrica")
    return {"snapshots": await proyeccion.listar_snapshots(project_id)}


@router.get("/api/proyecto/{project_id}/snapshot/ultima")
async def api_snapshot_ultima(project_id: int, request: Request) -> dict:
    import proyeccion
    user = auth.get_current_user(request) or {}
    if not (set(user.get("roles") or []) & _FAB_LEADER_ROLES):
        raise HTTPException(status_code=403, detail="solo líder de fábrica")
    return (await proyeccion.ultima_snapshot(project_id)) or {}


# --- Reporte de avance (presentación cara al líder de cliente) --------------
# Página autocontenida REUTILIZABLE: la abre el líder de fábrica y la embebe el
# panel para el líder de cliente (project-manager). Consume los MISMOS endpoints
# (/resumen + /comentarios-export) → sin duplicar la capa de datos.
_REPORTE_AVANCE_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Avance del proyecto</title>
<style>
 * { box-sizing: border-box; }
 body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
        color:#1a2733; background:#f0f3f7; }
 .wrap { max-width:none; margin:0; padding:8px 12px 22px; }
 .top { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:4px; }
 h1 { font-size:15px; margin:0; flex:1; min-width:180px; }
 select, input { font:inherit; font-size:12px; padding:4px 7px; border:1px solid #cdd6e0; border-radius:5px; background:#fff; }
 .muted { color:#6b7a89; font-size:11px; }
 .btn { border:none; border-radius:5px; padding:5px 11px; font-weight:600; cursor:pointer; font-size:12px; color:#fff; }
 .btn.green { background:#107e3e; } .btn.blue { background:#0a6ed1; }
 .kpis { display:grid; grid-template-columns:2fr 1fr 1fr 1fr 1fr; gap:8px; margin:8px 0 6px; }
 @media (max-width:720px){ .kpis{ grid-template-columns:1fr 1fr; } }
 .kpi { background:#fff; border:1px solid #e3e9f0; border-radius:8px; padding:7px 11px; }
 .kpi .n { font-size:19px; font-weight:800; line-height:1; }
 .kpi .l { font-size:9.5px; color:#6b7a89; margin-top:2px; text-transform:uppercase; letter-spacing:.02em; }
 .kpi.big .n { font-size:26px; color:#107e3e; }
 .bar { background:#e3e9f2; border-radius:5px; height:8px; overflow:hidden; }
 .bar > i { display:block; height:100%; background:#107e3e; border-radius:5px; }
 .bar.big { height:10px; margin-top:6px; }
 .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:8px 0; }
 .toolbar .grow { flex:1; min-width:160px; }
 .vtog { display:inline-flex; border:1px solid #cdd6e0; border-radius:6px; overflow:hidden; }
 .vbtn { border:none; background:#fff; color:#556; font:inherit; font-size:12px; padding:4px 11px; cursor:pointer; }
 .vbtn.active { background:#0a6ed1; color:#fff; font-weight:600; }
 .gbar { position:absolute; top:4px; height:15px; border-radius:3px; overflow:hidden; }
 .badge { display:inline-block; font-size:10px; font-weight:700; color:#fff; border-radius:20px; padding:1px 8px; white-space:nowrap; }
 .avwrap { display:flex; align-items:center; gap:6px; }
 .avwrap .p { font-size:11px; font-weight:700; width:28px; text-align:right; }
 .empty { text-align:center; color:#8a97a4; padding:30px; }
 .tblwrap { overflow-x:auto; background:#fff; border:1px solid #cdd6e0; border-radius:8px; }
 table.gt { width:100%; border-collapse:collapse; font-size:11px; table-layout:fixed; }
 /* Líneas de guía: cada celda con borde (verticales + horizontales). Layout fijo
    + anchos por columna (colgroup) → entra todo sin scroll horizontal. */
 table.gt th, table.gt td { padding:3px 6px; text-align:left; vertical-align:top;
   border:1px solid #e4eaf0; overflow:hidden; }
 table.gt td { white-space:nowrap; text-overflow:ellipsis; }
 table.gt .gt-t { white-space:normal; word-break:break-word; font-weight:600; }
 table.gt thead th { position:sticky; top:0; z-index:2; font-size:9.5px; text-transform:uppercase;
   letter-spacing:.02em; color:#5f6f7d; background:#eef2f7; font-weight:700; white-space:nowrap;
   border-color:#cdd6e0; }
 table.gt td.num { text-align:right; white-space:nowrap; }
 table.gt .cmt { color:#5a6b7b; text-overflow:ellipsis; }
 table.gt tr.grp td { background:#e8eef5; border-top:2px solid #b9c4d1; border-bottom:1px solid #cdd6e0; }
 table.gt tr:hover td { background:#f6f9fc; }
 table.gt tr.grp:hover td { background:#e8eef5; }
 .grpwrap { display:flex; align-items:center; gap:12px; }
 .grpwrap .cn { font-weight:700; font-size:12.5px; color:#26303a; }
 .grpwrap .grpbar { width:150px; } .grpwrap .pct { font-weight:700; font-size:12px; }
</style></head>
<body><div class="wrap">
  <div class="top">
    <h1 id="ptitle">Avance del proyecto</h1>
    <select id="proj" style="display:none"></select>
    <button id="xls" class="btn green">⬇ Excel</button>
  </div>
  <div class="muted" id="sub">Cargando…</div>
  <div id="kpis"></div>
  <div class="toolbar" id="tb" style="display:none">
    <span class="vtog"><button id="v-tabla" class="vbtn active">📋 Tabla</button><button id="v-gantt" class="vbtn">📅 Gantt</button></span>
    <input id="q" class="grow" placeholder="Buscar GAP o capacidad…">
    <select id="fase"><option value="">Todas las fases</option></select>
    <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#5a6b7b;white-space:nowrap;cursor:pointer">
      <input type="checkbox" id="desest"> Incluir desestimados</label>
  </div>
  <div id="body"></div>
</div>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const FASE_COLOR = { recibido:"#6a6d70", recepcion:"#6a6d70", generando:"#8c4fff", generando_paquete:"#8c4fff",
  diseno:"#0a6ed1", revision_fabrica:"#0a6ed1", validacion_cliente:"#0a6ed1", desarrollo:"#d97400",
  pruebas_funcionales:"#c77700", pruebas_usuario:"#c77700", pruebas:"#c77700",
  cerrado:"#107e3e", desestimado:"#b85450" };
const avColor = p => p>=100?"#107e3e":(p>=60?"#4aa02c":(p>=30?"#d97400":"#c77700"));
let PROJECTS=[], DATA=null, COM={}, FILT={q:"",fase:"",desest:false}, VIEW="tabla";
async function api(u){ const r=await fetch(u,{credentials:"same-origin"}); if(!r.ok) throw new Error(r.status); return r.json(); }
function bar(p, big){ p=Math.max(0,Math.min(100,+p||0));
  return `<div class="bar${big?' big':''}"><i style="width:${p}%;background:${avColor(p)}"></i></div>`; }

async function boot(){
  try { const d=await api("/api/mis-proyectos"); PROJECTS=d.projects||[]; }
  catch(e){ $("#sub").textContent="No se pudieron cargar tus proyectos."; return; }
  if(!PROJECTS.length){ $("#sub").textContent="No tienes proyectos asignados."; return; }
  const sel=$("#proj");
  if(PROJECTS.length>1){ sel.style.display="";
    sel.innerHTML=PROJECTS.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join("");
    sel.onchange=()=>load(+sel.value);
  }
  load(PROJECTS[0].id);
}
async function load(pid){
  $("#body").innerHTML=`<div class="empty">Cargando avance…</div>`;
  const proj = PROJECTS.find(p=>p.id===pid) || {};
  $("#ptitle").textContent = proj.name || "Avance del proyecto";
  try {
    DATA = await api(`/api/proyecto/${pid}/resumen?light=1`);
    try { COM = (await api(`/api/proyecto/${pid}/comentarios-export`)).comentarios || {}; }
    catch(_){ COM={}; }
  } catch(e){ $("#body").innerHTML=`<div class="empty">No se pudo cargar el avance (${e.message}).</div>`; return; }
  const fs=$("#fase"); fs.innerHTML=`<option value="">Todas las fases</option>`+
    (DATA.etapas_order||[]).map(e=>`<option value="${e.key}">${esc(e.label)}</option>`).join("");
  $("#q").oninput=e=>{FILT.q=e.target.value.toLowerCase();render();};
  fs.onchange=e=>{FILT.fase=e.target.value;render();};
  $("#desest").onchange=e=>{FILT.desest=e.target.checked;render();};
  $("#v-tabla").onclick=()=>{VIEW="tabla";$("#v-tabla").classList.add("active");$("#v-gantt").classList.remove("active");render();};
  $("#v-gantt").onclick=()=>{VIEW="gantt";$("#v-gantt").classList.add("active");$("#v-tabla").classList.remove("active");render();};
  $("#tb").style.display="";
  render();
}
function matches(r){
  // Desestimados: ocultos por default; se ven con el check o filtrando esa fase.
  if(r.etapa_key==="desestimado" && !FILT.desest && FILT.fase!=="desestimado") return false;
  if(FILT.fase && r.etapa_key!==FILT.fase) return false;
  if(FILT.q){ const h=((r.subject||"")+" "+(r.capability||"")).toLowerCase(); if(!h.includes(FILT.q)) return false; }
  return true;
}
function fdate(s){ if(!s) return "—"; const d=new Date(s); return isNaN(d)?"—":d.toLocaleDateString("es"); }
// Avance PLANEADO = % del cronograma transcurrido (inicio→fin vs hoy).
function planned(ini, fin){
  if(!ini||!fin) return null;
  const a=new Date(ini), b=new Date(fin), now=new Date();
  if(isNaN(a)||isNaN(b)||b<=a) return null;
  return Math.max(0, Math.min(100, Math.round((now-a)/(b-a)*100)));
}
// Avance del LÍDER (avance_lider); si no lo capturó, cae al efectivo.
function avL(r){ return (r.avance_lider!=null) ? +r.avance_lider : (+r.percentage||0); }
function devHtml(av, pl){
  if(pl==null) return '<span style="color:#aab3bd">—</span>';
  const d=av-pl, c=d>=0?"#107e3e":"#b8121b", s=d>0?"+":"";
  return `<b style="color:${c}">${s}${d}%</b>`;
}
function render(){
  const k=DATA.kpis||{};
  $("#sub").textContent=`Actualizado ${new Date().toLocaleString("es")}`;
  $("#kpis").className="kpis";
  $("#kpis").innerHTML=`
    <div class="kpi big"><div class="n">${k.avance_proyecto||0}%</div><div class="l">Avance del proyecto</div>${bar(k.avance_proyecto,true)}</div>
    <div class="kpi"><div class="n">${k.total||0}</div><div class="l">GAPs</div></div>
    <div class="kpi"><div class="n">${k.en_curso||0}</div><div class="l">En curso</div></div>
    <div class="kpi"><div class="n">${k.esperando_humano||0}</div><div class="l">Esperando</div></div>
    <div class="kpi"><div class="n">${k.terminados||0}</div><div class="l">Terminados</div></div>`;
  const caps=(DATA.capabilities||[]).map(c=>({...c, reqs:(c.requisitos||[]).filter(matches)}))
                                    .filter(c=>c.reqs.length);
  if(!caps.length){ $("#body").innerHTML=`<div class="empty">Ningún GAP coincide con el filtro.</div>`; return; }
  if(VIEW==="gantt"){ $("#body").innerHTML=ganttHtml(caps); return; }
  const hh=v=>v!=null?v:"—";
  // Una SOLA tabla continua: la capacidad es una fila de grupo (colspan) con su
  // subtotal; las columnas quedan alineadas entre capacidades.
  const bodyRows=caps.map(c=>{
    const sub=Math.round(c.reqs.reduce((a,r)=>a+avL(r),0)/c.reqs.length);
    const grp=`<tr class="grp"><td colspan="12"><div class="grpwrap">
        <span class="cn">${esc(c.capability)}</span>
        <span class="muted">${c.reqs.length} GAP${c.reqs.length!==1?'s':''}</span>
        <span class="grpbar">${bar(sub)}</span><span class="pct">${sub}%</span></div></td></tr>`;
    const rows=c.reqs.map(r=>{
      const col=FASE_COLOR[r.etapa_key]||"#6a6d70";
      const av=avL(r), pl=planned(r.start_date, r.due_date), cm=COM[r.id]||"";
      return `<tr>
        <td class="gt-t" title="${esc(r.subject||'')}">${esc(r.subject||"(sin título)")}</td>
        <td title="${esc(r.funcional_nombre||'')}">${esc(r.funcional_nombre||"—")}</td>
        <td title="${esc(r.desarrollador_nombre||'')}">${esc(r.desarrollador_nombre||"—")}</td>
        <td class="num">${fdate(r.start_date)}</td>
        <td class="num">${fdate(r.due_date)}</td>
        <td class="num">${hh(r.horas_tecnicas)}</td>
        <td class="num">${hh(r.horas_funcionales)}</td>
        <td class="avc"><div class="avwrap"><div style="flex:1">${bar(av)}</div><span class="p">${av}%</span></div></td>
        <td class="num">${pl==null?'—':pl+'%'}</td>
        <td class="num">${devHtml(av,pl)}</td>
        <td><span class="badge" style="background:${col}">${esc(r.etapa||"—")}</span></td>
        <td class="cmt" title="${esc(cm)}">${cm?esc(cm):'<span style="color:#b3bcc6">—</span>'}</td>
      </tr>`;
    }).join("");
    return grp+rows;
  }).join("");
  $("#body").innerHTML=`<div class="tblwrap"><table class="gt"><colgroup>
      <col style="width:17%"><col style="width:8%"><col style="width:8%"><col style="width:6.5%"><col style="width:6.5%">
      <col style="width:5%"><col style="width:5%"><col style="width:11%"><col style="width:6%"><col style="width:6%"><col style="width:8%"><col style="width:13%">
    </colgroup><thead><tr>
      <th>GAP</th><th>Funcional</th><th>Desarrollo</th><th>Inicio</th><th>Fin</th>
      <th title="Horas técnicas (plan)">H.Téc</th><th title="Horas funcionales (plan)">H.Func</th>
      <th>Avance líder</th><th>Planeado</th><th>Desv.</th><th>Fase</th><th>Comentarios</th>
    </tr></thead><tbody>${bodyRows}</tbody></table></div>`;
}

// Gantt de solo lectura para el reporte: mismas fases/colores, barra = avance.
function ganttHtml(caps){
  const DAY=86400000, WK=60, ROWH=23, LW=250;
  const MES=["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
  const pISO=s=>{const [y,m,d]=s.split("-").map(Number);return new Date(y,m-1,d);};
  const monday=iso=>{const d=pISO(iso);const wd=(d.getDay()+6)%7;d.setDate(d.getDate()-wd);d.setHours(0,0,0,0);return d;};
  const vis=caps.flatMap(c=>c.reqs);
  // Ignora fechas basura (typos de año, p.ej. 0026 que JS lee como 1926) para
  // que un dato malo no rompa toda la escala del Gantt.
  const okDate=s=>!!s&&s>="2015-01-01"&&s<="2100-12-31";
  const dated=vis.filter(r=>okDate(r.start_date)&&okDate(r.due_date));
  let weeks=[],gridStart=null,nW=0;
  if(dated.length){
    const minI=dated.reduce((m,r)=>r.start_date<m?r.start_date:m,dated[0].start_date);
    const maxF=dated.reduce((m,r)=>r.due_date>m?r.due_date:m,dated[0].due_date);
    gridStart=monday(minI);const lastMon=monday(maxF);
    nW=Math.round((lastMon-gridStart)/DAY/7)+1;
    weeks=Array.from({length:nW},(_,i)=>{const d=new Date(gridStart);d.setDate(d.getDate()+i*7);return d;});
  }
  const undated=vis.some(r=>!okDate(r.start_date)||!okDate(r.due_date));
  const TRACK=Math.max(nW*WK, undated?220:0), TOT=LW+TRACK;
  const GRID=`repeating-linear-gradient(to right,transparent 0 ${WK-1}px,#e4eaf0 ${WK-1}px ${WK}px)`;
  const barOf=r=>{
    if(!okDate(r.start_date)||!okDate(r.due_date)) return `<div title="Sin fechas de inicio/fin (o fecha inválida)" style="position:absolute;left:6px;top:4px;height:15px;display:flex;align-items:center;font-size:9px;font-weight:600;color:#b06a00;background:#fff3e0;border:1px dashed #e0a021;border-radius:3px;padding:0 6px;box-sizing:border-box">⚠ sin fechas</div>`;
    if(!gridStart) return "";
    const s=pISO(r.start_date),e=pISO(r.due_date);
    const left=(s-gridStart)/DAY/7*WK, w=Math.max(10,((e-s)/DAY+1)/7*WK);
    const col=FASE_COLOR[r.etapa_key]||"#9aa7b8", pct=Math.max(0,Math.min(100,avL(r)));
    return `<div title="${esc(r.subject||'')} · ${r.start_date}→${r.due_date} · ${pct}%" style="position:absolute;left:${left}px;width:${w}px;top:4px;height:15px;background:${col}26;border:1px solid ${col};border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${col}"></div></div>`;
  };
  const header=`<div style="display:flex;position:sticky;top:0;z-index:3;background:#eef2f7;border-bottom:2px solid #cdd6e0;font-size:9.5px;font-weight:700;color:#5f6f7d">
      <div style="flex-shrink:0;position:sticky;left:0;z-index:4;background:#eef2f7;width:${LW}px;padding:5px 8px;box-sizing:border-box">GAP</div>
      <div style="position:relative;width:${TRACK}px;flex-shrink:0">${weeks.map((d,i)=>`<div style="position:absolute;left:${i*WK}px;width:${WK}px;box-sizing:border-box;border-left:1px solid #cdd6e0;padding:2px;text-align:center;line-height:1.2">S${i+1}<div style="color:#8693a8;font-weight:400">${d.getDate()} ${MES[d.getMonth()]}</div></div>`).join("")}</div></div>`;
  const rowOf=r=>`<div style="display:flex;border-bottom:1px solid #eef1f6;height:${ROWH}px;align-items:center;font-size:11px">
      <div style="flex-shrink:0;position:sticky;left:0;z-index:2;background:#fff;width:${LW}px;padding:0 8px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(r.subject||'')}"><b>${esc(r.subject||'')}</b></div>
      <div style="position:relative;width:${TRACK}px;height:100%;flex-shrink:0;background:${GRID}">${barOf(r)}</div></div>`;
  const band=c=>`<div style="width:${TOT}px;background:#e8eef5;border-top:1px solid #cdd6e0;font-weight:700;font-size:11px;color:#26303a;padding:3px 8px;box-sizing:border-box;position:sticky;left:0">${esc(c.capability)} <span style="color:#8693a8;font-weight:400">(${c.reqs.length})</span></div>`;
  const legend=Object.entries({recepcion:"Recepción",diseno:"Diseño",desarrollo:"Desarrollo",pruebas_funcionales:"Pruebas Func.",pruebas_usuario:"Pruebas Usuario",cerrado:"Cerrado"}).map(([k,l])=>`<span style="display:inline-flex;align-items:center;gap:4px;margin-right:8px"><span style="width:10px;height:10px;border-radius:2px;background:${FASE_COLOR[k]||'#9aa7b8'};display:inline-block"></span>${l}</span>`).join("");
  return `<div style="font-size:10px;color:#8693a8;margin:2px 0 6px">${legend}${undated?` · <span style="color:#b06a00;font-weight:600">${vis.filter(r=>!okDate(r.start_date)||!okDate(r.due_date)).length} sin fechas</span>`:''}</div>
    <div class="tblwrap" style="overflow:auto;max-height:70vh"><div style="min-width:${TOT}px">${header}${caps.map(c=>band(c)+c.reqs.map(rowOf).join("")).join("")}</div></div>`;
}
$("#xls").onclick=async()=>{
  if(!DATA) return;
  const headers=["Capacidad","GAP","Funcional","Desarrollo","Inicio","Fin",
    "H. Téc","H. Func","Avance líder %","Planeado %","Desviación %","Fase","Comentarios"];
  const rows=[];
  (DATA.capabilities||[]).forEach(c=>(c.requisitos||[]).filter(matches).forEach(r=>{
    const av=avL(r), pl=planned(r.start_date, r.due_date);
    rows.push([c.capability, r.subject||"", r.funcional_nombre||"", r.desarrollador_nombre||"",
      r.start_date||"", r.due_date||"", r.horas_tecnicas!=null?r.horas_tecnicas:"", r.horas_funcionales!=null?r.horas_funcionales:"",
      av, pl==null?"":pl, pl==null?"":(av-pl), r.etapa||"", COM[r.id]||""]);
  }));
  const fname=`avance-${($("#ptitle").textContent||"proyecto").replace(/[^A-Za-z0-9_-]+/g,"_")}`;
  try {
    const resp=await fetch("/api/export/xlsx",{method:"POST",headers:{"Content-Type":"application/json"},
      credentials:"same-origin", body:JSON.stringify({filename:fname,sheet:"Avance",headers,rows})});
    if(!resp.ok) throw new Error(resp.status);
    const b=await resp.blob(), a=document.createElement("a");
    a.href=URL.createObjectURL(b); a.download=`${fname}.xlsx`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  } catch(e){ alert("No se pudo generar el Excel: "+e.message); }
};
boot();
</script></body></html>"""


@router.get("/reporte-avance", response_class=HTMLResponse)
async def reporte_avance_page(request: Request):
    """Reporte de avance de GAPs (presentación). Reutilizable: lo abre el líder
    de FÁBRICA y lo embebe el panel para el líder de CLIENTE. Solo roles de
    líder de proyecto; el resto 403. Los datos salen de /resumen (segmentado
    por proyecto) + /comentarios-export (solo comentarios de personas)."""
    user = auth.get_current_user(request) or {}
    roles = set(user.get("roles") or [])
    if not (roles & _LEADER_ROLES):
        return HTMLResponse(
            "<h1 style='font-family:sans-serif'>403 — requiere rol de líder de proyecto</h1>",
            status_code=403)
    return HTMLResponse(_REPORTE_AVANCE_HTML, headers={"Cache-Control": "no-store"})


# --- Síntesis de comentarios del blog para el Excel del Resumen -------------
_SYS_AUTHORS = {"", "fabrica", "fabro", "sistema", "system", "mcp-agent"}
_SYS_NAMES = {"fábrica", "fabrica", "sistema", "system"}


def _coment_es_humano(c: dict) -> bool:
    """True si el comentario es de una PERSONA (no del sistema / carga automática)."""
    autor = (c.get("autor") or "").strip().lower()
    nombre = (c.get("autor_nombre") or "").strip().lower()
    if autor in _SYS_AUTHORS or nombre in _SYS_NAMES:
        return False
    return True


def _coment_limpiar(txt: str | None) -> str:
    """Quita markdown/HTML/URLs/ruido y colapsa espacios."""
    import re
    t = txt or ""
    t = re.sub(r"<[^>]+>", " ", t)                       # HTML
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)     # [texto](url)/imágenes
    t = re.sub(r"https?://\S+", "", t)                    # URLs sueltas
    t = re.sub(r"[`*_#>~]+", "", t)                       # énfasis/headers/quote/code
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _sintetizar_blog(coms: list[dict], max_len: int = 240) -> str:
    """Comentarios de personas, limpios, en 'YYYY-MM-DD - texto' (uno por línea),
    en orden cronológico. Descarta sistema, cargas de adjuntos y notas vacías."""
    out = []
    for c in coms or []:
        if not _coment_es_humano(c):
            continue
        cuerpo = _coment_limpiar(c.get("cuerpo"))
        if not cuerpo:
            continue  # adjunto puro / nota sin texto
        ca = c.get("created_at")
        fecha = ""
        if ca is not None:
            s = ca.isoformat() if hasattr(ca, "isoformat") else str(ca)
            fecha = s[:10]
        if len(cuerpo) > max_len:
            cuerpo = cuerpo[:max_len].rstrip() + "…"
        out.append(f"{fecha} - {cuerpo}" if fecha else cuerpo)
    return "\n".join(out)


@router.get("/api/proyecto/{project_id}/comentarios-export")
async def api_proyecto_comentarios_export(project_id: int, request: Request,
                                          capability: str | None = None) -> dict:
    """Para el Excel del Resumen: por cada requerimiento visible, los comentarios
    de PERSONAS del blog (sin sistema/cargas), sintetizados a 'fecha - texto'.
    Misma segmentación de seguridad que el resumen."""
    import asyncio
    import proyeccion
    user = auth.get_current_user(request) or {}
    is_admin = "admin" in set(user.get("roles") or [])
    username = (user.get("username") or "").strip()
    _cap = int(capability) if (capability and str(capability).isdigit()) else None
    rows = await proyeccion.listar_inbox(
        username, is_admin, incluir_cerrados=True,
        proyecto_id=project_id, capacidad_id=_cap)

    async def _one(r: dict):
        wp = r.get("wp_id")
        coms = await proyeccion.listar_comentarios(wp)
        return str(wp), _sintetizar_blog(coms)

    pairs = await asyncio.gather(*[_one(r) for r in rows])
    return {"comentarios": {k: v for k, v in pairs if v}}


import re as _re


async def _read_dev_repo(gt: Gitea, branch: str) -> dict:
    """Coords del repo del requerimiento (las escribe provisionar_repo) + las
    versiones de entregables publicadas allí. {} si aún no se provisionó.
    Fuente de verdad = board (migración 010); cae a desarrollo/repo.json en
    Gitea solo para requerimientos viejos. Ver [[rediseno-fase-desarrollo-repo]]."""
    import proyeccion as _proy
    meta: dict = {}
    try:
        wp_id = int(branch.rsplit("-", 1)[-1])
        meta = await _proy.get_dev_repo(wp_id)
    except Exception:  # noqa: BLE001
        meta = {}
    if not meta:  # fallback: sidecar del repo (requerimientos previos a mig-010)
        try:
            f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                  "desarrollo/repo.json", ref=branch)
            meta = json.loads(f["text"])
        except Exception:  # noqa: BLE001
            return {}
    org, repo = meta.get("org"), meta.get("repo")
    entregables: list[str] = []
    if org and repo:
        try:
            for e in await gt.list_contents(org, repo, "entregables", "main"):
                name = e.get("name", "")
                if e.get("type") == "dir" and name.startswith("v") and name[1:].isdigit():
                    entregables.append(name)
        except Exception:  # noqa: BLE001
            pass
    entregables.sort(key=lambda v: int(v[1:]))
    meta["entregables"] = entregables
    return meta


async def _read_block_state(gt: Gitea, branch: str, root: str) -> dict:
    """Lee el estado de un bloque versionado según el modelo nuevo:
      {root}/meta.json          → status, version_actual, iter_actual, …
      {root}/versiones/v01/…    → lista de versiones aprobadas
      {root}/{vigente}          → existe? (chequea por meta presente)

    Devuelve {meta, versiones, has_vigente, has_historial}.
    Ver [[versionado-artefactos]]."""
    meta: dict = {}
    versiones: list[dict] = []
    has_vigente = False
    has_historial = False

    # 1. Meta vigente
    try:
        r = await gt._c.get(
            f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/"
            f"{root}/meta.json",
            params={"ref": branch})
        if r.status_code == 200:
            import base64 as _b64
            raw = _b64.b64decode(r.json()["content"]).decode("utf-8")
            try:
                meta = json.loads(raw)
                has_vigente = True
            except Exception:  # noqa: BLE001
                meta = {}
    except Exception:  # noqa: BLE001
        pass

    # 2. Versiones aprobadas (snapshots inmutables)
    try:
        r = await gt._c.get(
            f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/"
            f"{root}/versiones",
            params={"ref": branch})
        if r.status_code == 200:
            for item in r.json():
                if item.get("type") == "dir":
                    v_meta = {}
                    try:
                        rm = await gt._c.get(
                            f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/"
                            f"{root}/versiones/{item['name']}/meta.json",
                            params={"ref": branch})
                        if rm.status_code == 200:
                            import base64 as _b64
                            v_meta = json.loads(_b64.b64decode(
                                rm.json()["content"]).decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        pass
                    versiones.append({"version": item["name"], **v_meta})
            versiones.sort(key=lambda v: v.get("version", ""))
    except Exception:  # noqa: BLE001
        pass

    # 3. Historial de aprendizaje (solo flag de existencia)
    try:
        r = await gt._c.get(
            f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/"
            f"{root}/historial-aprendizaje.md",
            params={"ref": branch})
        has_historial = (r.status_code == 200)
    except Exception:  # noqa: BLE001
        pass

    return {"meta": meta, "versiones": versiones,
            "has_vigente": has_vigente, "has_historial": has_historial}


@router.get("/api/wp/{wp_id}")
async def api_wp_detail(wp_id: int, request: Request) -> dict:
    """Detalle de WP + estado del paquete funcional bajo el modelo de
    versionado nuevo. Ver [[versionado-artefactos]]. Incluye datos de
    asignación para que la UI renderice el botón 'Tomar este WP' (C2)."""
    user = auth.get_current_user(request) or {}
    user_roles = set(user.get("roles") or [])
    user_name = (user.get("name") or "").strip()
    user_username = (user.get("username") or "").strip().lower()
    my_primary = _primary_role(list(user_roles)) if user_roles else None
    # Detalle desde proyecto-db (read-model) — ya NO OpenProject.
    import proyeccion as _proy
    det = await _proy.get_requisito_detalle(wp_id) or {}
    relations = await _proy.listar_relaciones(wp_id)
    real_creator = None
    is_creator = False
    capability = {"id": det.get("capacidad_id"), "title": det.get("capacidad")}
    project_id_num = det.get("proyecto_id")

    async with Gitea() as gt:
        branch = f"req-{wp_id}"
        # Paquete funcional: spec + prototipo + test plan (los tres se
        # producen y validan juntos por el bloque 'diseno'). Ver
        # pipeline.yaml v4 y [[versionado-artefactos]].
        spec_state = await _read_block_state(gt, branch, "especificacion")
        proto_state = await _read_block_state(gt, branch, "prototipo")
        test_plan_state = await _read_block_state(gt, branch, "test-plan")
        desarrollo_state = await _read_block_state(gt, branch, "desarrollo")
        dev_repo = await _read_dev_repo(gt, branch)
        # Modelo nuevo (git-native): el paquete de diseño vive en `diseno/` del
        # repo del cliente, publicado por git push. Si está, marca `has_vigente`
        # por presencia de archivo (no hay meta.json/versiones: git = historial).
        _dorg, _drepo = dev_repo.get("org"), dev_repo.get("repo")
        if _dorg and _drepo:
            async def _present(p: str) -> bool:
                try:
                    await gt.get_file_bytes(_dorg, _drepo, p, ref="main")
                    return True
                except Exception:  # noqa: BLE001
                    return False
            if await _present("diseno/spec.md"):
                spec_state["has_vigente"] = True
            if await _present("diseno/prototipo.html"):
                proto_state["has_vigente"] = True
            if await _present("diseno/test-plan.md"):
                test_plan_state["has_vigente"] = True
        # Asignación: fuente de verdad = columnas (mig-008). El sidecar
        # asignaciones.json de Gitea ya no se escribe (mig-010); se lee solo
        # como fallback para requerimientos viejos sin columna.
        asignacion = {
            "funcional": det.get("funcional") or "",
            "usuario_clave": det.get("usuario_clave") or "",
            "desarrollador": det.get("desarrollador") or "",
        }
        if not asignacion["funcional"] and not asignacion["usuario_clave"]:
            _old = await _read_asignacion(gt, branch)
            asignacion["funcional"] = _old.get("funcional") or ""
            asignacion["usuario_clave"] = _old.get("usuario_clave") or ""
        # Preguntas de aclaración del agente (modo blocked). Sidecar escrito por
        # save_open_questions; el panel de clarificación las muestra.
        clarificacion = await _read_clarificacion(gt, branch)
        # Inputs subidos a MinIO (el agente los procesa). Vive en
        # inputs/manifest.json. Solo existe si hay al menos un archivo.
        # Documentos de ENTRADA (para que el validador compare contra la spec).
        # El manifest tiene DOS formas: (A) `files` = subidas a MinIO al crear;
        # (B) `images/pdfs/documents` = snapshot que el agente leyó (sobrescribe
        # A tras cargar_documentos). Unificamos ambas en `input_docs`.
        inputs_files: list[dict] = []
        input_docs: list[dict] = []

        def _safe_in(n: str) -> str:
            return (n or "").replace("/", "_").replace("..", "_")

        try:
            mf = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                    "inputs/manifest.json", ref=branch)
            mf_data = json.loads(mf["text"])
            # (A) Subidas a MinIO → URL presignada (browser pega directo a S3).
            inputs_files = mf_data.get("files") or []
            for f in inputs_files:
                url = None
                if f.get("key"):
                    try:
                        url = S3Storage.presigned_get_url(f["key"])
                        f["url"] = url
                    except Exception:  # noqa: BLE001
                        pass
                input_docs.append({"name": f.get("filename") or f.get("name"),
                                   "content_type": f.get("content_type"),
                                   "size": f.get("size"), "url": url,
                                   "tipo": f.get("tipo"), "key": f.get("key"),
                                   "uploaded_at": f.get("uploaded_at")})
            # (B) Snapshot del agente → se sirven desde Gitea (endpoint /input).
            # Solo si NO hay `files` (uploads MinIO): cuando los hay, el snapshot
            # duplica el mismo contenido pero SIN key (no borrable). Mostramos los
            # `files` (borrables) y omitimos el snapshot para no duplicar.
            if not inputs_files:
                for it in mf_data.get("pdfs") or []:
                    input_docs.append({"name": it.get("name"), "content_type": "application/pdf",
                                       "size": it.get("bytes"),
                                       "url": f"/api/wp/{wp_id}/input/inputs/pdfs/{_safe_in(it.get('name'))}"})
                for it in mf_data.get("images") or []:
                    input_docs.append({"name": it.get("name"), "content_type": it.get("content_type") or "image/*",
                                       "size": it.get("bytes"),
                                       "url": f"/api/wp/{wp_id}/input/inputs/imagenes/{_safe_in(it.get('name'))}"})
                for it in mf_data.get("documents") or []:
                    input_docs.append({"name": it.get("name"), "content_type": it.get("content_type") or "text/plain",
                                       "size": it.get("bytes"),
                                       "url": f"/api/wp/{wp_id}/input/inputs/documentos/{_safe_in(it.get('name'))}"})
        except Exception:  # noqa: BLE001
            pass
        # Transcripción + arquitectura (el contexto textual que vio el agente).
        for nm, pth in (("📝 Transcripción del requisito", "inputs/transcripcion.md"),
                        ("🏛 Arquitectura (contexto)", "inputs/arquitectura.md")):
            try:
                await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, pth, ref=branch)
                input_docs.append({"name": nm, "content_type": "text/markdown",
                                   "size": None, "url": f"/api/wp/{wp_id}/input/{pth}"})
            except Exception:  # noqa: BLE001
                pass
        # Fecha "Subido": la del manifest (uploaded_at) si existe; si no (subidas
        # previas al sellado), se deriva del historial de git del archivo en inputs/
        # — asi no queda en blanco (los inputs tambien viven en git).
        import asyncio as _aio
        _sin_fecha = [x for x in input_docs
                      if x.get("key") and not x.get("uploaded_at") and x.get("name")]
        if _sin_fecha:
            _sem = _aio.Semaphore(8)

            async def _fill_up(x: dict) -> None:
                async with _sem:
                    x["uploaded_at"] = await gt.last_commit_date(
                        _ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                        f"inputs/{x['name']}", ref=branch)
            await _aio.gather(*[_fill_up(x) for x in _sin_fecha])

    stage = None
    running = False
    if _temporal is not None:
        try:
            from workflow import FactoryWorkflow
            handle = _temporal.get_workflow_handle(f"requisito-{wp_id}")
            desc = await handle.describe()
            running = (str(desc.status).endswith("RUNNING") or
                       getattr(desc.status, "value", 0) == 1)
            if running:
                stage = await handle.query(FactoryWorkflow.current_stage)
        except Exception:  # noqa: BLE001
            pass

    # Etapa + "la pelota" desde proyecto-db (ya NO el status/assignee de OP).
    etapa_actual = det.get("etapa_actual") or stage
    etapa = etapa_from_stage(etapa_actual)
    # La UI pinta la barra de etapas arriba del detalle desde etapa.all_stages.
    etapa["all_stages"] = all_stages_for_ui()
    etapa["stage"] = etapa_actual or ""
    pelota_usuario = (det.get("pelota_usuario") or "")
    pelota_rol = det.get("pelota_rol")
    iniciado = bool(det.get("workflow_iniciado"))
    assignee_title = pelota_usuario
    is_mine = bool(user_username and pelota_usuario
                   and pelota_usuario.lower() == user_username)
    # En el POOL de mi rol (nadie la tomó) → puedo trabajarla/tomarla.
    role_match = bool(not pelota_usuario and pelota_rol and pelota_rol in user_roles)
    is_my_turn = bool(iniciado and (is_mine or role_match))
    can_take = bool(iniciado and not pelota_usuario and pelota_rol
                    and pelota_rol in user_roles)
    # ▶ Iniciar (Recepción): si NO hay workflow corriendo y soy fábrica/admin.
    can_validate_start = bool(
        not running and (user_roles & _EDITOR_ROLES))

    # Prioridad + blog colaborativo (proyecto-db; sustituye comentarios de OP).
    import proyeccion as _proy
    prioridad_actual = await _proy.get_prioridad(wp_id)
    prioridades_cat = await _proy.listar_prioridades()
    # Horas del board partidas (mig-014): para el bloque capturable del tab Plan.
    _plan_board = await _proy.get_plan(wp_id) or {}
    blog = [_ser_comentario(c) for c in await _proy.listar_comentarios(wp_id)]
    # Estado de la fase dirigida por blog (hub-and-spoke): la pelota + mi rol.
    estado_blog = await _proy.get_estado(wp_id)
    etapa_blog = (estado_blog or {}).get("etapa_actual")
    fase_blog = await _proy.get_fase(etapa_blog) if etapa_blog else None
    drivers_b = set((fase_blog or {}).get("drivers") or [])
    radios_b = set((fase_blog or {}).get("radios") or [])
    soy_driver_b = bool(user_roles & drivers_b)
    soy_radio_b = bool(user_roles & radios_b) and not soy_driver_b
    _pd = (estado_blog or {}).get("pelota_desde")
    _pelota_rol = (estado_blog or {}).get("pelota_rol")
    _pelota_usuario = (estado_blog or {}).get("pelota_usuario")
    _iniciado = bool((estado_blog or {}).get("workflow_iniciado"))
    blog_fase = {
        "etapa": etapa_blog,
        "hub_rol": (fase_blog or {}).get("hub_rol"),
        "avanza_a": (fase_blog or {}).get("avanza_a"),
        "devuelve_a": (fase_blog or {}).get("devuelve_a"),
        "drivers": sorted(drivers_b),
        "radios": sorted(radios_b),
        "soy_driver": soy_driver_b,
        "soy_radio": soy_radio_b,
        "pelota_rol": _pelota_rol,
        "pelota_usuario": _pelota_usuario,
        "pelota_desde": _pd.isoformat() if hasattr(_pd, "isoformat") else _pd,
        # Estado de arranque + "tomar la pelota del pool".
        "workflow_iniciado": _iniciado,
        "workflow_running": bool(running),
        # Puedo TOMAR si está corriendo, la pelota está en el POOL (sin persona)
        # y tengo el rol del pool.
        "puedo_tomar": bool(running and not _pelota_usuario
                            and _pelota_rol and _pelota_rol in user_roles),
        # Puedo INICIAR (▶) si NO hay workflow corriendo y soy fábrica/admin.
        "can_iniciar": bool(not running
                            and (user_roles & _EDITOR_ROLES)),
    }

    return {
        "wp_id": wp_id,
        "subject": det.get("titulo"),
        "status": etapa.get("label"),
        "project": det.get("proyecto"),
        "cliente": det.get("cliente"),
        "type": "Requisito",
        "assignee": assignee_title or None,
        "author": real_creator,
        "is_mine": is_mine,
        "is_creator": is_creator,
        "is_my_turn": is_my_turn,
        "can_take": can_take,
        "can_validate_start": can_validate_start,
        "my_primary_role": my_primary,
        "description": det.get("descripcion"),
        "attachments": [],   # adjuntos legacy de OP fuera; los inputs van en input_docs
        # Estado por bloque del paquete funcional (modelo nuevo de
        # versionado). El bloque `diseno` produce y aprueba los tres juntos.
        "spec_state": spec_state,
        "proto_state": proto_state,
        "test_plan_state": test_plan_state,
        "desarrollo_state": desarrollo_state,
        # Repo del requerimiento (rediseño "repo por requerimiento"): coords +
        # versiones de entregables publicadas. {} si aún no se provisionó.
        "dev_repo": dev_repo,
        # Preguntas de aclaración pendientes (modo blocked del agente). El panel
        # de clarificación las muestra y deja re-subir el insumo + re-enviar.
        "open_questions": clarificacion.get("questions") or [],
        "clarification_model": clarificacion.get("model") or "",
        # Asignación cliente (funcional + usuario clave). Informativo.
        "asignacion": asignacion,
        "can_assign": bool(user_roles & {"swf-funcional", "admin",
                                          "project-manager", "swf-project-manager"}),
        # Archivos subidos a MinIO (los que el agente lee). Distintos de
        # `attachments` que son los OP attachments (legacy, <5MB).
        "inputs_files": inputs_files,
        # Documentos de entrada UNIFICADOS (MinIO + snapshot del agente +
        # transcripción/arquitectura), cada uno con URL para abrirlo.
        "input_docs": input_docs,
        # Permiso de enriquecer archivos: antes de que arranque el agente
        # (Recibido) O cuando el agente está bloqueado esperando clarificación
        # (re-subir/borrar el PDF para re-correr). swf-funcional/admin o creador.
        # Enriquecer archivos: ahora se hace por el BLOG (botón en esa pestaña).
        # Se deja el permiso para fábrica/admin en Recepción/Diseño (compat UI).
        "can_add_files": bool(
            (etapa.get("key") in ("recepcion", "diseno"))
            and (user_roles & _EDITOR_ROLES)),
        # Borrar adjuntos: permitido en TODO el ciclo para fábrica/admin (el
        # endpoint valida rol/creador). A diferencia de agregar, borrar no
        # desincroniza insumos: el archivo se quita del manifest, MinIO y de
        # inputs/ en el repo de artefactos + el repo del requerimiento.
        "can_delete_files": bool(user_roles & _EDITOR_ROLES),
        "stage": stage,
        "etapa": etapa,                  # etapa de negocio (mapeo C1)
        "branch": f"req-{wp_id}",
        "artifacts_owner": _ARTIFACTS_OWNER,
        "artifacts_repo": _ARTIFACTS_REPO,
        # Reinicio a Recibido (admin/swf-funcional): termina el workflow y
        # deja el WP listo para re-validar desde cero.
        "can_reset": bool(user_roles & _EDITOR_ROLES),
        # Estructura: capacidad (parent) + proyecto + relaciones con otros
        # requisitos. can_manage_assoc gobierna reasignar capacidad y asociar.
        "project_id": project_id_num,
        "capability": capability,
        "relations": relations,
        "can_manage_assoc": bool(user_roles & _EDITOR_ROLES),
        # URLs públicas que ve el browser (links "ver en …").
        "gitea_web_url": config.GITEA_PUBLIC_URL,
        "temporal_web_url": config.TEMPORAL_WEB_URL,
        # Horas del plan partidas (mig-014): capturables en el tab Plan.
        "horas_tecnicas": (float(_plan_board["horas_tecnicas"])
                           if _plan_board.get("horas_tecnicas") is not None else None),
        "horas_funcionales": (float(_plan_board["horas_funcionales"])
                              if _plan_board.get("horas_funcionales") is not None else None),
        # Prioridad (catálogo editable) + blog colaborativo (proyecto-db).
        "prioridad": prioridad_actual or "media",
        "prioridades": prioridades_cat,
        "can_set_prioridad": bool(user_roles & {"admin", "project-manager",
                                                "swf-project-manager"}),
        "comentarios": blog,
        # Estado de la fase dirigida por blog (pelota + drivers/radios + mi rol).
        "blog_fase": blog_fase,
        # Desestimar / reactivar (estado terminal 'desestimado').
        "es_terminal": etapa_actual in ("cerrado", "desestimado"),
        "es_desestimado": etapa_actual == "desestimado",
        "can_desestimar": bool(
            user_roles & {"admin", "project-manager", "swf-project-manager"})
            and etapa_actual not in ("cerrado", "desestimado"),
        "can_reactivar": ("admin" in user_roles) and etapa_actual == "desestimado",
    }


@router.get("/api/artifact/{owner}/{repo}/{branch}/{path:path}")
async def api_artifact(owner: str, repo: str, branch: str, path: str):
    """Devuelve un archivo del repo de artefactos.

    - .html : raw HTML con Content-Type text/html (para iframe).
    - .md   : JSON con {text, html_render} (server-side markdown).
    - .json : JSON con {raw, parsed}.
    - otros : JSON con {text}.
    """
    async with Gitea() as gt:
        try:
            f = await gt.get_file(owner, repo, path, ref=branch)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    text = f["text"]
    if path.endswith(".html"):
        # Iframe sandboxed: devolvemos el HTML tal cual.
        return HTMLResponse(text)
    if path.endswith(".md"):
        html = md_lib.markdown(
            text, extensions=["fenced_code", "tables", "sane_lists", "nl2br"]
        )
        return {"text": text, "html_render": html, "sha": f["sha"]}
    if path.endswith(".json"):
        try:
            parsed = json.loads(text)
        except Exception:  # noqa: BLE001
            parsed = None
        return {"raw": text, "parsed": parsed, "sha": f["sha"]}
    return {"text": text, "sha": f["sha"]}


@router.get("/api/wp/{wp_id}/input/{path:path}")
async def api_wp_input(wp_id: int, path: str):
    """Sirve un documento de entrada del snapshot en Gitea (rama req-N) para
    que el validador lo ABRA. .md → página HTML legible; binarios (PDF/imagen)
    → inline con su content-type. Solo rutas bajo `inputs/`."""
    if not path.startswith("inputs/") or ".." in path:
        raise HTTPException(status_code=403, detail="ruta no permitida")
    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        if path.endswith(".md"):
            try:
                f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, path, ref=branch)
            except RuntimeError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return HTMLResponse(_wrap_md_pdf(f["text"], path.rsplit("/", 1)[-1]))
        try:
            f = await gt.get_file_bytes(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, path, ref=branch)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    import mimetypes
    ct = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=f["bytes"], media_type=ct)


# Carpetas de artefactos del paquete de diseño (rama req-N del repo de fábrica).
_ARTIFACT_DIRS = ("especificacion", "prototipo", "test-plan", "diseno-soporte",
                  "entregables")


@router.get("/api/wp/{wp_id}/artifacts")
async def api_wp_artifacts(wp_id: int, request: Request) -> dict:
    """Lista el paquete de diseño para el visor de la pestaña Diseño.

    Modelo nuevo (git-native): vive en `diseno/` del repo del requerimiento
    (org/repo del cliente, rama main), publicado por `git push` desde el clon
    local — incluye `diseno/soporte/` (material de apoyo). Fallback legacy: los
    bloques de la rama req-N del repo de fábrica (especificacion/, prototipo/,
    test-plan/, diseno-soporte/) para requerimientos viejos."""
    auth.require_user(request)
    import asyncio
    out: list[dict] = []

    def _add(it: dict, folder: str) -> None:
        nm = it.get("name") or ""
        out.append({"path": it.get("path"), "name": nm, "folder": folder,
                    "ext": (nm.rsplit(".", 1)[-1].lower() if "." in nm else ""),
                    "size": it.get("size") or 0})

    async def _fill_dates(gt, owner: str, repo: str, ref: str,
                          files: list[dict]) -> None:
        """Fecha del último commit por archivo (para ver el más reciente)."""
        sem = asyncio.Semaphore(8)

        async def one(f: dict) -> None:
            async with sem:
                f["date"] = await gt.last_commit_date(owner, repo, f["path"], ref=ref)
        await asyncio.gather(*[one(f) for f in files if f.get("path")])

    async with Gitea() as gt:
        # 1) Modelo nuevo: diseno/ del repo del cliente (recursivo).
        meta = await _read_dev_repo(gt, f"req-{wp_id}")
        org, repo = meta.get("org"), meta.get("repo")
        if org and repo:
            async def _walk(path: str, depth: int) -> None:
                if depth > 5:
                    return
                try:
                    items = await gt.list_contents(org, repo, path, ref="main")
                except Exception:  # noqa: BLE001
                    items = []
                for it in items:
                    if it.get("type") == "file":
                        _add(it, path)
                    elif it.get("type") == "dir":
                        sub = it.get("path") or f"{path}/{it.get('name')}"
                        await _walk(sub, depth + 1)
            await _walk("diseno", 0)
        if out:
            await _fill_dates(gt, org, repo, "main", out)
            return {"files": out}

        # 2) Fallback legacy: bloques en la rama req-N del repo de fábrica.
        branch = f"req-{wp_id}"
        for folder in _ARTIFACT_DIRS:
            try:
                items = await gt.list_contents(
                    _ARTIFACTS_OWNER, _ARTIFACTS_REPO, folder, ref=branch)
            except Exception:  # noqa: BLE001
                items = []
            for it in items:
                if it.get("type") == "file":
                    _add(it, folder)
                elif it.get("type") == "dir":
                    sub = it.get("path") or f"{folder}/{it.get('name')}"
                    try:
                        subitems = await gt.list_contents(
                            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, sub, ref=branch)
                    except Exception:  # noqa: BLE001
                        subitems = []
                    for s in subitems:
                        if s.get("type") == "file":
                            _add(s, sub)
        if out:
            await _fill_dates(gt, _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch, out)
    return {"files": out}


@router.get("/api/wp/{wp_id}/file/{path:path}")
async def api_wp_file(wp_id: int, path: str, request: Request):
    """Sirve un artefacto del paquete con su content-type real, para el visor:
    html/imagen/pdf inline, binarios descargables. Modelo nuevo: `diseno/…` se
    sirve del repo del requerimiento (cliente, rama main). Legacy: las carpetas
    de bloques de la rama req-N del repo de fábrica (allowlist)."""
    auth.require_user(request)
    if ".." in path:
        raise HTTPException(status_code=403, detail="ruta no permitida")
    async with Gitea() as gt:
        if path.startswith("diseno/"):
            # Modelo nuevo: repo del cliente, rama main.
            meta = await _read_dev_repo(gt, f"req-{wp_id}")
            org, repo = meta.get("org"), meta.get("repo")
            if not org or not repo:
                raise HTTPException(status_code=404,
                                    detail="repo del requerimiento no provisionado")
            try:
                f = await gt.get_file_bytes(org, repo, path, ref="main")
            except RuntimeError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            import mimetypes
            ct = mimetypes.guess_type(path)[0] or "application/octet-stream"
            headers = {}
            if request.query_params.get("dl"):
                headers["Content-Disposition"] = (
                    f'attachment; filename="{path.rsplit("/", 1)[-1]}"')
            return Response(content=f["bytes"], media_type=ct, headers=headers)
        # Legacy: bloques del repo de fábrica (allowlist).
        if not path.startswith(tuple(d + "/" for d in _ARTIFACT_DIRS)):
            raise HTTPException(status_code=403, detail="ruta no permitida")
        branch = f"req-{wp_id}"
        try:
            f = await gt.get_file_bytes(
                _ARTIFACTS_OWNER, _ARTIFACTS_REPO, path, ref=branch)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    import mimetypes
    ct = mimetypes.guess_type(path)[0] or "application/octet-stream"
    headers = {}
    if request.query_params.get("dl"):
        headers["Content-Disposition"] = (
            f'attachment; filename="{path.rsplit("/", 1)[-1]}"')
    return Response(content=f["bytes"], media_type=ct, headers=headers)


# Carpeta de entregables de CÓDIGO en el repo del requerimiento (org=cliente,
# rama main). El dev los publica vía el MCP `fabrica` al cerrar desarrollo /
# liberar a Pruebas Funcionales. Visor en la pestaña Desarrollo (mismo navegador+
# visor que Diseño). Ver [[rediseno-fase-desarrollo-repo]].
_DEV_ARTIFACT_ROOT = "entregables"


@router.get("/api/wp/{wp_id}/dev-artifacts")
async def api_wp_dev_artifacts(wp_id: int, request: Request) -> dict:
    """Lista los entregables de código del repo del requerimiento (org/repo del
    cliente, rama main, bajo `entregables/`). Recorre versiones y subcarpetas
    (entregables/vN/...). Para el visor de la pestaña Desarrollo."""
    auth.require_user(request)
    out: list[dict] = []

    def _add(it: dict, folder: str) -> None:
        nm = it.get("name") or ""
        out.append({"path": it.get("path"), "name": nm, "folder": folder,
                    "ext": (nm.rsplit(".", 1)[-1].lower() if "." in nm else ""),
                    "size": it.get("size") or 0})

    async with Gitea() as gt:
        meta = await _read_dev_repo(gt, f"req-{wp_id}")
        org, repo = meta.get("org"), meta.get("repo")
        if not org or not repo:
            return {"files": []}

        async def _walk(path: str, depth: int) -> None:
            if depth > 5:
                return
            try:
                items = await gt.list_contents(org, repo, path, ref="main")
            except Exception:  # noqa: BLE001
                items = []
            for it in items:
                if it.get("type") == "file":
                    _add(it, path)
                elif it.get("type") == "dir":
                    sub = it.get("path") or f"{path}/{it.get('name')}"
                    await _walk(sub, depth + 1)

        await _walk(_DEV_ARTIFACT_ROOT, 0)
    return {"files": out}


@router.get("/api/wp/{wp_id}/dev-file/{path:path}")
async def api_wp_dev_file(wp_id: int, path: str, request: Request):
    """Sirve un entregable de código del repo del requerimiento con su content-type
    real, para el visor (html/imagen/pdf inline, texto/código y binarios). Solo
    bajo `entregables/` (allowlist)."""
    auth.require_user(request)
    if ".." in path or not path.startswith(_DEV_ARTIFACT_ROOT + "/"):
        raise HTTPException(status_code=403, detail="ruta no permitida")
    async with Gitea() as gt:
        meta = await _read_dev_repo(gt, f"req-{wp_id}")
        org, repo = meta.get("org"), meta.get("repo")
        if not org or not repo:
            raise HTTPException(status_code=404, detail="repo del requerimiento no provisionado")
        try:
            f = await gt.get_file_bytes(org, repo, path, ref="main")
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    import mimetypes
    ct = mimetypes.guess_type(path)[0] or "text/plain; charset=utf-8"
    headers = {}
    if request.query_params.get("dl"):
        headers["Content-Disposition"] = (
            f'attachment; filename="{path.rsplit("/", 1)[-1]}"')
    return Response(content=f["bytes"], media_type=ct, headers=headers)


# --------------------------------------------------------------------------
# Exportación a PDF (entregable para el consultor funcional).
# Chromium headless renderiza spec/test-plan (Markdown→HTML) y el prototipo
# (HTML con JS) a PDF. El binario lo trae el Dockerfile (paquete `chromium`).
# --------------------------------------------------------------------------
_CHROMIUM_BIN = os.environ.get("CHROMIUM_BIN", "chromium")

# kind → (ruta en `diseno/` del repo del cliente, ruta legacy en el repo de
# fábrica, sufijo del archivo, formato). El modelo nuevo lee de `diseno/`; la
# ruta legacy es el fallback para requerimientos viejos.
_PDF_KINDS = {
    "spec":      ("diseno/spec.md",        "especificacion/spec.md",   "especificacion",   "md"),
    "test-plan": ("diseno/test-plan.md",   "test-plan/test-plan.md",   "casos-de-prueba",  "md"),
    "prototipo": ("diseno/prototipo.html", "prototipo/prototipo.html", "prototipo",        "html"),
}


def _slugify(text: str) -> str:
    """Nombre técnico: minúsculas, sin acentos, separado por guiones."""
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    slug = _re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return slug or "requisito"


def _wrap_md_pdf(md_text: str, title: str) -> str:
    """Markdown del artefacto → HTML con estilos de impresión (A4) limpios."""
    body = md_lib.markdown(
        md_text, extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
    safe_title = (title or "").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>{safe_title}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
          font-size: 11pt; color: #1a1a1a; line-height: 1.5; }}
  h1 {{ font-size: 20pt; border-bottom: 2px solid #0a6ed1; padding-bottom: 4px; }}
  h2 {{ font-size: 15pt; color: #0a6ed1; margin-top: 1.2em; }}
  h3 {{ font-size: 12.5pt; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 8px 0; }}
  th, td {{ border: 1px solid #c0c0c0; padding: 5px 7px; text-align: left; vertical-align: top; }}
  th {{ background: #f0f4f8; }}
  tr, h1, h2, h3 {{ page-break-inside: avoid; }}
  code {{ background: #f5f5f5; padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }}
  pre {{ background: #f5f5f5; padding: 10px; border-radius: 4px; overflow: auto; }}
</style></head><body>{body}</body></html>"""


# Este HTML del prototipo se renderiza AISLADO (solo para el PDF), así que
# revelamos TODAS las vistas (una por página) y ocultamos tabs/diálogos. NO
# usamos @media print: el headless no siempre lo aplica en --print-to-pdf y
# entonces ganaba el .view-hidden{display:none} del framework (salía 1 vista).
_PROTO_PRINT_HEAD = """<style>
@page { size: A4; margin: 12mm; }
body { background: #fff !important; }
.f-tabs, .f-dialog-bg { display: none !important; }
[data-view-content] { display: block !important; }
[data-view-content] + [data-view-content] { break-before: page; }
</style>"""

# Refuerzo: estilos INLINE vía script (máxima prioridad, inmune a !important y
# al orden de hojas) + page-break legacy. Corre en 'load', antes del snapshot.
_PROTO_PRINT_SCRIPT = """<script>
window.addEventListener('load', function () {
  var secs = document.querySelectorAll('[data-view-content]');
  secs.forEach(function (s, i) {
    s.classList.remove('view-hidden');
    s.style.display = 'block';
    if (i > 0) { s.style.breakBefore = 'page'; s.style.pageBreakBefore = 'always'; }
  });
  document.querySelectorAll('.f-tabs, .f-dialog-bg').forEach(function (e) {
    e.style.display = 'none';
  });
});
</script>"""


def _inject_print_css(html: str) -> str:
    """Prepara el prototipo para el PDF: revela TODAS las vistas (una por
    página) con CSS no-gated + un script que lo fuerza con estilos inline."""
    if "</head>" in html:
        html = html.replace("</head>", _PROTO_PRINT_HEAD + "</head>", 1)
    else:
        html = _PROTO_PRINT_HEAD + html
    if "</body>" in html:
        html = html.replace("</body>", _PROTO_PRINT_SCRIPT + "</body>", 1)
    else:
        html = html + _PROTO_PRINT_SCRIPT
    return html


async def _html_to_pdf(html: str) -> bytes:
    """Renderiza HTML (con su JS/CSS) a PDF vía Chromium headless. Escribe a
    archivos temporales y corre `chromium --print-to-pdf`. --no-sandbox es
    obligatorio en contenedor; --virtual-time-budget deja correr el JS del
    prototipo antes del snapshot."""
    tmpdir = tempfile.mkdtemp(prefix="fabro-pdf-")
    in_path = os.path.join(tmpdir, "in.html")
    out_path = os.path.join(tmpdir, "out.pdf")
    profile = os.path.join(tmpdir, "profile")
    try:
        with open(in_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        proc = await asyncio.create_subprocess_exec(
            _CHROMIUM_BIN,
            "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--no-first-run",
            "--disable-background-networking", "--disable-extensions",
            "--disable-sync", "--virtual-time-budget=8000",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={out_path}",
            f"file://{in_path}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("chromium excedió el tiempo (45s)")
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(
                f"chromium rc={proc.returncode}: "
                f"{stderr.decode('utf-8', 'ignore')[:300]}")
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/api/wp/{wp_id}/pdf/{kind}")
async def api_wp_pdf(wp_id: int, kind: str, request: Request):
    """Genera y descarga el artefacto `kind` (spec|test-plan|prototipo) del WP
    como PDF, con nombre técnico derivado del título del requisito:
    `{slug-del-titulo}-{sufijo}.pdf`."""
    auth.require_user(request)
    spec = _PDF_KINDS.get(kind)
    if not spec:
        raise HTTPException(status_code=404, detail=f"tipo de PDF desconocido: {kind}")
    new_path, legacy_path, name_part, fmt = spec
    branch = f"req-{wp_id}"
    import proyeccion
    txt = await proyeccion.get_requisito_texto(wp_id)
    subject = (txt or {}).get("titulo") or f"requisito-{wp_id}"
    slug = _slugify(subject)
    async with Gitea() as gt:
        f = None
        # Modelo nuevo: diseno/ del repo del cliente (rama main).
        meta = await _read_dev_repo(gt, branch)
        _dorg, _drepo = meta.get("org"), meta.get("repo")
        if _dorg and _drepo:
            try:
                f = await gt.get_file(_dorg, _drepo, new_path, ref="main")
            except Exception:  # noqa: BLE001
                f = None
        # Fallback legacy: bloque en la rama req-N del repo de fábrica.
        if f is None:
            try:
                f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                      legacy_path, ref=branch)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=404,
                    detail=f"no existe {new_path} (¿ya publicaste el paquete por git push?)"
                ) from exc
    html = _wrap_md_pdf(f["text"], title=subject) if fmt == "md" else _inject_print_css(f["text"])
    try:
        pdf_bytes = await _html_to_pdf(html)
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF %s WP #%s falló: %s", kind, wp_id, exc)
        raise HTTPException(
            status_code=500, detail=f"no se pudo generar el PDF: {exc}") from exc
    filename = f"{slug}-{name_part}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/skills")
async def api_skills(request: Request) -> dict:
    """Catalogo de skills de desarrollo seleccionables — para el dropdown al
    liberar desarrollo. Lee skills/catalog.yaml del repo de arquitectura."""
    auth.require_user(request)
    if not config.FACTORY_ARCH_REPO or not config.FACTORY_SKILLS_CATALOG_PATH:
        return {"default": None, "skills": []}
    owner, _, repo = config.FACTORY_ARCH_REPO.partition("/")
    async with Gitea() as gt:
        try:
            f = await gt.get_file(owner, repo,
                                  config.FACTORY_SKILLS_CATALOG_PATH,
                                  ref=config.FACTORY_ARCH_BRANCH)
        except Exception:  # noqa: BLE001
            return {"default": None, "skills": []}
    try:
        cat = yaml.safe_load(f["text"]) or {}
    except Exception:  # noqa: BLE001
        return {"default": None, "skills": []}
    skills = [{"key": s.get("key"), "title": s.get("title"),
               "when_to_use": s.get("when_to_use")}
              for s in (cat.get("skills") or []) if s.get("key")]
    return {"default": cat.get("default"), "skills": skills}


@router.get("/api/profiles")
async def api_profiles(request: Request) -> dict:
    """Catálogo de PERFILES/stack (para el dropdown al validar). Cada perfil
    define el skill de diseño + el dev por defecto. Lee skills/catalog.yaml."""
    auth.require_user(request)
    if not config.FACTORY_ARCH_REPO or not config.FACTORY_SKILLS_CATALOG_PATH:
        return {"default_profile": None, "profiles": []}
    owner, _, repo = config.FACTORY_ARCH_REPO.partition("/")
    async with Gitea() as gt:
        try:
            f = await gt.get_file(owner, repo,
                                  config.FACTORY_SKILLS_CATALOG_PATH,
                                  ref=config.FACTORY_ARCH_BRANCH)
        except Exception:  # noqa: BLE001
            return {"default_profile": None, "profiles": []}
    try:
        cat = yaml.safe_load(f["text"]) or {}
    except Exception:  # noqa: BLE001
        return {"default_profile": None, "profiles": []}
    profiles = [{"key": p.get("key"), "title": p.get("title"),
                 "when_to_use": p.get("when_to_use"),
                 "dev_skill_key": p.get("dev_skill_key")}
                for p in (cat.get("profiles") or []) if p.get("key")]
    return {"default_profile": cat.get("default_profile"), "profiles": profiles}


async def _persist_profile(wp_id: int, profile_key: str) -> dict | None:
    """Persiste el PERFIL/stack elegido al validar: escribe `profile.json` en la
    rama `req-N` del repo de artefactos. Lo lee la maquinaria de diseño (design
    skill + is_abap) y el `.mcp.json` deriva de aquí el header `X-Fabrica-Stack`
    que el MCP usa para resolver el skill del stack. Devuelve el perfil del
    catálogo o None si la clave no existe / no se pudo escribir. Best-effort."""
    profile_key = (profile_key or "").strip()
    if not profile_key:
        return None
    try:
        from activities import load_skill_catalog
        cat = await load_skill_catalog()
        prof = next((p for p in cat.get("profiles", [])
                     if p.get("key") == profile_key), None)
        if not prof:
            return None
        branch = f"req-{wp_id}"
        async with Gitea() as gt:
            await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
            await gt.put_file(
                _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch, "profile.json",
                json.dumps({"profile": prof["key"], "key": prof["key"],
                            "title": prof.get("title"),
                            "design_skill": prof.get("design_skill"),
                            "dev_skill_key": prof.get("dev_skill_key")},
                           indent=2, ensure_ascii=False),
                f"perfil {prof['key']} refs #{wp_id}")
        return prof
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo persistir profile.json (perfil %s): %s",
                    profile_key, exc)
        return None


@router.post("/api/wp/{wp_id}/approve-and-start")
async def api_wp_approve_and_start(wp_id: int, request: Request,
                                   body: dict | None = None) -> dict:
    """D1: SwF Funcional/admin valida un requisito creado por funcional/
    usuario-clave y arranca el workflow. Falla si ya estaba arrancado.

    Body opcional: {edited_subject, edited_description} para enriquecer/
    corregir el requisito antes de mandarlo al agente. Si vienen y difieren
    de lo que hay en OP, se persisten en el WP (PATCH) y se usan en el
    arranque del workflow."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(
            status_code=403,
            detail="solo swf-funcional/admin pueden validar requisitos")
    # Ediciones opcionales del validador (enriquecer antes de mandar al agente)
    edited_subject = None
    edited_description = None
    if body:
        es = body.get("edited_subject")
        ed = body.get("edited_description")
        if es is not None and str(es).strip():
            edited_subject = str(es).strip()
        if ed is not None:
            edited_description = str(ed)
    # Verificar que NO esté ya corriendo
    if _temporal is not None:
        try:
            handle = _temporal.get_workflow_handle(f"requisito-{wp_id}")
            desc = await handle.describe()
            if (str(desc.status).endswith("RUNNING") or
                    getattr(desc.status, "value", 0) == 1):
                raise HTTPException(
                    status_code=409,
                    detail="El workflow ya está corriendo para este requisito")
        except RPCError:
            pass  # NotFound: aún no existe, OK
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            pass
    # Datos del requisito desde proyecto-db (ya NO OpenProject).
    import proyeccion
    det = await proyeccion.get_requisito_detalle(wp_id) or {}
    project_id = det.get("proyecto_id")
    subject = det.get("titulo") or ""
    description = det.get("descripcion") or ""
    patch_subject = (edited_subject if edited_subject is not None
                     and edited_subject != subject else None)
    patch_description = (edited_description if edited_description is not None
                         and edited_description != description else None)
    edited = patch_subject is not None or patch_description is not None
    if edited:
        await proyeccion.set_requisito_texto(
            wp_id, titulo=patch_subject, descripcion=patch_description)
        if patch_subject is not None:
            subject = patch_subject
        if patch_description is not None:
            description = patch_description
    await proyeccion.agregar_comentario(
        wp_id, user.get("username") or "fabrica",
        user.get("name") or user.get("username") or "Fábrica",
        (f"✅ Requisito **iniciado** por **{user.get('name') or user.get('username')}** "
         f"(rol: `{_primary_role(list(roles))}`)."
         + ("\n\n✏️ Enriqueció el requisito antes de iniciarlo." if edited else "")
         + "\n\nEntra a **Recepción** del flujo dirigido por blog."), None)
    if not project_id:
        raise HTTPException(
            status_code=500,
            detail="No se pudo derivar el proyecto (¿requisito en el board?)")
    # Persistir el PERFIL/stack elegido al validar → gobierna el diseño (skill
    # de diseño) y el default del skill de dev. Se escribe ANTES de arrancar el
    # workflow para que la etapa de diseño lo vea.
    await _persist_profile(wp_id, (body or {}).get("profile") or "")
    from webhook import factory_start, StartRequest
    start_req = StartRequest(work_package_id=wp_id, project_id=project_id,
                              subject=subject, description=description)
    started = await factory_start(start_req)
    return {"ok": True, "started": started}


@router.post("/api/wp/{wp_id}/edit-meta")
async def api_wp_edit_meta(wp_id: int, request: Request,
                           body: dict | None = None) -> dict:
    """Edita SOLO título/descripción del requisito mientras está en Recibido
    (workflow aún sin arrancar). NO arranca nada — a diferencia de
    approve-and-start. Permiso: swf-funcional/admin.

    Body: {subject?, description?} — solo se persisten los campos que vengan
    y difieran de lo actual."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(
            status_code=403,
            detail="solo swf-funcional/admin pueden editar el requisito")
    body = body or {}
    new_subject = None
    new_description = None
    if body.get("subject") is not None and str(body["subject"]).strip():
        new_subject = str(body["subject"]).strip()
    if body.get("description") is not None:
        new_description = str(body["description"])
    if new_subject is None and new_description is None:
        raise HTTPException(status_code=400,
                            detail="nada que guardar (subject/description)")
    # Solo en Recibido: rechazar si el workflow ya está corriendo.
    if _temporal is not None:
        try:
            handle = _temporal.get_workflow_handle(f"requisito-{wp_id}")
            desc = await handle.describe()
            if (str(desc.status).endswith("RUNNING") or
                    getattr(desc.status, "value", 0) == 1):
                raise HTTPException(
                    status_code=409,
                    detail="El workflow ya arrancó; no se puede editar aquí")
        except RPCError:
            pass  # NotFound: aún no existe, OK
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            pass
    import proyeccion
    cur = await proyeccion.get_requisito_texto(wp_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="requisito no existe")
    subject = cur.get("titulo") or ""
    description = cur.get("descripcion") or ""
    patch_subject = (new_subject if new_subject is not None
                     and new_subject != subject else None)
    patch_description = (new_description if new_description is not None
                         and new_description != description else None)
    if patch_subject is None and patch_description is None:
        return {"ok": True, "changed": False}
    await proyeccion.set_requisito_texto(
        wp_id, titulo=patch_subject, descripcion=patch_description)
    await proyeccion.agregar_comentario(
        wp_id, user.get("username") or "?",
        user.get("name") or user.get("username") or "?",
        "✏️ Requisito **editado** ("
        + ", ".join(
            (["título"] if patch_subject is not None else [])
            + (["descripción"] if patch_description is not None else []))
        + ") en *Recepción*.",
        accion="responder", etapa="recepcion")
    return {"ok": True, "changed": True}


@router.post("/api/wp/{wp_id}/capability")
async def api_wp_set_capability(wp_id: int, request: Request, body: dict) -> dict:
    """Reasigna la Capability (parent) de un Requisito ya creado. Permiso:
    swf-funcional/admin. La capability destino debe ser tipo Capability (9)
    y del MISMO proyecto que el requisito."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(
            status_code=403,
            detail="solo swf-funcional/admin pueden reasignar la capacidad")
    new_cap = body.get("capability_id")
    try:
        new_cap = int(new_cap)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="capability_id requerido")
    import proyeccion
    res = await proyeccion.set_capacidad(wp_id, new_cap)
    if res is None:
        raise HTTPException(
            status_code=400,
            detail="capacidad inexistente o de otro proyecto")
    await proyeccion.agregar_comentario(
        wp_id, user.get("username") or "?",
        user.get("name") or user.get("username") or "?",
        f"🎯 Capacidad reasignada → **{res['capacidad_nombre']}**.",
        accion="responder")
    return {"ok": True, "capability_id": new_cap,
            "capability_title": res["capacidad_nombre"]}


@router.post("/api/wp/{wp_id}/relations")
async def api_wp_add_relation(wp_id: int, request: Request, body: dict) -> dict:
    """Asocia este requisito con otro (relación 'relates'). Permiso:
    swf-funcional/admin. El destino debe ser tipo Requisito (8) y distinto."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(
            status_code=403,
            detail="solo swf-funcional/admin pueden asociar requisitos")
    try:
        to_id = int(body.get("to_wp_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="to_wp_id inválido")
    if to_id == wp_id:
        raise HTTPException(status_code=400,
                            detail="no puede relacionarse consigo mismo")
    import proyeccion
    other = await proyeccion.get_requisito_detalle(to_id)
    if not other:
        raise HTTPException(status_code=404,
                            detail=f"No existe el requisito #{to_id}")
    # Evitar duplicado (relación ya existente en cualquier sentido).
    for rel in await proyeccion.listar_relaciones(wp_id):
        if rel.get("wp_id") == to_id:
            return {"ok": True, "already": True, "to_wp_id": to_id}
    ok = await proyeccion.crear_relacion(wp_id, to_id, "relacionado")
    if not ok:
        raise HTTPException(status_code=400,
                            detail="No se pudo crear la relación")
    await proyeccion.agregar_comentario(
        wp_id, user.get("username") or "?",
        user.get("name") or user.get("username") or "?",
        f"🔗 Relacionado con **#{to_id} {other.get('titulo')}**.",
        accion="responder")
    return {"ok": True, "to_wp_id": to_id, "subject": other.get("titulo")}


@router.delete("/api/wp/{wp_id}/relations/{relation_id}")
async def api_wp_del_relation(wp_id: int, relation_id: int,
                              request: Request) -> dict:
    """Quita una relación entre requisitos. Permiso: swf-funcional/admin."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin")
    import proyeccion
    await proyeccion.eliminar_relacion(relation_id)
    return {"ok": True}


@router.post("/api/wp/{wp_id}/add-files")
async def api_wp_add_files(wp_id: int, request: Request, body: dict) -> dict:
    """Agrega archivos al manifest del WP (los archivos ya están en MinIO,
    subidos via presigned URL). Mergea con el manifest existente. Solo
    permitido mientras el workflow NO haya arrancado (antes de validar).

    Body: {files: [{filename, key, size, content_type}]}"""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    username = (user.get("username") or "").strip().lower()
    files_new = body.get("files") or []
    if not files_new:
        raise HTTPException(status_code=400, detail="files vacío")
    # Validar permiso: swf-funcional/admin, o el creador real
    import proyeccion
    is_authorized = bool(roles & _EDITOR_ROLES)
    if not is_authorized and username:
        real_creator = await proyeccion.get_creador(wp_id)
        is_authorized = bool(real_creator and real_creator.lower() == username)
    if not is_authorized:
        raise HTTPException(
            status_code=403,
            detail="solo swf-funcional/admin o el creador pueden agregar archivos")
    # Validar que el workflow no esté ya corriendo — EXCEPTO si está parado en
    # "Esperando clarificación": ahí justamente queremos dejar re-subir el
    # PDF/transcripción para que el agente re-corra con mejor insumo.
    if _temporal is not None:
        try:
            from workflow import FactoryWorkflow
            handle = _temporal.get_workflow_handle(f"requisito-{wp_id}")
            desc = await handle.describe()
            running = (str(desc.status).endswith("RUNNING") or
                       getattr(desc.status, "value", 0) == 1)
            if running:
                cur_stage = ""
                try:
                    cur_stage = await handle.query(FactoryWorkflow.current_stage)
                except Exception:  # noqa: BLE001
                    cur_stage = ""
                if cur_stage != "esperando_clarificacion":
                    raise HTTPException(
                        status_code=409,
                        detail="El workflow ya está corriendo — para agregar archivos abre un CR (Sprint B)")
        except RPCError:
            pass
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            pass

    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        # Leer manifest existente (si hay) y mergear
        existing_files: list[dict] = []
        try:
            mf = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                    "inputs/manifest.json", ref=branch)
            existing_files = (json.loads(mf["text"]).get("files") or [])
        except Exception:  # noqa: BLE001
            pass
        # Dedup por key MinIO (no re-agregar el mismo objeto) + nombre único
        # (consecutivo) contra los ya presentes, para que dos archivos no
        # terminen con el mismo filename (colisión en Gitea / _dedup_by_name).
        keys_existing = {f.get("key") for f in existing_files}
        used_names = {(f.get("filename") or "") for f in existing_files}
        new_clean = []
        for f in files_new:
            if f.get("key") in keys_existing:
                continue
            new_clean.append({
                "filename": _next_unique_name(f.get("filename") or "archivo", used_names),
                "key": f.get("key"), "size": f.get("size"),
                "tipo": f.get("tipo"), "content_type": f.get("content_type")})
        all_files = existing_files + new_clean
        manifest = {
            "wp_id": wp_id, "files": all_files,
            "last_updated_by": user.get("username"),
            "last_updated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        }
        await gt.put_file(
            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
            "inputs/manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
            f"inputs manifest +{len(new_clean)} archivo(s) refs #{wp_id}")

    # Comentario al blog para trazabilidad
    if new_clean:
        lines = [f"📎 Agregó {len(new_clean)} archivo(s) al manifest:"]
        for f in new_clean:
            size_mb = (f.get("size") or 0) / 1024 / 1024
            lines.append(f"- `{f.get('filename')}` ({size_mb:.2f} MB)")
        try:
            await proyeccion.agregar_comentario(
                wp_id, user.get("username") or "?",
                user.get("name") or user.get("username") or "?",
                "\n".join(lines), accion="responder")
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "added": len(new_clean), "total": len(all_files)}


@router.post("/api/wp/{wp_id}/delete-file")
async def api_wp_delete_file(wp_id: int, request: Request, body: dict) -> dict:
    """Borra un adjunto (subido a MinIO) del manifest del requisito + del
    object storage. Permiso: swf-funcional/admin o el creador real."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    username = (user.get("username") or "").strip().lower()
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key requerido")
    import proyeccion
    is_auth = bool(roles & _EDITOR_ROLES)
    if not is_auth and username:
        rc = await proyeccion.get_creador(wp_id)
        is_auth = bool(rc and rc.lower() == username)
    if not is_auth:
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin o el creador")
    branch = f"req-{wp_id}"
    removed = None
    async with Gitea() as gt:
        try:
            mf = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                    "inputs/manifest.json", ref=branch)
            data = json.loads(mf["text"])
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=404, detail="sin manifest")
        files = data.get("files") or []
        removed = next((f for f in files if f.get("key") == key), None)
        if not removed:
            raise HTTPException(status_code=404, detail="adjunto no encontrado")
        data["files"] = [f for f in files if f.get("key") != key]
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          "inputs/manifest.json",
                          json.dumps(data, indent=2, ensure_ascii=False),
                          f"borrar adjunto refs #{wp_id}")
        # Borrar también los bytes materializados en inputs/ (repo de artefactos
        # y, si está provisionado, el repo del requerimiento) para que un
        # `git pull` no conserve el archivo. Rutas candidatas según el esquema:
        # blog = plano inputs/<nombre>; snapshot del agente = pdfs/ | imagenes/.
        fn = removed.get("filename") or ""
        ct = (removed.get("content_type") or "").lower()
        cand = [f"inputs/{fn}"]
        if ct == "application/pdf" or fn.lower().endswith(".pdf"):
            cand.append(f"inputs/pdfs/{fn}")
        if ct.startswith("image/"):
            cand.append(f"inputs/imagenes/{fn}")
        targets = [(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)]
        dev = await _read_dev_repo(gt, branch)
        if dev.get("org") and dev.get("repo"):
            targets.append((dev["org"], dev["repo"], "main"))
        for _o, _r, _b in targets:
            for _p in cand:
                try:
                    await gt.delete_file(_o, _r, _b, _p,
                                         f"borrar input {fn} refs #{wp_id}")
                except Exception:  # noqa: BLE001
                    pass
    try:
        await S3Storage.delete(key)
    except Exception:  # noqa: BLE001
        pass
    try:
        await proyeccion.agregar_comentario(
            wp_id, user.get("username") or "?",
            user.get("name") or username or "?",
            f"🗑 Borró el adjunto `{removed.get('filename') or key}`.",
            accion="responder")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "removed": removed.get("filename")}


@router.post("/api/wp/{wp_id}/take")
async def api_wp_take(wp_id: int, request: Request) -> dict:
    """Self-assign (acción de blog 'tomar'): el usuario actual toma la pelota del
    requisito. Solo si está sin persona asignada (pool del rol). Escribe en
    proyecto-db (pelota_usuario)."""
    user = auth.require_user(request)
    login = (user.get("username") or "").strip()
    if not login:
        raise HTTPException(status_code=400, detail="usuario sin username")
    import proyeccion
    det = await proyeccion.get_requisito_detalle(wp_id)
    if not det:
        raise HTTPException(status_code=404, detail="requisito no existe")
    if det.get("pelota_usuario"):
        raise HTTPException(
            status_code=409,
            detail=f"Ya lo tomó {det.get('pelota_usuario')}")
    await proyeccion.agregar_comentario(
        wp_id, login, user.get("name") or login,
        f"🤚 Tomó el requisito (rol: {_primary_role(user.get('roles') or [])}).",
        accion="tomar")
    return {"ok": True, "assignee": user.get("name") or login,
            "assignee_id": login}


# --------------------------------------------------------------------------
# Blog colaborativo (proyecto-db) + prioridad — SUSTITUYE los comentarios de OP.
# Los archivos del blog son INPUTS: sus bytes se committean al repo /inputs/ en
# Gitea para que un `git pull` los traiga (dev / revisor de spec en Claude Code).
# --------------------------------------------------------------------------
def _next_unique_name(name: str, used: set) -> str:
    """Nombre único respecto a `used` (se actualiza in-place). Si colisiona,
    inserta un consecutivo -2, -3, … ANTES de la extensión — misma convención
    que buildFileName en el cliente. Evita que dos archivos con el mismo nombre
    se sobreescriban en Gitea inputs/<nombre> o se descarten en _dedup_by_name
    al armar los inputs del agente."""
    name = name or "archivo"
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:                       # sin extensión
        stem, ext = name, ""
    i = 2
    while True:
        cand = f"{stem}-{i}.{ext}" if ext else f"{stem}-{i}"
        if cand not in used:
            used.add(cand)
            return cand
        i += 1


async def _commit_inputs_a_gitea(wp_id: int, files: list[dict],
                                 actor: str) -> list[dict]:
    """Committea los BYTES de cada archivo (ya en MinIO) al repo en Gitea bajo
    /inputs/ y mergea inputs/manifest.json. Escribe en el repo de artefactos
    (rama req-N, de donde provisionar copia /inputs al repo del cliente) y, si el
    repo del requerimiento ya está provisionado, también en su rama main. Devuelve
    los files enriquecidos con repo_path/minio_key."""
    import base64 as _b64
    branch = f"req-{wp_id}"
    out: list[dict] = []
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        dev_repo = await _read_dev_repo(gt, branch)
        dev_org, dev_name = dev_repo.get("org"), dev_repo.get("repo")
        existing_files: list[dict] = []
        try:
            mf = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                   "inputs/manifest.json", ref=branch)
            existing_files = (json.loads(mf["text"]).get("files") or [])
        except Exception:  # noqa: BLE001
            pass
        keys_existing = {f.get("key") for f in existing_files}
        # Nombres ya presentes en el manifest — para numerar (consecutivo) los
        # nuevos que colisionen y NO sobreescribir inputs/<nombre> en Gitea ni
        # perderlos en _dedup_by_name al armar los inputs del agente.
        used_names = {(f.get("filename") or "") for f in existing_files}
        nuevos_manifest: list[dict] = []
        for f in files:
            key = f.get("key")
            if not key:
                continue
            filename = _next_unique_name(f.get("filename") or "archivo", used_names)
            repo_path = f"inputs/{filename}"
            try:
                data = await S3Storage.download(key)
                b64 = _b64.b64encode(data).decode("ascii")
                msg = f"input {filename} (blog, por {actor}) refs #{wp_id}"
                await gt.put_file_binary(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                         branch, repo_path, b64, msg)
                if dev_org and dev_name:
                    try:
                        await gt.put_file_binary(dev_org, dev_name, "main",
                                                 repo_path, b64, msg)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("commit input al dev repo falló %s: %s",
                                    filename, exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("commit input %s a Gitea falló: %s", filename, exc)
                repo_path = None
            out.append({"filename": filename, "minio_key": key,
                        "repo_path": repo_path, "size": f.get("size"),
                        "content_type": f.get("content_type")})
            if key not in keys_existing:
                nuevos_manifest.append({
                    "filename": filename, "key": key, "size": f.get("size"),
                    "tipo": f.get("tipo"), "content_type": f.get("content_type"),
                    "uploaded_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())})
        if nuevos_manifest:
            all_files = existing_files + nuevos_manifest
            manifest = {"wp_id": wp_id, "files": all_files,
                        "last_updated_by": actor,
                        "last_updated_at": _time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", _time.gmtime())}
            await gt.put_file(
                _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                "inputs/manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False),
                f"inputs manifest +{len(nuevos_manifest)} (blog) refs #{wp_id}")
    return out


def _ser_comentario(c: dict) -> dict:
    adj_out = []
    for a in (c.get("adjuntos") or []):
        url = None
        if a.get("minio_key"):
            try:
                url = S3Storage.presigned_get_url(a["minio_key"])
            except Exception:  # noqa: BLE001
                url = None
        adj_out.append({"filename": a.get("filename"), "size": a.get("size"),
                        "content_type": a.get("content_type"),
                        "repo_path": a.get("repo_path"), "url": url})
    ca = c.get("created_at")
    return {"id": c.get("id"), "autor": c.get("autor"),
            "autor_nombre": c.get("autor_nombre"), "cuerpo": c.get("cuerpo"),
            "created_at": ca.isoformat() if hasattr(ca, "isoformat") else ca,
            "accion": c.get("accion") or "responder",
            "destino_rol": c.get("destino_rol"),
            "destino_usuario": c.get("destino_usuario"),
            "adjuntos": adj_out}


@router.get("/api/wp/{wp_id}/blog")
async def api_wp_blog(wp_id: int, request: Request) -> dict:
    """Hilo del blog colaborativo (comentarios + adjuntos)."""
    auth.require_user(request)
    import proyeccion
    coms = await proyeccion.listar_comentarios(wp_id)
    return {"comentarios": [_ser_comentario(c) for c in coms],
            "db": proyeccion.habilitada()}


async def _signal_blog_action(wp_id: int, accion: str, destino_rol: str | None,
                              destino_usuario: str | None, actor: str) -> None:
    """Señala al workflow Temporal una acción del blog que DIRIGE el flujo
    (delegar/aprobar/rechazar). Best-effort: si el workflow no corre o no tiene
    el handler (modelo viejo, pre-cutover), se ignora — la pelota/registro ya
    quedó en la DB. El workflow nuevo (FactoryWorkflow) maneja `blog_action`."""
    if _temporal is None:
        return
    try:
        handle = _temporal.get_workflow_handle(f"requisito-{wp_id}")
        await handle.signal("blog_action", {
            "accion": accion, "destino_rol": destino_rol,
            "destino_usuario": destino_usuario, "actor": actor})
    except Exception as exc:  # noqa: BLE001
        log.warning("signal blog_action wp=%s falló (no fatal): %s", wp_id, exc)


@router.post("/api/wp/{wp_id}/comment")
async def api_wp_comment(wp_id: int, request: Request, body: dict) -> dict:
    """Comentario del blog (proyecto-db) que puede DIRIGIR el flujo:
      - responder: comentario normal; si lo hace un RADIO, la pelota vuelve al hub.
      - delegar (revisar): un DRIVER pasa la pelota a `destino_rol`/`destino_usuario`.
      - aprobar / rechazar: un DRIVER mueve la fase (vía el workflow).
    Si trae `files`, sus BYTES se committean al repo /inputs/ en Gitea (= inputs)."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    text = (body.get("text") or "").strip()
    files = body.get("files") or []
    accion = (body.get("accion") or "responder").strip()
    destino_rol = (body.get("destino_rol") or "").strip() or None
    destino_usuario = (body.get("destino_usuario") or "").strip() or None
    if accion not in ("responder", "delegar", "aprobar", "rechazar",
                      "devolver", "tomar"):
        raise HTTPException(status_code=400, detail="acción inválida")
    if accion == "responder" and not text and not files:
        raise HTTPException(status_code=400, detail="texto o archivo requerido")
    autor = (user.get("username") or "").strip() or "anon"
    autor_nombre = user.get("name") or user.get("username") or autor

    import proyeccion
    estado = await proyeccion.get_estado(wp_id)
    etapa = (estado or {}).get("etapa_actual")
    pelota_rol = (estado or {}).get("pelota_rol")
    fase = await proyeccion.get_fase(etapa) if etapa else None
    drivers = set((fase or {}).get("drivers") or [])
    radios = set((fase or {}).get("radios") or [])
    soy_driver = bool(roles & drivers)
    soy_radio = bool(roles & radios) and not soy_driver
    # Solo los drivers de la fase mueven el flujo.
    if accion in ("delegar", "aprobar", "rechazar", "devolver") and not soy_driver:
        raise HTTPException(
            status_code=403,
            detail=f"solo un driver de la fase '{etapa}' puede {accion}")
    if accion == "delegar" and not (destino_rol or destino_usuario):
        raise HTTPException(status_code=400, detail="delegar requiere un destino")
    # 'tomar': reclamar la pelota del POOL → solo quien tiene el rol del pool.
    if accion == "tomar" and pelota_rol and pelota_rol not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"solo el rol '{pelota_rol}' puede tomar esta pelota")

    adjuntos: list[dict] = []
    if files:
        adjuntos = await _commit_inputs_a_gitea(wp_id, files, autor_nombre)
    cuerpo = text
    if not cuerpo:
        cuerpo = {
            "aprobar": "✅ Aprobado",
            "rechazar": "⛔ Rechazado",
            "devolver": "↩️ Devuelto a la fase anterior",
            "tomar": f"✋ {autor_nombre} tomó el requerimiento",
            "delegar": f"🔎 Pasa la pelota a {destino_usuario or destino_rol}",
        }.get(accion) or (f"(adjuntó {len(adjuntos)} archivo(s))" if adjuntos
                          else "(sin texto)")
    cid = await proyeccion.agregar_comentario(
        wp_id, autor, autor_nombre, cuerpo, adjuntos, accion=accion,
        destino_rol=destino_rol, destino_usuario=destino_usuario,
        etapa=etapa, autor_es_radio=soy_radio)
    if cid is None:
        raise HTTPException(status_code=503,
                            detail="blog no disponible (DB de proyecto)")
    # Al aprobar Recepción→Diseño: fijar el PERFIL/stack del requerimiento ANTES
    # de señalar (la transición a Diseño provisiona el repo y debe verlo, para
    # que la spec se genere con el design skill + convenciones del stack elegido).
    if accion == "aprobar" and etapa == "recepcion":
        await _persist_profile(wp_id, (body.get("profile") or ""))
    if accion in ("delegar", "aprobar", "rechazar", "devolver"):
        await _signal_blog_action(wp_id, accion, destino_rol, destino_usuario, autor)
    return {"ok": True, "id": cid, "adjuntos": len(adjuntos), "accion": accion}


@router.post("/api/wp/{wp_id}/prioridad")
async def api_wp_prioridad(wp_id: int, request: Request, body: dict) -> dict:
    """Fija la prioridad del requisito (catálogo `prioridad`). Solo admin y
    líder de proyecto (project-manager / swf-project-manager)."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & {"admin", "project-manager", "swf-project-manager"}):
        raise HTTPException(
            status_code=403,
            detail="solo admin / líder de proyecto cambian la prioridad")
    pr = (body.get("prioridad") or "").strip()
    if not pr:
        raise HTTPException(status_code=400, detail="prioridad requerida")
    import proyeccion
    if not await proyeccion.set_prioridad(wp_id, pr):
        raise HTTPException(
            status_code=400,
            detail="no se pudo fijar (¿requisito aún no registrado en proyecto-db?)")
    return {"ok": True, "prioridad": pr}


@router.post("/api/wp/{wp_id}/campo")
async def api_wp_campo(wp_id: int, request: Request, body: dict) -> dict:
    """Edita un campo del requerimiento desde el Gantt del inbox: fin (recalcula
    inicio), funcional, desarrollador o usuario_clave. Permiso: editores (SwF /
    admin / líder de proyecto / líder fábrica)."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    field = (body.get("field") or "").strip()
    if field == "avance_pct":
        # El % de avance manual lo captura el administrador/líder de proyecto.
        if not (roles & _LEADER_ROLES):
            raise HTTPException(status_code=403,
                                detail="solo admin / líder de proyecto fija el avance")
    elif not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403, detail="rol no autorizado")
    elif field not in ("fin", "horas", "horas_tecnicas", "horas_funcionales",
                        "funcional", "desarrollador", "usuario_clave"):
        raise HTTPException(status_code=400, detail="campo inválido")
    import proyeccion
    if not await proyeccion.editar_campo(wp_id, field, body.get("value") or ""):
        raise HTTPException(status_code=400, detail="no se pudo guardar")
    return {"ok": True}


@router.post("/api/wp/{wp_id}/review")
async def api_wp_review(wp_id: int, body: dict) -> dict:
    """Delega en /factory/{wp_id}/review del orquestador."""
    from webhook import factory_review, ReviewRequest
    req = ReviewRequest(**body)
    return await factory_review(wp_id, req)


# --------------------------------------------------------------------------
# Edición humana + cierre de la especificación (la spec es el CONTRATO).
# El humano edita spec.md a mano (cero alucinación) y la cierra; al cerrar se
# valida la estructura, se re-extraen las aseveraciones AS y se aprueba el
# bloque diseno (el workflow snapshotea lo VIGENTE — la versión editada).
# --------------------------------------------------------------------------
_SPEC_VIGENTE = "especificacion/spec.md"
_ASSERTIONS_VIGENTE = "especificacion/assertions.json"


def _parse_as_table(md: str) -> list[dict]:
    """Extrae las aseveraciones AS-xx de la tabla markdown de spec.md.
    Filas: | AS-01 | dado | cuando | entonces | regla | datos |."""
    out: list[dict] = []
    for line in md.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or not _re.match(r"(?i)^AS-?\d+\w*$", cells[0]):
            continue
        g = lambda i: cells[i] if i < len(cells) else ""  # noqa: E731
        out.append({"id": cells[0], "dado": g(1), "cuando": g(2),
                    "entonces": g(3), "regla": g(4), "datos": g(5)})
    return out


def _validate_spec(md: str) -> tuple[list[dict], list[str]]:
    """Validación mínima antes de cerrar: secciones clave + tabla AS bien
    formada. Devuelve (assertions, issues)."""
    issues: list[str] = []
    low = md.lower()
    if "criterios de aceptaci" not in low and "aseveraciones" not in low:
        issues.append("Falta la sección de Criterios de aceptación / Aseveraciones")
    if "## " not in md:
        issues.append("La spec no tiene secciones (encabezados '## ')")
    asserts = _parse_as_table(md)
    if not asserts:
        issues.append("No se encontró ninguna aseveración AS-xx en formato tabla")
    for a in asserts:
        if not a["entonces"].strip():
            issues.append(f"{a['id']}: falta el 'Entonces' (resultado verificable)")
    return asserts, issues


@router.get("/api/wp/{wp_id}/spec/raw")
async def api_wp_spec_raw(wp_id: int, request: Request) -> dict:
    """Devuelve el texto crudo de spec.md vigente para editarlo. 404 si no hay."""
    auth.require_user(request)
    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        try:
            f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                  _SPEC_VIGENTE, ref=branch)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404,
                                detail="no hay spec.md vigente todavía") from exc
    return {"text": f["text"], "sha": f["sha"]}


@router.post("/api/wp/{wp_id}/spec")
async def api_wp_spec_save(wp_id: int, body: dict, request: Request) -> dict:
    """Guarda la spec.md editada a mano (sobreescribe la vigente). Solo
    swf-funcional/admin. NO toca el agente ni el workflow."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin editan la spec")
    content = body.get("content")
    if content is None or not str(content).strip():
        raise HTTPException(status_code=400, detail="content vacío")
    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          _SPEC_VIGENTE, content,
                          f"spec editada a mano por {user.get('username','?')} refs #{wp_id}")
    asserts, issues = _validate_spec(content)
    return {"ok": True, "assertions_count": len(asserts), "issues": issues}


@router.post("/api/wp/{wp_id}/spec/close")
async def api_wp_spec_close(wp_id: int, body: dict, request: Request) -> dict:
    """Cierra (congela) la spec: valida estructura, re-extrae assertions.json
    de la tabla AS, y aprueba el bloque diseno (el workflow snapshotea lo
    vigente — la spec editada). Si hay issues y no se fuerza, NO cierra."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin cierran la spec")
    force = bool(body.get("force"))
    actor = (body.get("actor") or user.get("name") or user.get("username") or "")
    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        try:
            f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                  _SPEC_VIGENTE, ref=branch)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404,
                                detail="no hay spec.md vigente para cerrar") from exc
    asserts, issues = _validate_spec(f["text"])
    if issues and not force:
        return {"ok": False, "issues": issues, "assertions_count": len(asserts)}
    # Re-extraer assertions.json de la tabla editada (contrato sincronizado).
    async with Gitea() as gt:
        await gt.put_file(
            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch, _ASSERTIONS_VIGENTE,
            json.dumps({"wp_id": wp_id, "count": len(asserts),
                        "assertions": asserts}, indent=2, ensure_ascii=False),
            f"assertions re-extraídas de la spec cerrada refs #{wp_id}")
    # Cerrar = aprobar el loop diseno → snapshot del vigente + avanza.
    from webhook import factory_review, ReviewRequest
    await factory_review(wp_id, ReviewRequest(
        action="approve", actor=actor,
        instructions="Spec editada y CERRADA por el revisor (contrato congelado)."))
    return {"ok": True, "assertions_count": len(asserts), "issues": issues}


@router.post("/api/render-md")
async def api_render_md(body: dict, request: Request) -> dict:
    """Renderiza Markdown a HTML con el MISMO renderer del resto del app
    (para el preview en vivo del editor de spec). Mismas extensiones que
    /api/artifact."""
    auth.require_user(request)
    text = body.get("text") or ""
    html = md_lib.markdown(
        text, extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
    return {"html": html}


_REGEN = {
    "test-plan": ("test_plan", "test-plan/test-plan.md", 16384),
    "prototipo": ("prototipo", "prototipo/prototipo.html", 32000),
}


@router.post("/api/wp/{wp_id}/regenerate/{kind}")
async def api_wp_regenerate(wp_id: int, kind: str, request: Request,
                            body: dict | None = None) -> dict:
    """Regenera test-plan o prototipo DESDE la spec congelada (el contrato), sin
    tocar la spec. Llamada one-shot directa al agente (como estimate_plan).

    Body opcional {instructions, focus_points}: AFINA el artefacto vigente con
    comentarios humanos (el agente recibe la versión actual + el feedback y la
    refina, no la rehace). Sin instructions = regeneración limpia desde la spec.
    """
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403, detail="solo swf-funcional/admin")
    spec = _REGEN.get(kind)
    if not spec:
        raise HTTPException(status_code=404, detail=f"tipo desconocido: {kind}")
    stage, out_path, max_tokens = spec
    instructions = ((body or {}).get("instructions") or "").strip()
    focus_points = [p for p in ((body or {}).get("focus_points") or []) if p]
    branch = f"req-{wp_id}"
    current_artifact = ""
    async with Gitea() as gt:
        try:
            spec_md = (await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                         _SPEC_VIGENTE, ref=branch))["text"]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=404,
                detail="no hay spec.md vigente — edita/ciérrala primero") from exc
        assertions_json = ""
        try:
            assertions_json = (await gt.get_file(
                _ARTIFACTS_OWNER, _ARTIFACTS_REPO, _ASSERTIONS_VIGENTE,
                ref=branch))["text"]
        except Exception:  # noqa: BLE001
            pass
        # AFINAR: si hay comentarios, leemos la versión vigente para que el
        # agente la refine (no la rehaga). Sin vigente, cae a regeneración limpia.
        if instructions:
            try:
                current_artifact = (await gt.get_file(
                    _ARTIFACTS_OWNER, _ARTIFACTS_REPO, out_path,
                    ref=branch))["text"]
            except Exception:  # noqa: BLE001
                current_artifact = ""
    subject = f"requisito-{wp_id}"
    try:
        import proyeccion
        subject = (await proyeccion.get_requisito_texto(wp_id) or {}).get("titulo") or subject
    except Exception:  # noqa: BLE001
        pass
    from agent import run_agent
    from activities import load_architecture, load_design_skill
    arch = await load_architecture()
    ds = await load_design_skill(wp_id)
    if ds:
        arch = {**arch, "design_skill": ds}
    inputs = {"subject": subject, "description": "",
              "spec_md": spec_md, "assertions_json": assertions_json}
    if current_artifact:
        inputs["current_artifact"] = current_artifact
    iterations = []
    if instructions:
        iterations = [{
            "n": 1, "model": "", "mode": "draft",
            "summary": "(versión vigente — afinar con el feedback, no rehacer)",
            "feedback": {"instructions": instructions,
                         "focus_points": focus_points},
        }]
    result = await run_agent(
        stage, {"work_package_id": wp_id, "subject": subject},
        inputs, arch, iterations=iterations, max_tokens=max_tokens)
    if result.get("mode") == "blocked":
        return {"ok": False, "blocked": True,
                "open_questions": result.get("open_questions", [])}
    artifact = (result.get("artifact") or "").strip()
    if not artifact:
        raise HTTPException(status_code=502,
                            detail="el agente no devolvió contenido")
    # El detalle detecta "hay vigente" por la PRESENCIA de {root}/meta.json
    # (ver _read_block_state). Sin el meta, el prototipo/test-plan se escribe
    # pero la UI lo muestra como "aún no generado". Por eso escribimos AMBOS.
    root = out_path.split("/")[0]
    meta = {
        "version_actual": "v01",
        "status": "draft",
        "cr_actual": None,
        "iter_actual": 1,
        "ultimo_modelo": result.get("model"),
        "ultimo_summary": result.get("summary", ""),
        "ultimo_actor": user.get("username") or user.get("name") or "",
        "stage": stage,
        "regenerado_desde_spec": True,
    }
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch, out_path,
                          artifact,
                          f"regenerar {kind} desde spec congelada refs #{wp_id}")
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          f"{root}/meta.json",
                          json.dumps(meta, indent=2, ensure_ascii=False),
                          f"meta de {kind} regenerado refs #{wp_id}")
        # Historial de afinamiento: si vino feedback, lo registramos (traza de
        # qué se pidió y qué respondió el agente). Append al historial del bloque.
        if instructions:
            from datetime import datetime, timezone
            actor = user.get("name") or user.get("username") or "?"
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            foco = ("\n  - Foco: " + "; ".join(focus_points)) if focus_points else ""
            entry = (f"\n## {ts} — afinado por {actor} "
                     f"(modelo {result.get('model') or '?'})\n"
                     f"**Comentarios:** {instructions}{foco}\n\n"
                     f"**Resumen del agente:** {result.get('summary', '')}\n")
            hist_path = f"{root}/historial-aprendizaje.md"
            prev = ""
            try:
                prev = (await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                          hist_path, ref=branch))["text"]
            except Exception:  # noqa: BLE001
                prev = f"# Historial de afinamiento — {kind}\n"
            await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                              hist_path, prev + entry,
                              f"afinar {kind} con feedback refs #{wp_id}")
    return {"ok": True, "summary": result.get("summary", ""),
            "model": result.get("model"), "afinado": bool(instructions)}


@router.post("/api/wp/{wp_id}/reset")
async def api_wp_reset(wp_id: int, request: Request) -> dict:
    """Reinicia el requisito a la fase inicial (Recibido) SIN borrarlo: termina
    el workflow de Temporal (descarta el progreso en curso) y, si el status de
    OP quedó en pruebas/cerrado, lo regresa al inicial para que la etapa vuelva
    a Recibido. Los artefactos vigentes se dejan (el re-run los sobreescribe);
    el WP, su historial y las versiones aprobadas quedan en Gitea."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin reinician requisitos")
    wid = f"requisito-{wp_id}"
    terminated = False
    if _temporal is not None:
        try:
            await _temporal.get_workflow_handle(wid).terminate(
                reason=f"Reinicio por {user.get('username', '?')}")
            terminated = True
        except RPCError:
            pass  # no existe / ya cerrado
        except Exception:  # noqa: BLE001  (ya completado, etc.)
            pass
    import proyeccion
    # La etapa vive en proyecto-db. Reiniciar = volver a 'recepcion', liberar la
    # pelota y marcar workflow_iniciado=false (re-validable con ▶ Iniciar).
    await proyeccion.reset_requisito(wp_id)
    try:
        await proyeccion.agregar_comentario(
            wp_id, user.get("username") or "?",
            user.get("name") or user.get("username") or "?",
            "🔄 Requisito **reiniciado** — vuelve a *Recepción*. Edita la "
            "descripción/adjuntos si quieres y dale **▶ Iniciar** para regenerar "
            "el paquete desde cero. (El historial y las versiones previas quedan "
            "en Gitea; el nuevo run sobreescribe la spec/prototipo/test vigentes.)",
            accion="responder", etapa="recepcion")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "terminated": terminated}


@router.post("/api/wp/{wp_id}/discard-duplicate")
async def api_wp_discard_duplicate(wp_id: int, request: Request,
                                   body: dict | None = None) -> dict:
    """Descarta un requisito DUPLICADO en un solo paso: termina el workflow de
    Temporal (si corre), lo marca como duplicado del original (relación
    'duplicates' en OP, opcional) y lo cierra con un estatus terminal
    (STATUS_REJECTED) para que salga del inbox activo. NO borra el WP en OP — la
    trazabilidad del descarte queda en el historial. Solo swf-funcional/admin.

    Body opcional: {duplicate_of: <wp_id original>} para enlazar la relación.
    """
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin descartan duplicados")
    dup_of = (body or {}).get("duplicate_of")
    try:
        dup_of = int(dup_of) if dup_of not in (None, "") else None
    except (TypeError, ValueError):
        dup_of = None
    if dup_of == wp_id:
        raise HTTPException(status_code=400,
                            detail="un requisito no puede ser duplicado de sí mismo")

    wid = f"requisito-{wp_id}"
    terminated = False
    if _temporal is not None:
        try:
            await _temporal.get_workflow_handle(wid).terminate(
                reason=f"Descartado (duplicado) por {user.get('username', '?')}")
            terminated = True
        except RPCError:
            pass  # no existe / ya cerrado
        except Exception:  # noqa: BLE001
            pass

    import proyeccion
    related = False
    # Relación con el original (best-effort). El catálogo solo admite
    # 'cr'/'relacionado'; el descarte como duplicado usa 'relacionado'.
    if dup_of:
        try:
            related = await proyeccion.crear_relacion(wp_id, dup_of, "relacionado")
        except Exception:  # noqa: BLE001
            pass
    # Cerrar el requisito (etapa 'cerrado') para que salga del inbox activo.
    status_set = "cerrado" if await proyeccion.cerrar_requisito(wp_id) else None
    ref = f" de #{dup_of}" if dup_of else ""
    try:
        await proyeccion.agregar_comentario(
            wp_id, user.get("username") or "?",
            user.get("name") or user.get("username") or "?",
            f"🗑️ Requisito **descartado como duplicado{ref}**. "
            "El workflow en curso se terminó y el requisito se cerró. "
            "(No se borró; la spec/adjuntos/repo previos quedan para "
            "trazabilidad. Si quedó un repo en Gitea, bórralo a mano.)",
            accion="responder", etapa="cerrado")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "terminated": terminated, "related": related,
            "status_set": status_set}


# Desestimar = descartar un requisito a un estado TERMINAL 'desestimado' (sale
# del flujo, el inbox lo bloquea). Solo admin / líder de proyecto. Reversible por
# un admin con /reactivar (vuelve a Recepción). Ver [[workflow-dirigido-por-blog]].
_DESESTIMAR_ROLES = {"admin", "project-manager", "swf-project-manager"}


@router.post("/api/wp/{wp_id}/desestimar")
async def api_wp_desestimar(wp_id: int, request: Request,
                            body: dict | None = None) -> dict:
    """Desestima un requisito: termina el workflow (si corre), lo deja en etapa
    terminal 'desestimado' y registra el motivo en el blog. Solo admin / líder de
    proyecto. No borra el WP; un admin puede reactivarlo con /reactivar."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _DESESTIMAR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo admin / líder de proyecto desestiman")
    motivo = ((body or {}).get("motivo") or "").strip()
    wid = f"requisito-{wp_id}"
    terminated = False
    if _temporal is not None:
        try:
            await _temporal.get_workflow_handle(wid).terminate(
                reason=f"Desestimado por {user.get('username', '?')}")
            terminated = True
        except RPCError:
            pass  # no existe / ya cerrado
        except Exception:  # noqa: BLE001
            pass
    import proyeccion
    ok = await proyeccion.desestimar_requisito(wp_id)
    try:
        await proyeccion.agregar_comentario(
            wp_id, user.get("username") or "?",
            user.get("name") or user.get("username") or "?",
            "🚫 Requisito **desestimado**"
            + (f": {motivo}" if motivo else ".")
            + " Sale del flujo y queda bloqueado en el inbox. "
            "(No se borró; un admin puede reactivarlo.)",
            accion="responder", etapa="desestimado")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": ok, "terminated": terminated}


@router.post("/api/wp/{wp_id}/reactivar")
async def api_wp_reactivar(wp_id: int, request: Request) -> dict:
    """Reactiva un requisito desestimado: vuelve a 'recepcion' (re-validable con
    ▶ Iniciar). Solo admin."""
    user = auth.require_user(request)
    if "admin" not in set(user.get("roles") or []):
        raise HTTPException(status_code=403,
                            detail="solo un admin reactiva un requisito desestimado")
    import proyeccion
    ok = await proyeccion.reset_requisito(wp_id)
    try:
        await proyeccion.agregar_comentario(
            wp_id, user.get("username") or "?",
            user.get("name") or user.get("username") or "?",
            "♻️ Requisito **reactivado** por un admin — vuelve a *Recepción*. "
            "Edita lo que haga falta y dale **▶ Iniciar**.",
            accion="responder", etapa="recepcion")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": ok}


@router.post("/api/wp/{wp_id}/decision")
async def api_wp_decision(wp_id: int, body: dict) -> dict:
    # La compuerta de estimación trae la fecha inicial MANUAL: la persistimos
    # en el plan ANTES de señalar, para que el bloque set_dates la lea al
    # volcar las fechas a OP tras los VoBos. Ver [[op-simplificado-detalle-temporal]].
    start_date = str(body.pop("start_date", "") or "").strip()
    if start_date:
        await _set_plan_start_date(wp_id, start_date)
    from webhook import factory_decision, DecisionRequest
    req = DecisionRequest(**body)
    return await factory_decision(wp_id, req)


@router.post("/api/wp/{wp_id}/generar-borrador")
async def api_wp_generar_borrador(wp_id: int, request: Request,
                                  body: dict | None = None) -> dict:
    """Ayudante OPCIONAL del rediseño: pide al agente un borrador one-shot de la
    spec mientras el requisito está en la compuerta 'Generando paquete'. Señala
    el workflow; el borrador se POSTEA AL BLOG (no toca el repo, para no
    desincronizar el clon del dev) y el humano lo copia a `diseno/spec.md` en su
    clon y lo refina con Claude Code. NO avanza la etapa. Permiso:
    swf-funcional/admin. Ver [[rediseno-fase-diseno-mcp]]."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf-funcional/admin generan borradores")
    model = str((body or {}).get("model", "") or "").strip()
    if _temporal is None:
        raise HTTPException(status_code=503, detail="Temporal no disponible")
    from workflow import FactoryWorkflow
    wid = f"requisito-{wp_id}"
    try:
        handle = _temporal.get_workflow_handle(wid)
        await handle.signal(FactoryWorkflow.generar_borrador, model)
    except RPCError as exc:
        raise HTTPException(
            status_code=409,
            detail="El requisito no está en una etapa que acepte borrador") from exc
    return {"ok": True, "signaled": wid, "model": model or "default"}


async def _set_plan_start_date(wp_id: int, start_date: str) -> None:
    """Fija/mezcla start_date en estimacion/estimation.json (rama del WP)."""
    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        plan: dict = {}
        try:
            f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                   _est_current_json_path(), ref=branch)
            plan = json.loads(f["text"])
        except Exception:  # noqa: BLE001
            plan = {}
        plan["start_date"] = start_date
        await gt.put_file(
            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
            _est_current_json_path(),
            json.dumps(plan, indent=2, ensure_ascii=False),
            f"fecha inicial manual {start_date} refs #{wp_id}")


def _due_from(start_iso: str, calendar_days: float) -> str | None:
    """Fecha final = inicial + N días LABORABLES (excluye sáb/dom)."""
    from datetime import date, timedelta
    try:
        start = date.fromisoformat(start_iso)
    except (ValueError, TypeError):
        return None
    n = max(1, int(round(float(calendar_days or 0)))) if calendar_days else 1
    due = start
    added = 0
    while added < n:
        due += timedelta(days=1)
        if due.weekday() < 5:
            added += 1
    return due.isoformat()


async def _sync_plan_to_board(wp_id: int) -> dict:
    """Empuja el plan VIGENTE al board (proyecto-db): inicio (del plan o el que ya
    está en el board), fin (= inicio + días laborables del estimado) y horas. Se
    llama cada vez que el plan CAMBIA → el board queda sincronizado sin botón."""
    branch = f"req-{wp_id}"
    plan: dict = {}
    async with Gitea() as gt:
        try:
            f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                  _est_current_json_path(), ref=branch)
            plan = json.loads(f["text"])
        except Exception:  # noqa: BLE001
            plan = {}
    import proyeccion
    actual = await proyeccion.get_plan(wp_id) or {}
    ini_board = actual.get("inicio_plan")
    start = (str(plan.get("start_date") or "").strip()
             or (ini_board.isoformat() if ini_board else ""))
    cal = plan.get("calendar_days_realistic") or 0
    due = _due_from(start, cal) if (start and cal) else None
    hours = plan.get("total_horas_con_contingencia")
    ok = await proyeccion.guardar_plan(wp_id, start or None, due, hours)
    return {"ok": ok, "start_date": start or None, "due_date": due,
            "horas": hours}


@router.post("/api/wp/{wp_id}/op-sync")
async def api_wp_op_sync(wp_id: int, request: Request) -> dict:
    """Sincroniza el plan vigente con el BOARD (proyecto-db): fecha inicial
    (manual), fecha final (= inicial + días laborables del estimado) y horas. OP
    quedó fuera; el board es ahora la única fuente. Si el body trae start_date,
    lo persiste primero en estimation.json. (El board ya se sincroniza solo en
    cada cambio del plan; este endpoint lo fuerza on-demand.)"""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403, detail="solo swf-funcional/admin")
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sd_in = str(body.get("start_date") or "").strip()
    if sd_in:
        await _set_plan_start_date(wp_id, sd_in)
    res = await _sync_plan_to_board(wp_id)
    if not res.get("start_date"):
        raise HTTPException(
            status_code=400,
            detail="falta la fecha inicial — fíjala antes de sincronizar")
    return {"ok": res.get("ok"), "start_date": res.get("start_date"),
            "due_date": res.get("due_date"), "estimated_hours": res.get("horas")}


@router.post("/api/wp/{wp_id}/guardar-plan")
async def api_wp_guardar_plan(wp_id: int, request: Request, body: dict) -> dict:
    """Guarda el PLAN en el board (proyecto-db): fecha de inicio (manual) + fecha
    fin (= inicio + días laborables del estimado). Reemplaza el viejo sync a OP.
    El estimado por días sale del plan vigente (estimacion.json)."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & {"swf-funcional", "admin", "project-manager",
                     "swf-project-manager"}):
        raise HTTPException(status_code=403,
                            detail="solo SwF Funcional / admin / líder de proyecto")
    start_iso = str(body.get("start_date") or "").strip()
    if not start_iso:
        raise HTTPException(status_code=400, detail="fecha de inicio requerida")
    # Persistir la fecha en el plan (Gitea) y sincronizar el board (inicio + fin
    # + horas). Mismo camino que cualquier cambio de plan.
    try:
        await _set_plan_start_date(wp_id, start_iso)
    except Exception:  # noqa: BLE001
        pass
    res = await _sync_plan_to_board(wp_id)
    if not res.get("ok"):
        raise HTTPException(
            status_code=400,
            detail="no se pudo guardar (¿el requisito está en el board?)")
    return {"ok": True, "start_date": res.get("start_date"),
            "due_date": res.get("due_date")}


# --------------------------------------------------------------------------
# Iniciar nuevo requerimiento desde el inbox.
# --------------------------------------------------------------------------
from fastapi import UploadFile, File, Form


@router.get("/api/projects")
async def api_projects(request: Request) -> dict:
    """Lista proyectos (proyecto-db) para el selector de alta de requerimiento."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & {"swf-funcional", "admin", "funcional", "usuario-clave"}):
        raise HTTPException(status_code=403, detail="rol no autorizado")
    import proyeccion
    is_admin = "admin" in roles
    proys = await proyeccion.mis_proyectos(
        "" if is_admin else (user.get("username") or ""), is_admin)
    return {"items": [{"id": p["id"],
                       "name": f"{p['cliente']} · {p['nombre']}",
                       "parent_id": None} for p in proys]}


@router.get("/api/projects/{project_id}/capabilities")
async def api_project_capabilities(project_id: int, request: Request) -> dict:
    """Capacidades del proyecto (proyecto-db) — el nuevo Requisito cuelga de una."""
    auth.require_user(request)
    import proyeccion
    caps = await proyeccion.listar_capacidades(int(project_id))
    return {"items": [{"id": c["id"], "subject": c["nombre"]} for c in caps]}


@router.post("/api/inbox/upload-url")
async def api_inbox_upload_url(request: Request, body: dict) -> dict:
    """Devuelve presigned PUT URL para que el browser suba un archivo
    DIRECTO a MinIO/S3 sin pasar por backend. El browser arma el `batch_id`
    (UUIDv4) y manda cada upload con `{filename, content_type}`; recibe
    `{put_url, key}`. Después llama /new-requirement con la lista de keys."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & {"swf-funcional", "admin", "funcional", "usuario-clave"}):
        raise HTTPException(status_code=403, detail="rol no autorizado")
    batch_id = (body.get("batch_id") or "").strip()
    filename = (body.get("filename") or "").strip()
    content_type = body.get("content_type") or "application/octet-stream"
    if not batch_id or not filename:
        raise HTTPException(status_code=400, detail="batch_id y filename requeridos")
    # Sanitizar filename para que sea seguro como key
    safe_name = filename.replace("/", "_").replace("\\", "_").replace("..", "_")
    key = f"staged/{batch_id}/{safe_name}"
    put_url = S3Storage.presigned_put_url(key, content_type=content_type)
    return {"put_url": put_url, "key": key, "filename": filename,
            "content_type": content_type, "method": "PUT",
            "ttl_seconds": config.S3_PRESIGNED_TTL}


@router.post("/api/inbox/new-requirement")
async def api_new_requirement(request: Request, body: dict) -> dict:
    """Crea Requisito + Capability si nueva. Arranca workflow SOLO si el
    creador es swf-funcional/admin; los roles del lado cliente (funcional,
    usuario-clave) crean el WP en 'Pendiente validación' y un swf-funcional
    debe aprobarlo via /api/wp/{id}/approve-and-start (D1).

    El body es JSON (no multipart): los archivos ya están en MinIO.
    Body:
      {project_id, subject, description, capability_id|new_capability_name,
       files: [{filename, key, size, content_type}]}
    """
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    allowed = {"swf-funcional", "admin", "funcional", "usuario-clave"}
    if not (roles & allowed):
        raise HTTPException(
            status_code=403,
            detail="solo swf-funcional, admin, funcional o usuario-clave")
    # Nadie auto-arranca: TODOS los roles crean el requisito en "Recepción"
    # para poder adjuntar documentos y luego disparar con el botón explícito
    # "Validar y enviar al agente" (approve-and-start / ▶ Iniciar).
    auto_start = False
    project_id = body.get("project_id")
    subject = (body.get("subject") or "").strip()
    description = body.get("description") or ""
    capability_id = body.get("capability_id")
    new_capability_name = (body.get("new_capability_name") or "").strip()
    files = body.get("files") or []
    related_to = body.get("related_to")   # opcional: # de requisito a asociar
    if not project_id or not subject:
        raise HTTPException(status_code=400, detail="project_id y subject requeridos")

    import proyeccion
    created_capability = None
    # 1) Capacidad padre (proyecto-db)
    parent_id = capability_id
    if not parent_id:
        if not new_capability_name:
            raise HTTPException(
                status_code=400,
                detail="elige una capacidad existente o nombra una nueva")
        parent_id = await proyeccion.crear_capacidad(int(project_id), new_capability_name)
        if parent_id is None:
            raise HTTPException(status_code=500, detail="no se pudo crear la capacidad")
        created_capability = {"id": parent_id, "subject": new_capability_name}

    # 2) Requisito (proyecto-db) — wp_id propio de la secuencia, etapa='recepcion'
    req = await proyeccion.crear_requisito(
        int(project_id), int(parent_id), subject, description)
    if not req:
        raise HTTPException(status_code=500, detail="no se pudo crear el requisito")
    wp_id = req["wp_id"]

    # 2b) Relación opcional con otro requisito.
    if related_to:
        try:
            rid = int(related_to)
            if rid != wp_id:
                await proyeccion.crear_relacion(wp_id, rid, "relacionado")
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo crear relación al crear req %s: %s", wp_id, exc)

    # 4) Persistir manifest de adjuntos en Gitea para que el worker lo lea fácil
    if files:
        try:
            branch = f"req-{wp_id}"
            _up_ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
            manifest = {
                "wp_id": wp_id,
                "files": [{"filename": f.get("filename"),
                           "key": f.get("key"),
                           "size": f.get("size"),
                           "tipo": f.get("tipo"),
                           "content_type": f.get("content_type"),
                           "uploaded_at": _up_ts} for f in files],
                "created_by": user.get("username"),
                "created_at": _up_ts,
            }
            async with Gitea() as gt:
                await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
                await gt.put_file(
                    _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                    "inputs/manifest.json",
                    json.dumps(manifest, indent=2, ensure_ascii=False),
                    f"inputs manifest refs #{wp_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning("manifest a Gitea falló: %s", exc)

    # 5) Comentario de creación al blog (proyecto-db). El requisito nace en
    # 'recepcion' con la pelota en swf-funcional y workflow_iniciado=false; se
    # dispara con el botón explícito ▶ Iniciar (approve-and-start).
    rol_creador = _primary_role(list(roles))
    creator_un = user.get("username") or "?"
    creator_name = user.get("name") or creator_un
    try:
        await proyeccion.agregar_comentario(
            wp_id, creator_un, creator_name,
            f"📨 Requisito creado por **{creator_name}** (rol: `{rol_creador}`).\n\n"
            "**Pendiente de validación** antes de procesar con el agente IA. "
            "Se arranca desde el inbox con el botón **▶ Iniciar**.",
            accion="responder", etapa="recepcion")
    except Exception as exc:  # noqa: BLE001
        log.warning("comentario de creación falló para req %s: %s", wp_id, exc)
    return {"ok": True, "wp_id": wp_id, "capability_id": parent_id,
            "created_capability": created_capability,
            "files_count": len(files), "started": None,
            "needs_validation": True}


# --------------------------------------------------------------------------
# Plan / estimación — preview con IA, persistencia en Gitea (estimacion.json
# vigente + iteraciones/v{n}/), edición humana. Crear los WPs en OP es
# endpoint aparte (MITAD B).
# --------------------------------------------------------------------------

def _est_current_json_path() -> str:
    return "estimacion/estimation.json"

def _est_current_md_path() -> str:
    return "estimacion/estimation.md"

def _est_iter_folder(n: int) -> str:
    return f"estimacion/iteraciones/v{n:02d}"


def _format_estimation_md(plan: dict, wp_id: int, version: int) -> str:
    """Convierte el plan JSON en un MD legible para Gitea."""
    devs = plan.get("developments") or []
    wps = plan.get("work_packages") or []
    asn = plan.get("assumptions") or []
    stack = ", ".join(plan.get("tech_stack") or [])
    lines = [
        f"# Estimación del Requisito #{wp_id}",
        "",
        f"**Versión:** v{version:02d} · **Modelo:** {plan.get('model','—')}"
        f"{' · ✏️ editada por humano' if plan.get('edited_by') else ''}",
        "",
        "## Resumen",
        "",
        f"- **Complejidad:** {plan.get('complexity','?')}",
        f"- **Total con contingencia ({plan.get('contingencia_pct', 15)}%):** "
        f"{(plan.get('total_horas_con_contingencia') or 0):.0f} h "
        f"({(plan.get('calendar_days_realistic') or 0):.1f} días laborables)",
        f"- **Total sin contingencia:** {(plan.get('total_horas_sin_contingencia') or 0):.0f} h",
        f"- **Stack:** {stack or '—'}",
        "",
        f"> {plan.get('summary','')}",
        "",
        "## Desarrollos (clasificación T/S/M/L/VL)",
        "",
        "| # | Desarrollo | Back | Front | N integ. | h back | h front | h pruebas | h integ. | Total |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, dv in enumerate(devs, 1):
        lines.append(
            f"| {i} | {dv.get('name','')} | {dv.get('talla_back','—')} | "
            f"{dv.get('talla_front','—')} | {dv.get('n_integraciones',0)} | "
            f"{(dv.get('horas_back') or 0):.0f} | {(dv.get('horas_front') or 0):.0f} | "
            f"{(dv.get('horas_pruebas') or 0):.0f} | {(dv.get('horas_integraciones') or 0):.0f} | "
            f"**{(dv.get('total') or 0):.0f}** |"
        )
    lines += [
        "",
        "## Work Packages propuestos (desglose)",
        "",
        "| # | Tipo | Subject | Horas |",
        "|---|---|---|---:|",
    ]
    for i, wp in enumerate(wps, 1):
        lines.append(
            f"| {i} | {wp.get('type_name','')} | {wp.get('subject','')} | "
            f"**{(wp.get('hours') or 0):.0f}** |"
        )
    if asn:
        lines += ["", "## Asunciones", ""]
        for a in asn:
            lines.append(f"- {a}")
    return "\n".join(lines) + "\n"


async def _list_estimation_iterations(branch: str) -> list[str]:
    async with Gitea() as gt:
        try:
            r = await gt._c.get(
                f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/"
                f"estimacion/iteraciones",
                params={"ref": branch})
            if r.status_code == 200:
                return sorted([i["name"] for i in r.json() if i["type"] == "dir"])
        except Exception:  # noqa: BLE001
            pass
    return []


@router.get("/api/wp/{wp_id}/plan")
async def api_wp_plan_get(wp_id: int, request: Request) -> dict:
    """Devuelve el plan vigente persistido en Gitea. Si no existe, 404."""
    auth.require_user(request)
    branch = f"req-{wp_id}"
    iterations = await _list_estimation_iterations(branch)
    async with Gitea() as gt:
        try:
            f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                  _est_current_json_path(), ref=branch)
            plan = json.loads(f["text"])
        except Exception:  # noqa: BLE001
            return {"status": "no_plan", "iterations": iterations}
    return {"status": "ok", "plan": plan, "iterations": iterations}


@router.post("/api/wp/{wp_id}/plan")
async def api_wp_plan_save(wp_id: int, body: dict, request: Request) -> dict:
    """Persiste un plan. Crea v{n+1} en iteraciones/ y actualiza vigente.
    El body es el plan completo (puede ser el generado por IA o el editado
    por el revisor). Marca `edited_by` si difiere del original_model.
    """
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="solo swf/admin pueden guardar plan")
    plan = dict(body)
    plan["saved_at"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    plan["saved_by"] = user.get("username") or "anónimo"

    branch = f"req-{wp_id}"
    iterations = await _list_estimation_iterations(branch)
    next_n = len(iterations) + 1
    plan["version"] = next_n

    json_str = json.dumps(plan, indent=2, ensure_ascii=False)
    md_str = _format_estimation_md(plan, wp_id, next_n)

    refs = f"refs #{wp_id}"
    iter_folder = _est_iter_folder(next_n)
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        # Iteración inmutable
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          f"{iter_folder}/estimation.json", json_str,
                          f"Estimación v{next_n:02d} JSON {refs}")
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          f"{iter_folder}/estimation.md", md_str,
                          f"Estimación v{next_n:02d} MD {refs}")
        # Vigente
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          _est_current_json_path(), json_str,
                          f"Estimación vigente v{next_n:02d} {refs}")
        await gt.put_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
                          _est_current_md_path(), md_str,
                          f"Estimación vigente MD v{next_n:02d} {refs}")
    # Sincroniza el board: el estimado cambió → fin y horas se actualizan
    # (conservando la fecha de inicio ya fijada).
    try:
        await _sync_plan_to_board(wp_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("sync plan→board falló (no fatal): %s", exc)
    return {"status": "saved", "version": next_n, "plan": plan}


@router.delete("/api/wp/{wp_id}/plan")
async def api_wp_plan_delete(wp_id: int, request: Request) -> dict:
    """Borra solo el plan VIGENTE (las iteraciones quedan como historial)."""
    user = auth.require_user(request)
    roles = set(user.get("roles") or [])
    if not (roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403, detail="solo swf/admin")
    branch = f"req-{wp_id}"
    async with Gitea() as gt:
        for path in (_est_current_json_path(), _est_current_md_path()):
            try:
                f = await gt._c.get(
                    f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/{path}",
                    params={"ref": branch})
                if f.status_code != 200:
                    continue
                sha = f.json()["sha"]
                await gt._c.request(
                    "DELETE",
                    f"/repos/{_ARTIFACTS_OWNER}/{_ARTIFACTS_REPO}/contents/{path}",
                    json={"sha": sha, "branch": branch,
                          "message": f"Borrar plan vigente refs #{wp_id}"})
            except Exception:  # noqa: BLE001
                pass
    return {"status": "deleted"}


@router.post("/api/wp/{wp_id}/estimate")
async def api_wp_estimate(wp_id: int, request: Request) -> dict:
    """Llama al agente estimador con la spec/prototipo vigentes + plantilla
    de descomposicion. Devuelve un plan JSON (WPs propuestos con horas y
    dependencias) que el revisor SwF puede ajustar antes de crearlos en
    OpenProject (que es un endpoint distinto)."""
    user = auth.require_user(request)
    # Solo swf-funcional/admin pueden estimar (no funcional/usuario-clave).
    user_roles = set(user.get("roles") or [])
    if not (user_roles & _EDITOR_ROLES):
        raise HTTPException(status_code=403,
                            detail="estimacion requiere rol swf o admin")

    branch = f"req-{wp_id}"
    # Cargar artefactos vigentes + arquitectura + plantilla
    import proyeccion
    subject = (await proyeccion.get_requisito_texto(wp_id) or {}).get("titulo") or f"WP {wp_id}"

    async with Gitea() as gt:
        # Spec aprobada (vigente)
        try:
            spec_f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                       "especificacion/spec.md", ref=branch)
            spec_md = spec_f["text"]
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail="no encontre especificacion/spec.md en la branch — "
                       "aprueba primero el diseño")
        # Prototipo aprobado
        try:
            proto_f = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                        "prototipo/prototipo.html", ref=branch)
            prototipo_html = proto_f["text"]
        except Exception:  # noqa: BLE001
            prototipo_html = "(prototipo no disponible)"
        # Arquitectura (del repo de configuracion de la fabrica)
        try:
            arch_owner, _, arch_repo = config.FACTORY_ARCH_REPO.partition("/")
            arch_f = await gt.get_file(arch_owner, arch_repo,
                                       config.FACTORY_ARCH_PATH,
                                       ref=config.FACTORY_ARCH_BRANCH)
            architecture_md = arch_f["text"]
        except Exception:  # noqa: BLE001
            architecture_md = ""
        # Plantilla de descomposicion (mismo repo que la arquitectura)
        try:
            tpl_f = await gt.get_file(arch_owner, arch_repo,
                                      "wp_breakdown_template.yaml",
                                      ref=config.FACTORY_ARCH_BRANCH)
            template_yaml = tpl_f["text"]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=f"plantilla wp_breakdown_template.yaml no disponible: {exc}")
        # Metodologia de estimacion (tallas T/S/M/L/VL + tabla horas + 15% contingencia)
        try:
            met_f = await gt.get_file(arch_owner, arch_repo,
                                      "estimacion_metodologia.md",
                                      ref=config.FACTORY_ARCH_BRANCH)
            metodologia_md = met_f["text"]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=f"estimacion_metodologia.md no disponible: {exc}")

    from agent import estimate_plan
    ctx = {"work_package_id": wp_id, "subject": subject}
    try:
        plan = await estimate_plan(
            spec_md=spec_md, prototipo_html=prototipo_html,
            architecture_md=architecture_md, template_yaml=template_yaml,
            metodologia_md=metodologia_md, ctx=ctx)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"agente estimador fallo: {exc}") from exc

    return plan


# --------------------------------------------------------------------------
# Annotations (comentarios contextuales sobre el VIGENTE del bloque 'diseno'
# antes de mandar feedback al agente). Bajo el modelo nuevo de versionado
# ([[versionado-artefactos]]) las anotaciones son SIEMPRE pendientes sobre
# el vigente (no por iteración) — al iterar/aprobar se consumen y archivan.
# El parámetro `iteration` de las URLs queda por compat pero se ignora.
# --------------------------------------------------------------------------
import time as _time
import uuid as _uuid


def _annotations_path(iteration: str = "") -> str:
    """Path canónico de anotaciones pendientes del bloque diseño. El arg
    `iteration` se acepta por compat con URLs viejas pero se ignora — solo
    hay un set de pendientes a la vez (sobre el vigente)."""
    _ = iteration  # ignorado, compat
    return "especificacion/anotaciones-pendientes.json"


def _primary_role(roles: list[str]) -> str:
    """Para clasificar anotaciones, elegimos UN rol primario por prioridad.
    funcional/usuario-clave dominan sobre swf-funcional (el feedback de
    negocio pesa más para el agente). admin no es un 'rol de revisor'.

    Orden de prioridad: usuario-clave > funcional > swf-funcional >
    swf-desarrollador > swf-tester."""
    for r in ("usuario-clave", "funcional", "swf-funcional",
              "swf-desarrollador", "swf-tester"):
        if r in roles:
            return r
    return roles[0] if roles else "swf-funcional"


async def _load_annotations(branch: str, iteration: str) -> list:
    async with Gitea() as gt:
        try:
            f = await gt.get_file(
                _ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                _annotations_path(iteration), ref=branch)
            data = json.loads(f["text"])
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []


async def _save_annotations(branch: str, iteration: str, ann_list: list) -> None:
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        await gt.put_file(
            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
            _annotations_path(iteration),
            json.dumps(ann_list, indent=2, ensure_ascii=False),
            f"anotaciones pendientes: {len(ann_list)} items")


@router.get("/api/wp/{wp_id}/diseno/{iteration}/annotations")
async def api_list_annotations(wp_id: int, iteration: str) -> dict:
    """[Compat] Lista anotaciones pendientes (el arg iteration se ignora)."""
    branch = f"req-{wp_id}"
    items = await _load_annotations(branch, iteration)
    return {"items": items, "count": len(items)}


@router.get("/api/wp/{wp_id}/diseno/annotations")
async def api_list_annotations_v2(wp_id: int) -> dict:
    """Lista anotaciones pendientes del bloque diseño (modelo nuevo)."""
    branch = f"req-{wp_id}"
    items = await _load_annotations(branch, "")
    return {"items": items, "count": len(items)}


@router.post("/api/wp/{wp_id}/diseno/{iteration}/annotation")
async def api_add_annotation(wp_id: int, iteration: str, body: dict,
                              request: Request) -> dict:
    """Agrega una anotación. El actor y el rol vienen de la sesión OIDC
    (Keycloak); el body solo trae target/selected_text/comment.
    Body:
        {target: "spec"|"prototipo"|"global",
         selected_text: str | "",
         comment: str}
    """
    user = auth.require_user(request)
    branch = f"req-{wp_id}"
    items = await _load_annotations(branch, iteration)
    # Derivar el rol primario del usuario (prioridad: usuario-clave > funcional > swf-*
    # > admin) para que las anotaciones se clasifiquen consistentemente.
    role = _primary_role(user.get("roles") or [])
    ann = {
        "id": _uuid.uuid4().hex[:12],
        "target": body.get("target") or "global",
        "selected_text": (body.get("selected_text") or "")[:2000],
        "comment": (body.get("comment") or "").strip(),
        "actor": user.get("username") or user.get("name") or "anónimo",
        "actor_email": user.get("email"),
        "role": role,
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    if not ann["comment"]:
        raise HTTPException(status_code=400, detail="comment es obligatorio")
    items.append(ann)
    await _save_annotations(branch, iteration, items)
    return {"ok": True, "annotation": ann, "count": len(items)}


@router.delete("/api/wp/{wp_id}/diseno/{iteration}/annotation/{ann_id}")
async def api_del_annotation(wp_id: int, iteration: str, ann_id: str) -> dict:
    branch = f"req-{wp_id}"
    items = await _load_annotations(branch, iteration)
    new_items = [a for a in items if a.get("id") != ann_id]
    if len(new_items) == len(items):
        raise HTTPException(status_code=404, detail="annotation no encontrada")
    await _save_annotations(branch, iteration, new_items)
    return {"ok": True, "count": len(new_items)}


@router.post("/api/wp/{wp_id}/diseno/{iteration}/synthesize")
async def api_synthesize_feedback(wp_id: int, iteration: str) -> dict:
    """Toma todas las anotaciones de la iteración y llama a Sonnet para
    sintetizarlas en (instructions, focus_points) listos para enviar al
    agente de diseño como action=iterate. NO ejecuta el iterate — devuelve
    el prompt para que el revisor lo edite/confirme primero."""
    branch = f"req-{wp_id}"
    items = await _load_annotations(branch, iteration)
    if not items:
        raise HTTPException(status_code=400,
                            detail="no hay anotaciones para sintetizar")
    if not config.ANTHROPIC_API_KEY:
        # Fallback: concatenación simple.
        instr = "\n\n".join(
            f"[{a['role']}/{a['actor']}] sobre «{a['selected_text'][:80]}»: {a['comment']}"
            if a['selected_text'] else
            f"[{a['role']}/{a['actor']}] (comentario general): {a['comment']}"
            for a in items
        )
        return {"instructions": instr, "focus_points": [], "model": "concat"}

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    system = (
        "Eres un asistente que SINTETIZA feedback humano disperso en un "
        "prompt CLARO y ACCIONABLE para un agente de diseño que genera "
        "spec.md + prototipo.html. Recibes una lista de anotaciones (cada "
        "una con: rol del revisor, actor, texto seleccionado opcional, "
        "comentario). Devuelves JSON con:\n"
        "- `instructions`: 1-3 párrafos integrando TODOS los comentarios "
        "  por temática, citando texto seleccionado cuando aporta. Conserva "
        "  el nombre del rol/actor cuando agrega contexto.\n"
        "- `focus_points`: lista de bullets accionables (qué tiene que "
        "  cambiar concretamente en spec/prototipo).\n"
        "Importante: NO inventes — solo reorganiza lo que dijeron. Si dos "
        "comentarios se contradicen, marca la contradicción para que el "
        "agente la resuelva. Mantén el lenguaje del revisor (no traduzcas)."
    )
    user_msg = (
        f"Iteración actual: {iteration}\n"
        f"N anotaciones: {len(items)}\n\n"
        "```json\n" + json.dumps(items, ensure_ascii=False, indent=2) + "\n```"
    )
    tool = {
        "name": "submit_prompt",
        "description": "Devuelve el prompt sintetizado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instructions": {"type": "string"},
                "focus_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["instructions", "focus_points"],
        },
    }
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_prompt"},
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        msg = await stream.get_final_message()
    tool_use = next((b for b in msg.content if b.type == "tool_use"), None)
    if not tool_use:
        raise HTTPException(status_code=500,
                            detail="el sintetizador no devolvió prompt")
    out = tool_use.input
    usage = getattr(msg, "usage", None)
    return {
        "instructions": out.get("instructions", ""),
        "focus_points": out.get("focus_points") or [],
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
        } if usage else None,
        "n_annotations": len(items),
    }


# --------------------------------------------------------------------------
# D8: archivado de anotaciones — al iterar se MUEVEN a aplicadas/iter-NNN.json
# y se limpian las pendientes; al "limpiar manual" se mueven a descartadas/.
# Auditoria completa: cada conjunto de anotaciones que se le mandó al agente
# queda inmutable en aplicadas/, y las descartadas también se preservan.
# --------------------------------------------------------------------------
_ANN_APPLIED_DIR = "especificacion/anotaciones-aplicadas"
_ANN_DISCARDED_DIR = "especificacion/anotaciones-descartadas"


async def _archive_annotations(wp_id: int, branch: str, target_dir: str,
                                target_name: str, reason: str) -> int:
    """Mueve las pendientes a {target_dir}/{target_name}.json + limpia
    pendientes. Devuelve cantidad archivada. Idempotente: si no hay
    pendientes, devuelve 0 sin tocar nada."""
    items = await _load_annotations(branch, "")
    if not items:
        return 0
    async with Gitea() as gt:
        await gt.ensure_branch(_ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch)
        archive = {
            "wp_id": wp_id,
            "archived_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "reason": reason,
            "count": len(items),
            "items": items,
        }
        await gt.put_file(
            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
            f"{target_dir}/{target_name}.json",
            json.dumps(archive, indent=2, ensure_ascii=False),
            f"archive anotaciones → {target_dir}/{target_name} refs #{wp_id}")
        # Limpiar pendientes: escribir lista vacía
        await gt.put_file(
            _ARTIFACTS_OWNER, _ARTIFACTS_REPO, branch,
            _annotations_path(""),
            json.dumps([], indent=2),
            f"limpiar pendientes ({len(items)} archivadas) refs #{wp_id}")
    return len(items)


@router.post("/api/wp/{wp_id}/diseno/annotations/archive")
async def api_annotations_archive(wp_id: int, request: Request,
                                   body: dict | None = None) -> dict:
    """Mueve pendientes a anotaciones-aplicadas/iter-NNN.json y las limpia.
    Llamado por el frontend después de un /review action=iterate exitoso —
    asegura que el siguiente iterate no re-envíe anotaciones ya aplicadas."""
    auth.require_user(request)
    branch = f"req-{wp_id}"
    target_iter = (body or {}).get("target_iter") if body else None
    if not target_iter:
        # Calcular iter destino leyendo meta.json (iter_actual + 1)
        async with Gitea() as gt:
            try:
                mf = await gt.get_file(_ARTIFACTS_OWNER, _ARTIFACTS_REPO,
                                        "especificacion/meta.json", ref=branch)
                target_iter = int(json.loads(mf["text"]).get("iter_actual", 0)) + 1
            except Exception:  # noqa: BLE001
                target_iter = 1
    target_name = f"iter-{int(target_iter):03d}"
    n = await _archive_annotations(
        wp_id, branch, _ANN_APPLIED_DIR, target_name,
        reason=f"iterate → iter {target_iter}")
    return {"ok": True, "archived": n,
            "target": f"{_ANN_APPLIED_DIR}/{target_name}.json"}


@router.delete("/api/wp/{wp_id}/diseno/annotations")
async def api_annotations_clear(wp_id: int, request: Request) -> dict:
    """Botón 'Limpiar' del UI: archiva pendientes a
    anotaciones-descartadas/{timestamp}-{user}.json y limpia. NO se pierde
    nada — siempre recuperable desde el repo."""
    user = auth.require_user(request)
    branch = f"req-{wp_id}"
    ts = _time.strftime("%Y-%m-%dT%H-%M-%SZ", _time.gmtime())
    uname = user.get("username") or "anon"
    target_name = f"descartadas-{ts}-{uname}"
    n = await _archive_annotations(
        wp_id, branch, _ANN_DISCARDED_DIR, target_name,
        reason=f"manual clear by {uname}")
    return {"ok": True, "archived": n,
            "target": f"{_ANN_DISCARDED_DIR}/{target_name}.json"}


# --------------------------------------------------------------------------
# UI — SPA embebida
# --------------------------------------------------------------------------
@router.get("/inbox", response_class=HTMLResponse)
async def inbox_page():
    # No cachear el HTML (JS embebido) → siempre la última versión tras deploy.
    return HTMLResponse(_INBOX_HTML, headers={"Cache-Control": "no-store"})


_INBOX_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<title>Inbox de la fabrica</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%230a6ed1'/><line x1='16' y1='3.5' x2='16' y2='8' stroke='%23ffffff' stroke-width='2' stroke-linecap='round'/><circle cx='16' cy='3.5' r='2.3' fill='%2334d399'/><rect x='7.5' y='8' width='17' height='14.5' rx='4.5' fill='%23ffffff'/><circle cx='12.6' cy='15' r='2.1' fill='%230a6ed1'/><circle cx='19.4' cy='15' r='2.1' fill='%230a6ed1'/><rect x='12' y='18.8' width='8' height='1.8' rx='0.9' fill='%230a6ed1'/></svg>"/>
<style>
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    color: #1a1a1a; background: #f5f5f5;
    display: flex; flex-direction: column;   /* header (auto) + vista (flex:1) */
  }
  header {
    background: #0a6ed1; color: #fff; padding: 10px 20px;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  }
  header h1 { font-size: 18px; margin: 0; font-weight: 600; }
  header .actions { margin-left: auto; display: flex; gap: 8px; }
  header button {
    background: #fff3; color: #fff; border: 1px solid #fff5;
    padding: 6px 12px; border-radius: 4px; cursor: pointer; font: inherit;
  }
  header button:hover { background: #fff5; }
  main {
    display: grid;
    grid-template-columns: var(--list-width, 360px) 6px 1fr;
    flex: 1; min-height: 0;
  }
  /* Columna de lista: barra de búsqueda fija arriba + items scrollables. */
  aside#list-col {
    background: #fafafa; min-width: 0;
    display: flex; flex-direction: column; overflow: hidden;
  }
  #list-search { padding: 8px 8px 6px; border-bottom: 1px solid #e6e6e6;
    display: flex; flex-direction: column; gap: 6px; }
  /* Botón de menú (☰) y drawer lateral. */
  #menu-btn { font-size: 18px; line-height: 1; padding: 6px 11px; }
  #drawer-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.35);
    opacity: 0; pointer-events: none; transition: opacity .2s; z-index: 40;
  }
  #drawer-overlay.show { opacity: 1; pointer-events: auto; }
  #drawer {
    position: fixed; top: 0; left: 0; height: 100%; width: 270px;
    background: #fff; box-shadow: 2px 0 16px rgba(0,0,0,.2);
    transform: translateX(-100%); transition: transform .2s ease;
    z-index: 50; display: flex; flex-direction: column; padding: 16px;
  }
  #drawer.show { transform: translateX(0); }
  #drawer-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 14px; }
  #drawer #user-badge {
    font-size: 13px; color: #333; background: #f0f2f5; padding: 8px 12px;
    border-radius: 8px; line-height: 1.35; word-break: break-word;
  }
  #drawer-close { background: transparent; border: none; font-size: 18px; color: #888; cursor: pointer; line-height: 1; }
  #drawer #view-switch { flex-direction: column; background: transparent; gap: 4px; padding: 0; }
  #drawer #view-switch button {
    color: #1a1a1a; text-align: left; padding: 10px 12px; border-radius: 8px;
    font-size: 14px; background: #f7f8fa; border: none; cursor: pointer; width: 100%;
  }
  #drawer #view-switch button.active { background: #eef4fc; color: #0a6ed1; font-weight: 600; }
  #drawer-foot { margin-top: auto; }
  #drawer #logout-btn {
    width: 100%; background: #fff; color: #c0392b; border: 1px solid #e3b4ae;
    padding: 10px 12px; border-radius: 8px; cursor: pointer; font: inherit; font-weight: 600;
  }
  #drawer #logout-btn:hover { background: #fdecea; }
  #list-search input {
    width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
    padding: 6px 9px; border: 1px solid #d0d0d0; border-radius: 6px;
  }
  #list {
    flex: 1; min-height: 0;
    overflow-y: auto; padding: 8px;
  }
  #list-divider {
    background: #ddd; cursor: col-resize;
    position: relative; transition: background 0.15s;
  }
  #list-divider:hover { background: #0a6ed1; }
  #list-divider::after {
    content: ""; position: absolute;
    top: 50%; left: 50%; transform: translate(-50%,-50%);
    width: 2px; height: 30px; background: #999; border-radius: 2px;
  }
  #list .empty { color: #888; padding: 16px; text-align: center; }
  .item {
    background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
    padding: 10px 12px; margin-bottom: 8px; cursor: pointer;
    transition: border-color .1s;
  }
  .item:hover { border-color: #0a6ed1; }
  .item.selected { border-color: #0a6ed1; border-width: 2px; padding: 9px 11px; }
  .item .title { font-weight: 600; margin-bottom: 4px; }
  .item .meta { color: #666; font-size: 12px; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600; margin-right: 4px;
  }
  .badge-spec_review { background: #ffe7c2; color: #8a5500; }
  .badge-gate { background: #d4f0d4; color: #155724; }
  .badge-clarification { background: #ffd7d7; color: #842029; }
  .badge-running { background: #e0e0e0; color: #555; }
  .badge-status { background: #e7f0ff; color: #0a6ed1; }

  /* Chip de etapa de negocio (lista del inbox) */
  .etapa-chip {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600;
    background: #e7f0ff; color: #0a6ed1;
  }
  .etapa-chip[data-action="1"] { background: #fff3cd; color: #856404; }
  .etapa-chip[data-blocked="1"] { background: #ffd7d7; color: #842029; }
  .etapa-chip[data-done="1"] { background: #d4edda; color: #155724; }

  /* Timeline horizontal de etapas (header del detalle) */
  .timeline {
    display: flex; align-items: stretch; gap: 0;
    margin: 12px 0 20px 0;
    overflow-x: auto;
    padding: 4px 0;
  }
  .timeline .step {
    flex: 1; min-width: 90px;
    display: flex; flex-direction: column; align-items: center;
    gap: 4px;
    text-align: center;
    position: relative;
  }
  .timeline .step .icon {
    width: 36px; height: 36px; border-radius: 50%;
    background: #f0f0f0; color: #999;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
    border: 2px solid transparent;
    z-index: 1;
  }
  .timeline .step .label {
    font-size: 11px; color: #888; line-height: 1.2;
    max-width: 110px;
  }
  .timeline .step.done .icon {
    background: #d4edda; color: #155724;
  }
  .timeline .step.done .label { color: #155724; }
  .timeline .step.current .icon {
    background: #fff3cd; color: #856404;
    border-color: #f0a020;
    box-shadow: 0 0 0 4px #fff3cd;
    transform: scale(1.1);
  }
  .timeline .step.current .label {
    color: #856404; font-weight: 700; font-size: 12px;
  }
  .timeline .step.blocked .icon {
    background: #ffd7d7; color: #842029;
    border-color: #c0392b;
    box-shadow: 0 0 0 4px #ffd7d7;
  }
  .timeline .step.blocked .label { color: #842029; font-weight: 700; }
  /* Línea conectora entre steps */
  .timeline .step::before {
    content: ""; position: absolute;
    top: 18px; left: -50%; right: 50%; height: 2px;
    background: #e0e0e0; z-index: 0;
  }
  .timeline .step:first-child::before { display: none; }
  .timeline .step.done::before,
  .timeline .step.current::before { background: #28a745; }
  .timeline .step.blocked::before { background: #c0392b; }
  .timeline .step .sub {
    font-size: 10px; color: #aaa; margin-top: 2px;
  }
  .timeline .step.current .sub { color: #856404; }
  /* Rol responsable por etapa + marca de compuerta de decisión humana.
     Hace visible la separación de roles a lo largo de todo el flujo. */
  .timeline .step .roles {
    display: flex; flex-wrap: wrap; gap: 2px; justify-content: center;
    margin-top: 3px; max-width: 120px;
  }
  .timeline .step .rchip {
    font-size: 9px; font-weight: 600; padding: 0 5px; border-radius: 7px;
    line-height: 15px; white-space: nowrap;
  }
  .timeline .step .gate-tag {
    font-size: 9px; font-weight: 700; color: #c0392b; margin-top: 2px;
  }
  /* Compuerta NO activa (pasada o futura): marca tenue, sin alarma roja.
     Solo la etapa actual con turno pendiente muestra el 🔴 rojo sólido. */
  .timeline .step .gate-tag.muted {
    color: #9aa3ab; font-weight: 600;
  }
  /* Chip de VoBo del cliente: ✓ aprobado (verde tenue) / 🔔 turno actual. */
  .timeline .step .rchip.vobo-done { opacity: .55; }
  .timeline .step .rchip.vobo-turn {
    box-shadow: 0 0 0 1px #f0a020; animation: pulse 1.6s ease-in-out infinite;
  }
  section#detail {
    overflow-y: auto; padding: 20px 28px;
  }
  section#detail .empty {
    color: #888; text-align: center; margin-top: 100px;
  }
  section#detail h2 { margin: 0 0 4px 0; font-size: 22px; }
  section#detail .meta { color: #666; margin-bottom: 8px; }
  .ext-link {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 4px 10px; border: 1px solid #cfd8dc; border-radius: 4px;
    background: #f5f5f5; color: #1a1a1a; text-decoration: none;
    font-size: 12px;
  }
  .ext-link:hover { background: #e3f2fd; border-color: #0a6ed1; color: #0a6ed1; }
  .panel {
    background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
    padding: 16px 20px; margin-bottom: 16px;
  }
  .panel h3 {
    margin: 0 0 12px 0; font-size: 14px; color: #555;
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  /* Markdown render */
  .md-body { font-size: 14px; }
  .md-body h1 { font-size: 22px; margin: 16px 0 8px; }
  .md-body h2 { font-size: 18px; margin: 14px 0 6px; color: #333; }
  .md-body h3 { font-size: 15px; margin: 12px 0 4px; color: #555; }
  .md-body p { margin: 6px 0; }
  .md-body ul, .md-body ol { margin: 6px 0; padding-left: 28px; }
  .md-body li { margin: 2px 0; }
  .md-body code {
    background: #f0f0f0; padding: 1px 4px; border-radius: 3px;
    font-family: "Consolas", monospace; font-size: 13px;
  }
  .md-body pre {
    background: #2d2d2d; color: #f0f0f0; padding: 12px;
    border-radius: 4px; overflow-x: auto;
  }
  .md-body pre code { background: transparent; color: inherit; }
  .md-body table {
    border-collapse: collapse; margin: 8px 0; font-size: 13px;
  }
  .md-body th, .md-body td {
    border: 1px solid #ddd; padding: 6px 10px; text-align: left;
  }
  .md-body th { background: #f5f5f5; }
  .md-body blockquote {
    border-left: 3px solid #0a6ed1; margin: 8px 0; padding: 4px 12px;
    color: #555; background: #f5f8fc;
  }
  /* iframe prototipo */
  iframe.prototipo {
    width: 100%; height: 600px; border: 1px solid #ddd; border-radius: 4px;
    background: #fff;
  }
  /* Diseño split: spec MD + prototipo HTML lado a lado o apilados,
     con divisor draggable. */
  .diseno-split {
    display: grid; gap: 0;
    height: 78vh;
  }
  .diseno-split.horizontal {
    grid-template-columns: var(--split-left, 1fr) 6px var(--split-right, 1fr);
  }
  .diseno-split.vertical {
    grid-template-rows: var(--split-top, 1fr) 6px var(--split-bottom, 1fr);
    grid-template-columns: 1fr;
  }
  .diseno-split > .pane {
    min-width: 0; min-height: 0;
    overflow: auto;
    background: #fff; border: 1px solid #e0e0e0; border-radius: 4px;
    padding: 12px 16px;
  }
  .diseno-split .divider {
    background: #e0e0e0; position: relative;
    transition: background 0.15s;
  }
  .diseno-split .divider:hover { background: #0a6ed1; }
  .diseno-split.horizontal .divider { cursor: col-resize; }
  .diseno-split.vertical .divider { cursor: row-resize; }
  .diseno-split .divider::after {
    content: ""; position: absolute;
    background: #999; border-radius: 2px;
  }
  .diseno-split.horizontal .divider::after {
    top: 50%; left: 50%; transform: translate(-50%,-50%);
    width: 2px; height: 30px;
  }
  .diseno-split.vertical .divider::after {
    top: 50%; left: 50%; transform: translate(-50%,-50%);
    width: 30px; height: 2px;
  }
  .diseno-split iframe.prototipo {
    width: 100%; height: 100%; border: none; background: #fff;
    display: block;
  }
  /* Anotaciones */
  .ann-popover {
    position: fixed; background: #0a6ed1; color: #fff;
    padding: 6px 12px; border-radius: 4px; font-size: 12px;
    cursor: pointer; z-index: 9999; box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    user-select: none;
  }
  .ann-popover:hover { background: #084788; }
  .ann-modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.4);
    display: flex; align-items: center; justify-content: center;
    z-index: 10000;
  }
  .ann-modal {
    background: #fff; border-radius: 6px; width: 540px; max-width: 92vw;
    padding: 20px; box-shadow: 0 8px 32px rgba(0,0,0,0.3);
  }
  .ann-modal h3 { margin: 0 0 12px 0; font-size: 16px; }
  .ann-modal .quoted {
    background: #f5f5f5; border-left: 3px solid #0a6ed1;
    padding: 8px 12px; font-size: 12px; color: #444;
    margin: 8px 0; max-height: 80px; overflow: auto;
    font-style: italic;
  }
  .ann-modal textarea, .ann-modal input, .ann-modal select {
    width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px;
    font-family: inherit; font-size: 14px; margin-bottom: 8px;
  }
  .ann-modal textarea { min-height: 80px; resize: vertical; }
  .ann-modal label { font-size: 12px; color: #666; font-weight: 600; }
  .ann-modal .row { display: flex; gap: 8px; }
  .ann-modal .row > div { flex: 1; }
  .ann-modal .buttons { text-align: right; margin-top: 12px; }
  .ann-modal .buttons button { padding: 8px 16px; margin-left: 6px; cursor: pointer; }
  .ann-list-bar {
    margin-top: 10px; padding: 8px 12px;
    background: #f0f7ff; border: 1px solid #b6d4f0; border-radius: 4px;
    display: flex; align-items: center; gap: 12px; font-size: 13px;
  }
  .ann-list-bar.empty { background: #fafafa; border-color: #ddd; color: #888; }
  .ann-list-bar .count { font-weight: 600; color: #0a6ed1; }
  .ann-list-bar .spacer { flex: 1; }
  .ann-list-bar button {
    padding: 4px 12px; border: 1px solid #0a6ed1; background: #fff;
    color: #0a6ed1; border-radius: 4px; cursor: pointer; font-size: 12px;
  }
  .ann-list-bar button.primary { background: #0a6ed1; color: #fff; }
  .ann-list {
    max-height: 45vh; overflow: auto; margin: 8px 0;
    border: 1px solid #e0e0e0; border-radius: 4px;
  }
  .ann-item {
    padding: 10px 12px; border-bottom: 1px solid #eee;
    display: flex; gap: 10px; align-items: flex-start;
  }
  .ann-item:last-child { border-bottom: none; }
  .ann-item .meta { font-size: 11px; color: #888; margin-bottom: 4px; }
  .ann-item .quoted {
    background: #f5f5f5; padding: 4px 8px; font-size: 12px; color: #444;
    border-left: 2px solid #0a6ed1; margin: 4px 0; font-style: italic;
  }
  .ann-item .comment { font-size: 13px; }
  .ann-item .body { flex: 1; min-width: 0; }
  .ann-item button.del {
    background: none; border: none; color: #c33; cursor: pointer;
    font-size: 14px; padding: 0 4px;
  }
  .role-badge {
    display: inline-block; padding: 1px 6px; border-radius: 8px;
    font-size: 10px; font-weight: 600; text-transform: uppercase;
  }
  .role-swf-funcional     { background: #e3f2fd; color: #0a6ed1; }
  .role-swf-desarrollador { background: #ede7f6; color: #5e35b1; }
  .role-swf-tester        { background: #f3e5f5; color: #8e24aa; }
  .role-funcional         { background: #e8f5e9; color: #107e3e; }
  .role-usuario-clave     { background: #fff3e0; color: #df6e0c; }
  /* Compat con anotaciones viejas */
  .role-swf     { background: #e3f2fd; color: #0a6ed1; }
  .role-negocio { background: #fff3e0; color: #df6e0c; }
  .synth-modal textarea { min-height: 180px; }
  /* Plan / estimación */
  .plan-summary {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px; margin-bottom: 16px;
  }
  .plan-kpi {
    background: #f5f8fc; border: 1px solid #d1e1f0; border-radius: 6px;
    padding: 12px 16px;
  }
  .plan-kpi .lbl { font-size: 11px; color: #666; text-transform: uppercase; }
  .plan-kpi .val { font-size: 20px; font-weight: 600; color: #0a6ed1; margin-top: 4px; }
  .plan-kpi.complexity-S .val { color: #107e3e; }
  .plan-kpi.complexity-M .val { color: #0a6ed1; }
  .plan-kpi.complexity-L .val { color: #df6e0c; }
  .plan-kpi.complexity-XL .val { color: #bb0000; }
  table.wp-table {
    width: 100%; border-collapse: collapse; background: #fff;
    font-size: 13px;
  }
  table.wp-table th, table.wp-table td {
    text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee;
    vertical-align: top;
  }
  table.wp-table th {
    background: #f5f5f5; font-weight: 600; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.3px;
  }
  table.wp-table tr:hover td { background: #fafbff; }
  table.wp-table .hours { text-align: right; font-family: monospace; }
  table.wp-table .rationale { color: #666; font-size: 11px; font-style: italic; margin-top: 4px; }
  .tech-badge {
    display: inline-block; padding: 2px 8px; margin: 2px;
    background: #e8f5e9; color: #107e3e; border-radius: 10px;
    font-size: 10px; font-weight: 600;
  }
  .tech-badge.warn { background: #fff3e0; color: #df6e0c; }
  .split-toolbar {
    display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    padding-bottom: 6px; border-bottom: 1px solid #e0e0e0; margin-bottom: 12px;
  }
  .split-toolbar .split-toggle {
    display: inline-flex; border: 1px solid #c0c0c0; border-radius: 4px;
    overflow: hidden; font-size: 12px;
  }
  .split-toolbar .split-toggle button {
    border: none; background: #fff; padding: 4px 10px; cursor: pointer;
  }
  .split-toolbar .split-toggle button.active {
    background: #0a6ed1; color: #fff;
  }
  /* Forms */
  form { display: flex; flex-direction: column; gap: 8px; }
  label { font-size: 12px; color: #555; font-weight: 600; }
  input, select, textarea {
    font: inherit; padding: 6px 10px; border: 1px solid #ccc;
    border-radius: 4px; background: #fff;
  }
  textarea { resize: vertical; min-height: 60px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .row > * { flex: 1; }
  button.primary {
    background: #0a6ed1; color: #fff; border: none; padding: 8px 16px;
    border-radius: 4px; cursor: pointer; font: inherit; font-weight: 600;
  }
  button.primary:hover { background: #086abb; }
  button.secondary {
    background: #fff; color: #0a6ed1; border: 1px solid #0a6ed1;
    padding: 8px 16px; border-radius: 4px; cursor: pointer; font: inherit;
  }
  button.danger {
    background: #c0392b; color: #fff; border: none; padding: 8px 16px;
    border-radius: 4px; cursor: pointer; font: inherit;
  }
  .tabs {
    display: flex; gap: 0; border-bottom: 2px solid #e0e0e0;
    margin-bottom: 16px;
  }
  .tab {
    padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent;
    margin-bottom: -2px; font-weight: 600; color: #666;
  }
  .tab.active { color: #0a6ed1; border-bottom-color: #0a6ed1; }
  .toast {
    position: fixed; bottom: 20px; right: 20px;
    background: #1a1a1a; color: #fff; padding: 12px 20px;
    border-radius: 4px; opacity: 0; transition: opacity 0.3s;
    pointer-events: none; max-width: 400px;
  }
  .toast.show { opacity: 1; }
  .toast.error { background: #c0392b; }
  .toast.ok { background: #155724; }
  /* Pulse para badge "Tu turno" (C2) */
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.85; transform: scale(1.06); }
  }
  /* Conmutador de vistas (Inbox / Estado del proyecto / ...). */
  #view-switch { display: flex; gap: 2px; background: #fff3; border-radius: 14px; padding: 2px; }
  #view-switch button { background: transparent; color: #fff; border: none; padding: 4px 12px;
    border-radius: 12px; cursor: pointer; font: inherit; font-size: 13px; }
  #view-switch button.active { background: #fff; color: #0a6ed1; font-weight: 600; }
  /* Vista Estado del proyecto. */
  section#view-proyecto { flex: 1; min-height: 0; overflow: auto; padding: 20px 28px; background: #f5f5f5; }
  .kpi-row { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
  .kpi { background: #fff; border: 1px solid #e3e3e3; border-radius: 10px; padding: 14px 18px;
    min-width: 150px; flex: 1; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  .kpi .num { font-size: 30px; font-weight: 700; color: #0a6ed1; line-height: 1.1; }
  .kpi .lbl { font-size: 12px; color: #666; margin-top: 4px; }
  .kpi.accent .num { color: #107e3e; }
  .kpi.warn .num { color: #c47d00; }
  .cap-card { background: #fff; border: 1px solid #e3e3e3; border-radius: 10px; margin-bottom: 16px; overflow: hidden; }
  .cap-head { display: flex; align-items: center; gap: 12px; padding: 12px 16px; cursor: pointer; }
  .cap-head h3 { margin: 0; font-size: 15px; color: #1a3a52; flex: 1; }
  .bar { background: #eceff3; border-radius: 6px; height: 10px; width: 160px; overflow: hidden; }
  .bar > span { display: block; height: 100%; background: linear-gradient(90deg,#0a6ed1,#36a3ff); }
  .pct { font-size: 13px; font-weight: 700; color: #0a6ed1; min-width: 40px; text-align: right; }
  table.resumen { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  table.resumen th, table.resumen td { border-top: 1px solid #eee; padding: 7px 10px; text-align: left; vertical-align: middle; }
  table.resumen th { background: #f8fafc; font-weight: 600; color: #555; font-size: 11px; }
  table.resumen tr:hover td { background: #fafcff; }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 11px; font-size: 11px; font-weight: 600; }
  .pill.curso { background: #e7f1fb; color: #0a6ed1; }
  .pill.esp { background: #fff3cd; color: #8a6d00; }
  .pill.fin { background: #e3f6e9; color: #107e3e; }
  .minibar { background: #eceff3; border-radius: 5px; height: 8px; width: 90px; display: inline-block; vertical-align: middle; overflow: hidden; }
  .minibar > span { display: block; height: 100%; background: linear-gradient(90deg,#0a6ed1,#36a3ff); }
  .cell-cur { background: #fff3cd !important; }
  /* Fila de grupo por capacidad dentro de la tabla única del resumen. */
  table.resumen .cap-row td { background: #eef4fc; font-weight: 700; color: #1a3a52; border-top: 2px solid #d6e4f5; padding: 8px 10px; }
  .cap-inlinebar { display: inline-block; vertical-align: middle; width: 120px; height: 9px; background: #dde7f3; border-radius: 5px; overflow: hidden; margin: 0 8px; }
  .cap-inlinebar > span { display: block; height: 100%; background: linear-gradient(90deg,#0a6ed1,#36a3ff); }
  /* Gantt coqueto (vista Estado del proyecto). */
  .gantt { background: #fff; border: 1px solid #e3e3e3; border-radius: 10px; padding: 10px 14px 14px; overflow-x: auto; }
  .g-row, .g-caprow { display: flex; align-items: center; height: 26px; }
  .g-axisrow { height: 22px; margin-bottom: 2px; }
  .g-lbl { width: 230px; flex: none; font-size: 12px; color: #333; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 10px; }
  .g-caplbl { font-weight: 700; color: #1a3a52; }
  .g-track { position: relative; flex: 1; min-width: 360px; height: 100%; border-left: 1px solid #eef0f3; }
  .g-capbg { background: #f8fafc; }
  .g-tick { position: absolute; top: 0; height: 22px; border-left: 1px dashed #eef0f3; }
  .g-tick span { position: absolute; top: 3px; left: 4px; font-size: 10px; color: #aaa; white-space: nowrap; }
  .g-today { position: absolute; top: 0; bottom: 0; width: 2px; background: rgba(231,76,60,.45); z-index: 2; }
  .g-bar { position: absolute; top: 5px; height: 16px; border-radius: 8px; background: #e9eef3; box-shadow: inset 0 0 0 1px #dde3ea; overflow: hidden; min-width: 6px; }
  .g-bfill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 8px 0 0 8px; }
  .g-btag { position: absolute; right: 5px; top: 0; font-size: 10px; line-height: 16px; font-weight: 600; }
  .g-nodate { font-size: 10px; color: #c0392b; line-height: 26px; padding-left: 4px; }
</style>
</head>
<body>
<!-- Drawer lateral (menú ☰): usuario+rol, conmutador de vista y logout.
     Off-canvas; se abre con el botón de 3 líneas del header. -->
<div id="drawer-overlay"></div>
<aside id="drawer">
  <div id="drawer-head">
    <span id="user-badge"></span>
    <button id="drawer-close" title="Cerrar menú" aria-label="Cerrar menú">✕</button>
  </div>
  <!-- Conmutador de vistas. Extensible: agregar un <button data-view="..."> y
       un caso en showView()/render. La bandeja (view-inbox) no se modifica. -->
  <nav id="view-switch">
    <button data-view="inbox" class="active">📥 Inbox</button>
    <button data-view="proyecto">📊 Estado del proyecto</button>
  </nav>
  <div id="drawer-foot">
    <button id="logout-btn" title="Cerrar sesión">⎋ Cerrar sesión</button>
  </div>
</aside>
<header>
  <button id="menu-btn" title="Menú" aria-label="Menú">☰</button>
  <h1>Inbox de la fabrica</h1>
  <!-- Proyecto + capacidad apilados (una columna) para ahorrar ancho arriba.
       El backend filtra los requisitos al proyecto/capacidad elegidos. La
       capacidad aparece solo si hay más de una para filtrar. -->
  <div style="display:flex;flex-direction:column;gap:4px;min-width:170px">
    <label style="font-size:12px;color:#fff;display:flex;align-items:center;gap:6px">📁
      <select id="project-select" style="font-size:12px;padding:3px 6px;border-radius:6px;border:none;flex:1;min-width:0"></select>
    </label>
    <label id="cap-select-wrap" style="font-size:12px;color:#fff;display:none;align-items:center;gap:6px">🎯
      <select id="capability-select" style="font-size:12px;padding:3px 6px;border-radius:6px;border:none;flex:1;min-width:0"></select>
    </label>
  </div>
  <div class="actions" style="margin-left:auto">
    <button id="new-req-btn" style="background:#fff;color:#0a6ed1;border:none;font-weight:600">🚀 Iniciar requerimiento</button>
    <button id="refresh-btn">Refrescar</button>
  </div>
</header>
<main id="view-inbox">
  <aside id="list-col">
    <!-- Barra encima de la lista: toggle Mi inbox/Todos (solo admins) +
         búsqueda de texto libre (cliente) por #, título, proyecto, asignado,
         etapa o estado. Instantáneo, sobre lo ya cargado. -->
    <div id="list-search">
      <div id="scope-toggle" style="display:none;background:#eef1f5;border-radius:12px;padding:2px;font-size:12px;align-self:flex-start">
        <button data-scope="mine" class="active" style="background:#0a6ed1;color:#fff;border:none;padding:4px 12px;border-radius:12px;cursor:pointer;font-weight:600">Mi inbox</button>
        <button data-scope="all" style="background:transparent;color:#555;border:none;padding:4px 12px;border-radius:12px;cursor:pointer">Todos</button>
      </div>
      <input id="list-filter" type="search" placeholder="🔎 Buscar requerimiento…" autocomplete="off" />
    </div>
    <div id="list"><div class="empty">cargando...</div></div>
  </aside>
  <div id="list-divider" title="Arrastra para redimensionar la lista"></div>
  <section id="detail"><div class="empty">Selecciona un Requisito a la izquierda.</div></section>
</main>
<section id="view-proyecto" style="display:none"><div class="empty" style="padding:40px;text-align:center;color:#888">Elige un proyecto arriba para ver su estado.</div></section>
<div id="toast" class="toast"></div>
<script>
const $list = document.getElementById("list");
const $detail = document.getElementById("detail");
const $toast = document.getElementById("toast");
let currentWpId = null;
let currentDetail = null;
let currentIteration = null;

function toast(msg, kind = "ok") {
  $toast.textContent = msg;
  $toast.className = "toast show " + kind;
  setTimeout(() => $toast.className = "toast", 3500);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status}: ${t}`);
  }
  const ct = r.headers.get("Content-Type") || "";
  return ct.startsWith("application/json") ? r.json() : r.text();
}

let currentScope = (localStorage.getItem("inboxScope") || "mine");
let currentProject = (localStorage.getItem("inboxProject") || "");
let currentCapability = (localStorage.getItem("inboxCapability") || "");
let currentFilter = (localStorage.getItem("inboxFilter") || "");
let _filterTimer = null;   // debounce de la búsqueda en servidor

// Filtro de texto libre sobre la lista YA cargada (cliente). Oculta las filas
// que no contienen el término en su `data-search` (#, título, proyecto,
// asignado, etapa, estado). Se re-aplica tras cada loadInbox para persistir.
function applyListFilter() {
  const q = (currentFilter || "").trim().toLowerCase();
  let shown = 0;
  $list.querySelectorAll(".item").forEach(el => {
    const ok = !q || (el.dataset.search || "").includes(q);
    el.style.display = ok ? "" : "none";
    if (ok) shown++;
  });
  let none = document.getElementById("list-nomatch");
  if (q && shown === 0) {
    if (!none) {
      none = document.createElement("div");
      none.id = "list-nomatch"; none.className = "empty";
      $list.appendChild(none);
    }
    none.textContent = `Sin coincidencias para "${currentFilter.trim()}".`;
    none.style.display = "";
  } else if (none) {
    none.style.display = "none";
  }
  return shown;
}

// Pobla el selector de capacidades del proyecto elegido. Se muestra solo si
// hay MÁS de una capacidad entre las que el usuario puede filtrar.
async function loadMyCapabilities() {
  const wrap = document.getElementById("cap-select-wrap");
  const sel = document.getElementById("capability-select");
  if (!wrap || !sel) return;
  if (!currentProject) { wrap.style.display = "none"; currentCapability = ""; return; }
  let data;
  try { data = await api(`/api/mis-capacidades?project=${encodeURIComponent(currentProject)}`); }
  catch (e) { wrap.style.display = "none"; return; }
  const caps = data.capabilities || [];
  if (caps.length <= 1) {   // 0 o 1 → no tiene sentido el filtro
    wrap.style.display = "none"; currentCapability = "";
    localStorage.setItem("inboxCapability", "");
    return;
  }
  const valid = caps.map(c => String(c.id));
  if (currentCapability && !valid.includes(currentCapability)) currentCapability = "";
  sel.innerHTML = `<option value="">— Todas mis capacidades —</option>` +
    caps.map(c => `<option value="${c.id}">${(c.name || "").replace(/</g, "&lt;")}</option>`).join("");
  sel.value = currentCapability;
  wrap.style.display = "inline-flex";
}

// Pobla el selector de proyectos con los que el usuario tiene asignados.
// Si no tiene asignación (o es admin) ofrece «Todos» + el catálogo completo.
async function loadMyProjects() {
  const sel = document.getElementById("project-select");
  if (!sel) return;
  let data;
  try { data = await api("/api/mis-proyectos"); }
  catch (e) { return; }
  const opts = [];
  if (data.all) opts.push('<option value="">— Todos los proyectos —</option>');
  for (const p of (data.projects || [])) {
    opts.push(`<option value="${p.id}">${(p.name || "").replace(/</g, "&lt;")}</option>`);
  }
  sel.innerHTML = opts.join("");
  // Validar la selección guardada contra las opciones disponibles.
  const valid = (data.projects || []).map(p => String(p.id));
  if (!data.all && (!currentProject || !valid.includes(currentProject))) {
    currentProject = valid[0] || "";   // sin «Todos» → default al primero
  } else if (data.all && currentProject && !valid.includes(currentProject)) {
    currentProject = "";
  }
  sel.value = currentProject;
  localStorage.setItem("inboxProject", currentProject);
}

async function loadInbox(serverQuery) {
  // Solo mostramos "cargando…" en la primera carga (lista vacía). En refrescos
  // (guardar, polling) reconstruimos en un fragment y hacemos un solo swap,
  // preservando el scroll — sin blanqueo ni parpadeo.
  const _firstLoad = !$list.querySelector(".item");
  const _prevScroll = $list.scrollTop;
  if (_firstLoad) $list.innerHTML = '<div class="empty">cargando...</div>';
  try {
    const q = `/api/inbox?scope=${currentScope}`
      + (currentProject ? `&project=${encodeURIComponent(currentProject)}` : "")
      + (currentCapability ? `&capability=${encodeURIComponent(currentCapability)}` : "")
      + (serverQuery ? `&q=${encodeURIComponent(serverQuery)}` : "");
    const data = await api(q);
    // Toggle visible solo si el backend lo permite (admins)
    const $st = document.getElementById("scope-toggle");
    if ($st) $st.style.display = data.can_toggle_all ? "inline-flex" : "none";
    if (!data.items.length) {
      $list.innerHTML = `<div class="empty">No hay Requisitos en tu inbox (scope: ${data.scope}).${data.can_toggle_all && data.scope === "mine" ? "<br><br>Cambia a <b>Todos</b> para ver los del resto." : ""}</div>`;
      return;
    }
    const _frag = document.createDocumentFragment();
    for (const it of data.items) {
      const div = document.createElement("div");
      div.dataset.wpId = it.wp_id;
      // Texto indexado para el filtro de búsqueda libre.
      div.dataset.search = [it.wp_id, it.subject, it.project, it.assignee,
        (it.etapa && it.etapa.label), it.status].filter(Boolean).join(" ").toLowerCase();
      div.className = "item" + (it.wp_id === currentWpId ? " selected" : "");
      const badges = [];
      if (it.pending) {
        const labels = {
          spec_review: "REVISAR SPEC",   // compat — el backend ya manda "review"
          review: "REVISAR",
          gate: "GATE HUMANO",
          clarification: "CLARIFICACION",
          running: "EN CURSO",
        };
        badges.push(`<span class="badge badge-${it.pending}">${labels[it.pending] || it.pending}</span>`);
      }
      if (it.status) badges.push(`<span class="badge badge-status">${it.status}</span>`);
      // Chip de etapa de negocio (reemplaza el `stage` técnico crudo que
      // antes mostrábamos en la línea inferior). Color: ámbar si hay acción
      // pendiente, rojo si bloqueado, verde si cerrado, azul si solo informa.
      const et = it.etapa || {};
      const etDataset = et.key === "cerrado" ? 'data-done="1"' :
                         et.is_blocked ? 'data-blocked="1"' :
                         et.action_pending ? 'data-action="1"' : "";
      const etapaChip = et.label ?
        `<span class="etapa-chip" ${etDataset} title="Etapa ${et.index}/7 — stage técnico: ${escapeHtml(it.stage || "—")}">${et.icon || ""} ${escapeHtml(et.label)}${et.is_blocked ? " ⏸" : ""}</span>` : "";
      const turnBadge = it.is_my_turn
        ? '<span class="badge" style="background:#fff3cd;color:#856404;border:1px solid #f0a020;animation:pulse 1.6s ease-in-out infinite">🔔 Tu turno</span>' : "";
      const assigneeInfo = it.assignee
        ? `<span style="color:${it.is_mine ? "#107e3e" : "#888"}" title="${it.is_mine ? "Asignado a ti" : "Asignado a otra persona"}">${it.is_mine ? "👤 Tú" : "👤 " + escapeHtml(it.assignee)}</span>`
        : '<span style="color:#999" title="Sin asignar — en pool del rol">👥 sin asignar</span>';
      div.innerHTML = `
        <div class="title">${turnBadge}#${it.wp_id} ${escapeHtml(it.subject || "")}</div>
        <div class="meta">${etapaChip} ${badges.join("")}</div>
        <div class="meta">${escapeHtml(it.project || "")} · ${assigneeInfo}</div>
      `;
      div.onclick = () => loadDetail(it.wp_id);
      _frag.appendChild(div);
    }
    $list.innerHTML = "";
    $list.appendChild(_frag);
    $list.scrollTop = _prevScroll;   // no saltar al tope al refrescar
    applyListFilter();               // mantener el filtro de búsqueda activo
  } catch (e) {
    $list.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadDetail(wpId) {
  currentWpId = wpId;
  currentIteration = null;
  // Resetear estado del plan al cambiar de WP (forzar GET fresco).
  delete _planMode[wpId];
  delete _planCache[wpId];
  delete _planOriginal[wpId];
  delete _planPersisted[wpId];
  delete _planIterations[wpId];
  // Marcar el seleccionado alternando clase sobre las filas existentes —
  // NO reconstruir la lista (eso causaba el parpadeo en cada clic).
  document.querySelectorAll(".item").forEach(el =>
    el.classList.toggle("selected", String(el.dataset.wpId) === String(wpId)));
  // Solo blanquear si el panel está vacío (primera carga); si no, dejamos el
  // detalle anterior visible y atenuado hasta que llega el nuevo (swap único).
  if (!$detail.querySelector("h2")) $detail.innerHTML = '<div class="empty">cargando...</div>';
  setLoading($detail, true);
  try {
    const d = await api(`/api/wp/${wpId}`);
    currentDetail = d;
    renderDetail(d);
  } catch (e) {
    $detail.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message)}</div>`;
  } finally {
    setLoading($detail, false);
  }
}

function escapeHtml(s) {
  if (!s) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// Estado de carga NO destructivo: en vez de blanquear una sección con
// "cargando…" (parpadeo + pérdida de scroll/estado), deja el contenido actual
// visible y solo lo atenúa mientras llega el dato nuevo. Se cambia una vez.
function setLoading(el, on) {
  if (!el) return;
  el.style.transition = "opacity .12s ease";
  el.style.opacity = on ? "0.5" : "1";
  el.style.pointerEvents = on ? "none" : "";
}

function renderDetail(d) {
  const stageBadge = d.stage ? `<span class="badge badge-running">${escapeHtml(d.stage)}</span>` : "";
  // Spec/Prototipo viejas eliminadas — Diseño las contiene a las dos con
  // dropdown de versión. Las rutas legadas (especificacion/spec.md,
  // prototipo/prototipo.html) siguen escribiéndose en Gitea al aprobar
  // para downstream consumers.
  // "Paquete funcional" = spec + prototipo + test plan (el bloque diseno
  // los produce y aprueba como una unidad). Ver pipeline.yaml v4.
  const tabs = ["Info general", "Diseño", "Desarrollo", "Plan", "Blog"];
  // Links externos al WP: Temporal Web (workflow) y Gitea (branch del WP).
  const tempUrl = d.temporal_web_url ? `${d.temporal_web_url}/namespaces/default/workflows/requisito-${d.wp_id}` : "";
  const giteaUrl = `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}`;
  // Timeline horizontal de las 7 etapas de negocio (ver _ETAPAS en backend).
  // El backend nos manda la etapa actual + all_stages para que la UI no
  // necesite hardcodear el listado.
  const etapa = d.etapa || {};
  const allStages = etapa.all_stages || [];
  const curIdx = etapa.index || 1;
  // Etiqueta corta por rol para los chips del timeline (separación de roles).
  const ROLE_SHORT = {
    "swf-funcional": "SwF Func.",
    "funcional": "Consultor",
    "usuario-clave": "Usuario clave",
    "swf-desarrollador": "Desarrollo",
    "swf-tester": "QA",
  };
  const timelineHtml = allStages.length ? `
    <div class="timeline">
      ${allStages.map((e, i) => {
        const stepIdx = i + 1;
        const isCurrent = stepIdx === curIdx;
        const isDone = stepIdx < curIdx;
        let cls = isDone ? "done"
                : isCurrent ? (etapa.is_blocked ? "blocked current" : "current")
                : "pending";
        // stage técnico crudo de la etapa ACTUAL (para distinguir sub-pasos:
        // estimación vs revisión paquete; VoBo consultor vs usuario clave).
        const curStage = etapa.stage || "";
        // Etiqueta del sub-paso activo (más precisa que el genérico "Tu turno").
        const GATE_LABEL = {
          "esperando_estimacion": "Estimación",
          "diseno.esperando_revision": "Revisar paquete",
          "esperando_vobo_funcional": "VoBo Consultor",
          "esperando_vobo_usuario_clave": "VoBo Usuario clave",
          "esperando_liberacion_pruebas": "Liberar a pruebas",
          "esperando_paquete_diseno": "Paquete listo",
        };
        let sub = "";
        if (isCurrent) {
          if (etapa.is_blocked) sub = "Bloqueado";
          else if (etapa.action_pending) sub = "Tu turno" + (GATE_LABEL[curStage] ? " · " + GATE_LABEL[curStage] : "");
          else sub = etapa.running ? "Corriendo" : "En curso";
        }
        // Chips del/los rol(es) responsable(s) de ESTA etapa. Para la etapa
        // "Validación Cliente" anotamos el avance de los DOS VoBos secuenciales
        // (Consultor → Usuario clave): ✓ aprobado, 🔔 turno actual.
        const roles = e.responsable_roles || [];
        const voboStateFor = (r) => {
          if (e.key !== "validacion_cliente") return "";
          const past = curIdx > e.index;       // ya pasamos Validación Cliente
          if (r === "funcional")
            return past || curStage === "esperando_vobo_usuario_clave" ? "done"
                 : curStage === "esperando_vobo_funcional" ? "turn" : "";
          if (r === "usuario-clave")
            return past ? "done"
                 : curStage === "esperando_vobo_usuario_clave" ? "turn" : "";
          return "";
        };
        const rolesHtml = roles.length
          ? `<div class="roles">${roles.map(r => {
              const st = voboStateFor(r);
              const mark = st === "done" ? "✓ " : st === "turn" ? "🔔 " : "";
              const extra = st === "done" ? " vobo-done" : st === "turn" ? " vobo-turn" : "";
              return `<span class="rchip role-${r}${extra}">${mark}${escapeHtml(ROLE_SHORT[r] || r)}</span>`;
            }).join("")}</div>`
          : "";
        // Compuertas de decisión: 🔴 rojo SÓLIDO solo en la etapa actual con
        // turno pendiente ("decide ahora"); en pasadas/futuras, marca tenue
        // (✓ decidida / ⚪ punto de decisión) — sin doble alarma roja.
        let gateTag = "";
        if (e.gate) {
          const name = e.gate === "fabrica" ? "Decisión Fábrica" : "Decisión Cliente";
          if (isCurrent && etapa.action_pending && !etapa.is_blocked)
            gateTag = `<div class="gate-tag">🔴 ${name}</div>`;
          else if (isDone)
            gateTag = `<div class="gate-tag muted">✓ ${name}</div>`;
          else
            gateTag = `<div class="gate-tag muted">⚪ ${name}</div>`;
        }
        const respTitle = roles.length ? ` · responsable: ${roles.join(", ")}` : "";
        return `<div class="step ${cls}" title="Etapa ${stepIdx}/${allStages.length}${respTitle}">
          <div class="icon">${e.icon}</div>
          <div class="label">${escapeHtml(e.label)}</div>
          ${sub ? `<div class="sub">${sub}</div>` : ""}
          ${rolesHtml}
          ${gateTag}
        </div>`;
      }).join("")}
    </div>` : "";

  // El banner de Recibido (editar título/descripción + validar/guardar) ahora
  // vive DENTRO del primer tab (ver recibidoBannerHtml/wireRecibidoBanner en
  // renderDiseno), para liberar el encabezado y dar aire a la captura de
  // adjuntos. Ya no se inserta aquí arriba.

  // Banner de asignación (C2): muestra a quién está asignado + botón "Tomar"
  // si está sin asignar y mi rol coincide con la etapa actual.
  let assignBanner = "";
  if (d.is_mine) {
    assignBanner = `<div style="background:#e8f5e9;color:#107e3e;padding:8px 14px;border-radius:4px;font-weight:600;font-size:13px;margin-bottom:12px;">✋ Te corresponde — eres el assignee de este WP</div>`;
  } else if (d.can_take) {
    assignBanner = `<div style="background:#fff3cd;color:#856404;padding:8px 14px;border-radius:4px;font-size:13px;margin-bottom:12px;display:flex;align-items:center;gap:12px;">
      <span style="flex:1;font-weight:600">📭 En el pool de tu rol (${escapeHtml(d.my_primary_role || "")}). Tómalo para que solo tú lo trabajes.</span>
      <button id="take-wp-btn" style="background:#0a6ed1;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:600;font-size:13px">🤚 Tomar este WP</button>
    </div>`;
  } else if (d.assignee) {
    assignBanner = `<div style="background:#f5f5f5;color:#666;padding:8px 14px;border-radius:4px;font-size:13px;margin-bottom:12px;">👤 Asignado a <b>${escapeHtml(d.assignee)}</b></div>`;
  }

  // Asignación cliente (funcional + usuario clave). Editable si can_assign;
  // informativo si solo hay datos.
  const asig = d.asignacion || {};
  let asigBanner = "";
  if (d.can_assign) {
    asigBanner = `<div style="background:#f3f0fb;border:1px solid #d9cdf0;padding:8px 14px;border-radius:4px;margin-bottom:12px;font-size:13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <b style="color:#5b3da8">👥 Asignación cliente:</b>
      <label style="display:flex;align-items:center;gap:4px">Funcional <select id="asig-func" style="font-size:12px;padding:3px 6px"><option value="">(cargando…)</option></select></label>
      <label style="display:flex;align-items:center;gap:4px">Usuario clave <select id="asig-uc" style="font-size:12px;padding:3px 6px"><option value="">(cargando…)</option></select></label>
      <button id="asig-save" style="background:#5b3da8;color:#fff;border:none;padding:5px 12px;border-radius:4px;cursor:pointer;font-weight:600">💾 Guardar</button>
    </div>`;
  } else if (asig.funcional || asig.usuario_clave) {
    asigBanner = `<div style="background:#f3f0fb;border:1px solid #d9cdf0;padding:8px 14px;border-radius:4px;margin-bottom:12px;font-size:13px;"><b style="color:#5b3da8">👥</b> Funcional: <b>${escapeHtml(asig.funcional||"—")}</b> · Usuario clave: <b>${escapeHtml(asig.usuario_clave||"—")}</b></div>`;
  }

  // Prioridad (badge o selector si tiene permiso). Colores por urgencia.
  const PRIO_COLOR = {urgente:"#bb0000", alta:"#d97400", media:"#0a6ed1", baja:"#6a6d70"};
  const prioCode = d.prioridad || "media";
  const prioCat = d.prioridades || [];
  const prioLbl = (prioCat.find(p => p.codigo === prioCode) || {}).label || prioCode;
  const prioControl = (d.can_set_prioridad && prioCat.length)
    ? `<span style="font-size:12px">Prioridad: <select id="prio-sel" style="font-size:11px;font-weight:600;border:none;border-radius:10px;padding:2px 8px;color:#fff;background:${PRIO_COLOR[prioCode]||'#6a6d70'};cursor:pointer">
        ${prioCat.map(p => `<option value="${p.codigo}"${p.codigo===prioCode?' selected':''} style="background:#fff;color:#333">${escapeHtml(p.label)}</option>`).join("")}
       </select></span>`
    : `<span style="display:inline-block;font-size:11px;font-weight:600;color:#fff;background:${PRIO_COLOR[prioCode]||'#6a6d70'};border-radius:10px;padding:1px 9px">${escapeHtml(prioLbl)}</span>`;
  // Desestimar (admin/líder) / Reactivar (admin) — estado terminal 'desestimado'.
  const desestBtn = d.can_desestimar
    ? `<button id="desestimar-btn" style="font-size:11px;font-weight:600;border:1px solid #bb0000;color:#bb0000;background:#fff;border-radius:10px;padding:2px 9px;cursor:pointer">🚫 Desestimar</button>`
    : "";
  const desestBanner = d.es_desestimado
    ? `<div style="margin:8px 0;padding:10px 14px;background:#fdecea;border:1px solid #f5b7b1;border-radius:6px;color:#922b21;font-size:13px">
         🚫 <b>Requisito desestimado.</b> Está fuera del flujo y bloqueado para acciones.
         ${d.can_reactivar ? `<button id="reactivar-btn" style="margin-left:10px;font-size:11px;font-weight:600;border:1px solid #107e3e;color:#107e3e;background:#fff;border-radius:10px;padding:2px 9px;cursor:pointer">♻️ Reactivar</button>` : ""}
       </div>`
    : "";
  const html = `
    <h2>#${d.wp_id} ${escapeHtml(d.subject)}</h2>
    <div class="meta">${escapeHtml(d.project || "")} · ${escapeHtml(d.type || "")} · ${escapeHtml(d.status || "")} ${stageBadge} &nbsp; ${prioControl} &nbsp; ${desestBtn}</div>
    ${desestBanner}
    ${timelineHtml}
    ${assignBanner}
    ${asigBanner}
    <div class="external-links" style="margin: 8px 0 16px 0; display: flex; gap: 8px; flex-wrap: wrap;">
      <a href="${giteaUrl}" target="_blank" class="ext-link" title="Ver la branch req-${d.wp_id} en Gitea">📁 Gitea branch</a>
      ${tempUrl ? `<a href="${tempUrl}" target="_blank" class="ext-link" title="Ver el workflow en Temporal Web">⚙️ Temporal</a>` : ""}
      ${(d.spec_state?.has_vigente || d.test_plan_state?.has_vigente || d.proto_state?.has_vigente)
        ? `<button id="dl-pdf-btn" class="ext-link" style="cursor:pointer;border:1px solid #107e3e;background:#107e3e;color:#fff" title="Descarga spec, casos de prueba y prototipo en PDF (entregable para el consultor funcional)">📄 Descargar PDF</button>`
        : ""}
    </div>
    <div class="tabs">
      ${tabs.map((t, i) => `<div class="tab${i === 0 ? " active" : ""}" data-tab="${t.toLowerCase()}">${t}</div>`).join("")}
    </div>
    <div id="tab-content"></div>
  `;
  $detail.innerHTML = html;
  document.querySelectorAll(".tab").forEach(el => {
    el.onclick = () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      el.classList.add("active");
      showTab(el.dataset.tab);
    };
  });
  // Cambio de prioridad (proyecto-db).
  const $prioSel = document.getElementById("prio-sel");
  if ($prioSel) $prioSel.onchange = async () => {
    try {
      await api(`/api/wp/${d.wp_id}/prioridad`, { method: "POST", body: JSON.stringify({ prioridad: $prioSel.value }) });
      d.prioridad = $prioSel.value;
      $prioSel.style.background = PRIO_COLOR[$prioSel.value] || "#6a6d70";
      toast("Prioridad actualizada", "ok");
    } catch (e) { toast(e.message, "error"); }
  };
  // Desestimar (admin/líder): termina el workflow y deja 'desestimado' (bloquea).
  const $desest = document.getElementById("desestimar-btn");
  if ($desest) $desest.onclick = async () => {
    const motivo = prompt(
      `🚫 Desestimar #${d.wp_id}.\n\nLo saca del flujo y lo BLOQUEA en el inbox ` +
      "(termina el workflow). No se borra; un admin puede reactivarlo.\n\nMotivo (opcional):", "");
    if (motivo === null) return;  // canceló
    $desest.disabled = true; const o = $desest.textContent; $desest.textContent = "Desestimando…";
    try {
      await api(`/api/wp/${d.wp_id}/desestimar`, { method: "POST", body: JSON.stringify({ motivo: motivo.trim() }) });
      toast("Requisito desestimado", "ok");
      await loadInbox(); await loadDetail(d.wp_id);
    } catch (e) { toast(e.message, "error"); $desest.disabled = false; $desest.textContent = o; }
  };
  // Reactivar (admin): vuelve a Recepción.
  const $react = document.getElementById("reactivar-btn");
  if ($react) $react.onclick = async () => {
    if (!confirm(`♻️ Reactivar #${d.wp_id}: vuelve a "Recibido" y se puede re-iniciar. ¿Continuar?`)) return;
    $react.disabled = true; $react.textContent = "Reactivando…";
    try {
      await api(`/api/wp/${d.wp_id}/reactivar`, { method: "POST" });
      toast("Requisito reactivado", "ok");
      await loadInbox(); await loadDetail(d.wp_id);
    } catch (e) { toast(e.message, "error"); $react.disabled = false; }
  };
  // Asignación cliente: poblar picklists (filtrados por proyecto+capacidad+rol)
  // y guardar. Solo si can_assign.
  if (d.can_assign) {
    const fillAsig = (selEl, role, current) => {
      if (!selEl) return;
      api(`/api/wp/${d.wp_id}/asignables?role=${role}`).then(r => {
        const us = r.users || [];
        selEl.innerHTML = `<option value="">— sin asignar —</option>` +
          us.map(u => `<option value="${escapeHtml(u.username)}"${u.username === current ? " selected" : ""}>${escapeHtml(u.name || u.username)}</option>`).join("");
        if (current && !us.some(u => u.username === current))
          selEl.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)} (fuera de alcance)</option>`);
      }).catch(() => { selEl.innerHTML = `<option value="">(error al cargar)</option>`; });
    };
    fillAsig(document.getElementById("asig-func"), "funcional", asig.funcional || "");
    fillAsig(document.getElementById("asig-uc"), "usuario-clave", asig.usuario_clave || "");
    const $asigSave = document.getElementById("asig-save");
    if ($asigSave) $asigSave.onclick = async () => {
      const fv = (document.getElementById("asig-func") || {}).value || "";
      const uv = (document.getElementById("asig-uc") || {}).value || "";
      try {
        await api(`/api/wp/${d.wp_id}/asignacion`, { method: "POST", body: JSON.stringify({ funcional: fv, usuario_clave: uv }) });
        toast("Asignación guardada", "ok");
        loadDetail(d.wp_id);
      } catch (e) { toast(e.message, "error"); }
    };
  }

  // Botón "Tomar este WP" (C2) — solo aparece si can_take=true
  const $take = document.getElementById("take-wp-btn");
  if ($take) {
    $take.onclick = async () => {
      $take.disabled = true; $take.textContent = "Tomando...";
      try {
        const r = await api(`/api/wp/${d.wp_id}/take`, { method: "POST" });
        toast(`Tomado por ${r.assignee}`, "ok");
        await loadInbox();
        await loadDetail(d.wp_id);
      } catch (e) {
        toast(e.message, "error");
        $take.disabled = false; $take.textContent = "🤚 Tomar este WP";
      }
    };
  }
  // (El perfil/validar/guardar del banner de Recibido se cablean en
  // wireRecibidoBanner, dentro del primer tab.)
  // Botón "Descargar PDF" — baja spec/test-plan/prototipo (los que existan)
  // como archivos PDF separados, con nombre técnico = slug del título.
  const $dlpdf = document.getElementById("dl-pdf-btn");
  if ($dlpdf) {
    $dlpdf.onclick = async () => {
      const kinds = [];
      if (d.spec_state?.has_vigente) kinds.push("spec");
      if (d.test_plan_state?.has_vigente) kinds.push("test-plan");
      if (d.proto_state?.has_vigente) kinds.push("prototipo");
      if (!kinds.length) { toast("Aún no hay artefactos para exportar", "error"); return; }
      $dlpdf.disabled = true;
      const orig = $dlpdf.textContent;
      $dlpdf.textContent = "Generando…";
      try {
        for (const k of kinds) {
          const a = document.createElement("a");
          a.href = `/api/wp/${d.wp_id}/pdf/${k}`;
          a.download = "";
          document.body.appendChild(a);
          a.click();
          a.remove();
          await new Promise(r => setTimeout(r, 800));  // escalona las descargas
        }
        toast(`Generando ${kinds.length} PDF(s)…`, "ok");
      } finally {
        $dlpdf.disabled = false; $dlpdf.textContent = orig;
      }
    };
  }
  // Botón "Reiniciar" (admin/swf-funcional): termina el workflow y regresa el
  // requisito a Recibido para rehacer la spec desde cero (NO borra el WP).
  const $reset = document.getElementById("reset-wp-btn");
  if ($reset) {
    $reset.onclick = async () => {
      if (!confirm(`⚠️ Reiniciar el requisito #${d.wp_id}\n\nEsto TERMINA el workflow en curso y lo regresa a "Recibido". El requisito NO se borra; podrás editar la descripción/adjuntos y re-enviarlo al agente para regenerar todo desde cero. La spec/prototipo/test actuales se sobreescribirán al re-generar.\n\n¿Continuar?`)) return;
      $reset.disabled = true; const o = $reset.textContent; $reset.textContent = "Reiniciando…";
      try {
        await api(`/api/wp/${d.wp_id}/reset`, { method: "POST" });
        toast("Requisito reiniciado — vuelve a Recibido.", "ok");
        await loadInbox();
        await loadDetail(d.wp_id);
      } catch (e) {
        toast(e.message, "error");
        $reset.disabled = false; $reset.textContent = o;
      }
    };
  }
  // Botón "Descartar duplicado" (admin/swf-funcional): cierra el requisito como
  // duplicado en un paso (termina workflow + relación 'duplicates' + status
  // terminal). Pregunta opcionalmente el # del requisito original.
  const $discard = document.getElementById("discard-dup-btn");
  if ($discard) {
    $discard.onclick = async () => {
      const orig = prompt(
        `🗑️ Descartar #${d.wp_id} como DUPLICADO.\n\n` +
        "Escribe el # del requisito ORIGINAL para enlazarlos (opcional — déjalo " +
        "vacío para descartar sin enlace). Esto TERMINA el workflow y cierra el " +
        "requisito; sale del inbox activo (no se borra el WP).", "");
      if (orig === null) return;  // canceló
      const trimmed = orig.trim();
      if (trimmed && !/^\d+$/.test(trimmed)) {
        toast("El original debe ser un número (#) de requisito", "error");
        return;
      }
      const payload = trimmed ? { duplicate_of: Number(trimmed) } : {};
      $discard.disabled = true; const o = $discard.textContent; $discard.textContent = "Descartando…";
      try {
        const r = await api(`/api/wp/${d.wp_id}/discard-duplicate`,
                            { method: "POST", body: JSON.stringify(payload) });
        toast(r.status_set
          ? `Descartado como duplicado (${r.status_set}).`
          : "Descartado (workflow terminado; revisa el status en OP).", "ok");
        await loadInbox();
        await loadDetail(d.wp_id);
      } catch (e) {
        toast(e.message, "error");
        $discard.disabled = false; $discard.textContent = o;
      }
    };
  }
  showTab("info general");
}

async function showTab(tab) {
  const $tc = document.getElementById("tab-content");
  // No blanqueamos: cada renderX fija su propio esqueleto en el mismo tick, así
  // que el contenido se reemplaza sin flash. Solo atenuamos brevemente.
  setLoading($tc, true);
  requestAnimationFrame(() => setLoading($tc, false));
  const d = currentDetail;
  if (tab === "info general") renderInfoGeneral($tc, d);
  else if (tab === "diseño" || tab === "paquete funcional") renderDiseno($tc, d);
  else if (tab === "desarrollo") renderDesarrollo($tc, d);
  else if (tab === "spec") renderSpec($tc, d);
  else if (tab === "prototipo") renderPrototipo($tc, d);
  else if (tab === "plan") renderPlan($tc, d);
  else if (tab === "blog") renderBlog($tc, d);
  else if (tab === "adjuntos") renderInfoGeneral($tc, d);  // compat: adjuntos vive en Info general
}

async function renderSpec($tc, d) {
  const iters = d.iterations || [];
  if (!iters.length) {
    $tc.innerHTML = '<div class="panel"><div class="empty">No hay iteraciones de la spec todavia.</div></div>';
    return;
  }
  const selected = currentIteration || iters[iters.length - 1];
  const opts = iters.map(v => `<option value="${v}"${v === selected ? " selected" : ""}>${v}</option>`).join("");
  $tc.innerHTML = `
    <div class="panel">
      <div class="row" style="margin-bottom: 12px;">
        <label style="flex:0; white-space:nowrap;">Iteracion:</label>
        <select id="iter-sel" style="flex:0; min-width: 100px;">${opts}</select>
        <a id="iter-gitea-link" target="_blank" style="flex:0; white-space:nowrap; align-self:center;">ver en Gitea</a>
      </div>
      <div class="md-body" id="spec-content"><div class="empty">cargando...</div></div>
    </div>
    <div class="panel">
      <h3>Meta (estructurado)</h3>
      <pre id="meta-content" style="font-size:12px; max-height:300px; overflow:auto;"></pre>
    </div>
  `;
  document.getElementById("iter-sel").onchange = (e) => {
    currentIteration = e.target.value;
    showTab("spec");
  };
  await loadIteration(d, selected);
}

async function loadIteration(d, version) {
  const base = `/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/especificacion/iteraciones/${version}`;
  document.getElementById("iter-gitea-link").href =
    `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}/especificacion/iteraciones/${version}`;
  try {
    const spec = await api(`${base}/spec.md`);
    document.getElementById("spec-content").innerHTML = spec.html_render || "(vacio)";
  } catch (e) {
    document.getElementById("spec-content").innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
  try {
    const meta = await api(`${base}/meta.json`);
    document.getElementById("meta-content").textContent = JSON.stringify(meta.parsed, null, 2);
  } catch (e) {
    document.getElementById("meta-content").textContent = "(no hay meta)";
  }
}

let currentProtoIter = null;
// `sel` puede ser "vigente" (el draft actual) o "v01"/"v02"… (snapshot aprobado).
let currentDisenoSel = null;
// Sub-vista del tab "Paquete funcional": "split" (spec | prototipo) o "test"
// (panel único con el test plan). El paquete funcional incluye los TRES
// artefactos coordinados — esta variable elige qué se muestra en pantalla.
let currentPaqVista = (localStorage.getItem("paqVista") || "split");
let currentDisenoOrient = (localStorage.getItem("disenoOrient") || "horizontal");
// Tamaños del split persistidos (px del primer pane; resto = 1fr).
let currentSplitHSize = parseInt(localStorage.getItem("disenoSplitH") || "0", 10);
let currentSplitVSize = parseInt(localStorage.getItem("disenoSplitV") || "0", 10);

// Editor de la especificación (contrato): el humano edita spec.md a mano y la
// cierra. Cero alucinación; al cerrar se valida + re-extraen las AS + congela.
async function renderSpecEditor($tc, d) {
  const wpId = d.wp_id || currentWpId;
  $tc.innerHTML = '<div class="panel"><div class="empty">cargando spec…</div></div>';
  let raw = "";
  try { raw = (await api(`/api/wp/${wpId}/spec/raw`)).text; }
  catch (e) { $tc.innerHTML = `<div class="panel"><div class="empty">${escapeHtml(e.message)}</div></div>`; return; }
  $tc.innerHTML = `
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <h3 style="margin:0">✏️ Editar especificación <span style="font-weight:400;color:#888;font-size:13px">— el contrato</span></h3>
        <span style="flex:1"></span>
        <button id="spec-cancel">← Volver</button>
        <button id="spec-save" class="primary">💾 Guardar</button>
        <button id="spec-close" style="background:#107e3e;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:600" title="Valida, re-extrae las aseveraciones AS y congela el paquete (snapshot del vigente)">🔒 Cerrar especificación</button>
      </div>
      <div id="spec-toolbar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
        <button data-md="h2" title="Encabezado">H2</button>
        <button data-md="h3" title="Subencabezado">H3</button>
        <button data-md="bold" title="Negrita"><b>B</b></button>
        <button data-md="code" title="Código inline">&lt;/&gt;</button>
        <button data-md="ul" title="Lista">• Lista</button>
        <button data-md="asrow" title="Insertar una fila de aseveración">+ Fila AS</button>
        <button data-md="astable" title="Insertar tabla de aseveraciones">⊞ Tabla AS</button>
      </div>
      <div id="spec-msg" style="font-size:13px;margin-bottom:8px"></div>
      <div style="display:flex;gap:12px;align-items:stretch;height:60vh">
        <textarea id="spec-edit" spellcheck="false" style="flex:1;box-sizing:border-box;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.5;padding:10px;border:1px solid #ccc;border-radius:4px;resize:none">${escapeHtml(raw)}</textarea>
        <div class="md-body" id="spec-preview" style="flex:1;overflow:auto;border:1px solid #e0e0e0;border-radius:4px;padding:12px 16px;background:#fff"></div>
      </div>
      <p style="font-size:12px;color:#666;margin-top:6px">Editas a la izquierda (tú controlas el contrato, sin agente); el render se actualiza a la derecha. Al <b>Cerrar</b> se valida, se re-extraen las <code>AS-xx</code> de la tabla y se congela el paquete.</p>
    </div>`;
  const $msg = document.getElementById("spec-msg");
  const ta = document.getElementById("spec-edit");
  const $prev = document.getElementById("spec-preview");
  const getContent = () => ta.value;
  document.querySelectorAll("#spec-toolbar button").forEach(b => {
    b.style.cssText = "padding:4px 10px;border:1px solid #c0c0c0;background:#fff;border-radius:4px;cursor:pointer;font-size:12px";
  });
  // Preview en vivo (debounced) con el mismo renderer del app.
  let _rt = null;
  function render() {
    clearTimeout(_rt);
    _rt = setTimeout(async () => {
      try { $prev.innerHTML = (await api("/api/render-md", { method: "POST", body: JSON.stringify({ text: ta.value }) })).html; }
      catch (e) { /* mantener el último render */ }
    }, 350);
  }
  // Insertar markdown en la posición del cursor.
  function ins(before, after, ph) {
    const s = ta.selectionStart, e = ta.selectionEnd;
    const sel = ta.value.slice(s, e) || ph || "";
    ta.value = ta.value.slice(0, s) + before + sel + after + ta.value.slice(e);
    ta.focus();
    ta.selectionStart = s + before.length;
    ta.selectionEnd = s + before.length + sel.length;
    render();
  }
  const AS_HEADER = "\n| ID | Dado | Cuando | Entonces (verificable) | Regla | Datos |\n|----|------|--------|------------------------|-------|-------|\n";
  const AS_ROW = "| AS-01 | estado/contexto previo | acción/evento | resultado exacto comprobable | RN-01 | datos sin PII |\n";
  document.getElementById("spec-toolbar").onclick = (ev) => {
    const b = ev.target.closest("button[data-md]"); if (!b) return;
    const k = b.dataset.md;
    if (k === "h2") ins("\n## ", "", "Título");
    else if (k === "h3") ins("\n### ", "", "Subtítulo");
    else if (k === "bold") ins("**", "**", "texto");
    else if (k === "code") ins("`", "`", "code");
    else if (k === "ul") ins("\n- ", "", "ítem");
    else if (k === "asrow") ins(AS_ROW, "", "");
    else if (k === "astable") ins(AS_HEADER + AS_ROW, "", "");
  };
  ta.addEventListener("input", render);
  render();
  document.getElementById("spec-cancel").onclick = () => showTab("paquete funcional");
  document.getElementById("spec-save").onclick = async () => {
    try {
      const r = await api(`/api/wp/${wpId}/spec`, { method: "POST", body: JSON.stringify({ content: getContent() }) });
      $msg.innerHTML = `<span style="color:#107e3e">✓ Guardada · ${r.assertions_count} aseveración(es)</span>`
        + (r.issues && r.issues.length ? ` <span style="color:#df6e0c">· avisos: ${r.issues.map(escapeHtml).join("; ")}</span>` : "");
      toast("Spec guardada", "ok");
    } catch (e) { toast(e.message, "error"); }
  };
  document.getElementById("spec-close").onclick = async () => {
    try {
      await api(`/api/wp/${wpId}/spec`, { method: "POST", body: JSON.stringify({ content: getContent() }) });
      let r = await api(`/api/wp/${wpId}/spec/close`, { method: "POST", body: JSON.stringify({}) });
      if (!r.ok) {
        const ok = confirm("La validación encontró avisos:\n\n- " + (r.issues || []).join("\n- ") + "\n\n¿Cerrar de todos modos?");
        if (!ok) { $msg.innerHTML = `<span style="color:#df6e0c">No cerrada · avisos: ${(r.issues || []).map(escapeHtml).join("; ")}</span>`; return; }
        r = await api(`/api/wp/${wpId}/spec/close`, { method: "POST", body: JSON.stringify({ force: true }) });
      }
      toast(`Especificación cerrada (${r.assertions_count} AS congeladas).`, "ok");
      await loadInbox(); await loadDetail(wpId);
    } catch (e) { toast(e.message, "error"); }
  };
}

// Banner de la fase Recibido (D1/D2): editar título/descripción + elegir perfil
// y validar/guardar. Antes vivía SOBRE las pestañas; ahora se renderiza DENTRO
// del primer tab (renderDiseno) para liberar el encabezado y dar aire a la
// captura de adjuntos. Devuelve "" si no aplica (no es Recibido / sin permiso).
function recibidoBannerHtml(d) {
  if (d.can_validate_start) {
    const authorPart = d.author ? ` por <b>${escapeHtml(d.author)}</b>` : "";
    const lblStyle = "display:block;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#856404;margin:8px 0 3px";
    const fldStyle = "width:100%;box-sizing:border-box;font:inherit;font-size:13px;color:#333;background:#fff;border:1px solid #e0c080;border-radius:4px;padding:7px 9px";
    return `<div style="background:#fff8e1;padding:12px 14px;border-radius:4px;margin-bottom:12px;border:1px solid #f0a020;">
      <div style="font-weight:600;color:#856404;font-size:13px">📨 Requisito creado${authorPart}. <b>Revisa y enriquece</b> el insumo antes de abrir el workspace de diseño.</div>
      <label style="${lblStyle}">Título</label>
      <input id="edit-subject" type="text" style="${fldStyle}" value="${escapeHtml(d.subject)}" />
      <label style="${lblStyle}">Descripción (insumo principal del diseño — Markdown)</label>
      <textarea id="edit-description" rows="4" style="${fldStyle};resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;line-height:1.45">${escapeHtml(d.description || "")}</textarea>
      <label style="${lblStyle}">🧩 Stack / perfil objetivo (define el diseño: spec, prototipo y reglas de UI)</label>
      <select id="edit-profile" style="${fldStyle}"><option value="">(cargando perfiles…)</option></select>
      <div style="display:flex;align-items:center;gap:12px;margin-top:10px;flex-wrap:wrap">
        <button id="validate-start-btn" style="background:#107e3e;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-weight:600;font-size:13px" title="Provisiona el repo del requerimiento y abre la fase de diseño (Claude Code + MCP)">✅ Validar y abrir workspace de diseño</button>
        <button id="save-meta-btn" style="background:#fff;color:#856404;border:1px solid #f0a020;padding:8px 16px;border-radius:4px;cursor:pointer;font-weight:600;font-size:13px" title="Guarda título y descripción en el WP SIN arrancar el workflow (sigue en Recibido)">💾 Guardar cambios</button>
        <span style="font-size:12px;color:#856404">Guardar deja el requisito en Recibido; validar provisiona el repo y abre el workspace de diseño.</span>
      </div>
    </div>`;
  } else if (d.etapa && d.etapa.needs_validation && d.is_creator) {
    return `<div style="background:#e3f2fd;color:#0a6ed1;padding:8px 14px;border-radius:4px;font-size:13px;margin-bottom:12px;">📨 Tu requerimiento está <b>pendiente de validación</b> por SwF Funcional. Te avisaremos cuando se procese.</div>`;
  }
  return "";
}

// Cablea los controles del banner de Recibido (perfil + validar + guardar).
// DEBE llamarse después de inyectar recibidoBannerHtml en el DOM. Los oninput
// vuelcan lo tecleado a currentDetail (`d`) para que cambiar de pestaña y
// volver NO pierda lo escrito (se re-renderiza desde `d`).
function wireRecibidoBanner(d) {
  const subjEl = document.getElementById("edit-subject");
  const descEl = document.getElementById("edit-description");
  if (subjEl) subjEl.oninput = () => { d.subject = subjEl.value; };
  if (descEl) descEl.oninput = () => { d.description = descEl.value; };
  // Poblar el dropdown de perfil/stack (define el skill de diseño).
  const $profSel = document.getElementById("edit-profile");
  if ($profSel) {
    api("/api/profiles").then(cat => {
      const profs = cat.profiles || [];
      if (!profs.length) { $profSel.innerHTML = '<option value="">(sin catálogo de perfiles)</option>'; return; }
      $profSel.innerHTML = profs.map(p =>
        `<option value="${escapeHtml(p.key)}"${p.key === cat.default_profile ? " selected" : ""}>${escapeHtml(p.title || p.key)}</option>`
      ).join("");
    }).catch(() => { $profSel.innerHTML = '<option value="">(no se pudo cargar perfiles)</option>'; });
  }
  // Botón "Validar y enviar al agente" (D1)
  const $validate = document.getElementById("validate-start-btn");
  if ($validate) {
    $validate.onclick = async () => {
      if (!confirm("Validar el requerimiento y arrancar el workflow del agente IA? El agente generará spec + prototipo + test plan.")) return;
      const sEl = document.getElementById("edit-subject");
      const dEl = document.getElementById("edit-description");
      const profEl = document.getElementById("edit-profile");
      const payload = {};
      if (sEl) payload.edited_subject = sEl.value;
      if (dEl) payload.edited_description = dEl.value;
      if (profEl && profEl.value) payload.profile = profEl.value;
      $validate.disabled = true; $validate.textContent = "Arrancando…";
      try {
        await api(`/api/wp/${d.wp_id}/approve-and-start`, { method: "POST", body: JSON.stringify(payload) });
        toast("Validado. Workflow arrancado.", "ok");
        await loadInbox();
        await loadDetail(d.wp_id);
      } catch (e) {
        toast(e.message, "error");
        $validate.disabled = false; $validate.textContent = "✅ Validar y enviar al agente";
      }
    };
  }
  // Botón "Guardar cambios" — persiste título/descripción SIN arrancar el
  // workflow (sigue en Recibido). Solo swf-funcional/admin.
  const $saveMeta = document.getElementById("save-meta-btn");
  if ($saveMeta) {
    $saveMeta.onclick = async () => {
      const sEl = document.getElementById("edit-subject");
      const dEl = document.getElementById("edit-description");
      const payload = {};
      if (sEl) payload.subject = sEl.value;
      if (dEl) payload.description = dEl.value;
      $saveMeta.disabled = true; $saveMeta.textContent = "Guardando…";
      try {
        const r = await api(`/api/wp/${d.wp_id}/edit-meta`, { method: "POST", body: JSON.stringify(payload) });
        toast(r.changed === false ? "Sin cambios que guardar" : "Cambios guardados", "ok");
        await loadInbox();
        await loadDetail(d.wp_id);
      } catch (e) {
        toast(e.message, "error");
        $saveMeta.disabled = false; $saveMeta.textContent = "💾 Guardar cambios";
      }
    };
  }
}

async function renderDiseno($tc, d) {
  return renderArtefactos($tc, d);
}

// Visor de artefactos del paquete de diseño (rama req-N): navega md/html/pdf/
// imágenes/json/binarios y los renderiza inline. Reemplaza el visor viejo.
async function renderArtefactos($tc, d) {
  $tc.innerHTML = `<div class="panel"><div class="empty">cargando artefactos…</div></div>`;
  let files = [];
  try { files = (await api(`/api/wp/${d.wp_id}/artifacts`)).files || []; } catch (e) {}
  if (!files.length) {
    const repo = d.dev_repo || {};
    const hint = repo.clone_url
      ? `Trabaja el paquete <b>localmente con Claude Code</b> en el repo del requerimiento, escríbelo en <code>diseno/</code> (incl. <code>diseno/soporte/</code>) y publícalo por <b>git push</b> (<code>git add diseno/ && git commit && git push</code>); aparecerá aquí.${repoCloneHint(d)}`
      : `El repo del requerimiento se provisiona al <b>iniciar</b> el requisito (Blog → ▶ Iniciar); luego lo trabajas con Claude Code y publicas <code>diseno/</code> por git push.`;
    $tc.innerHTML = `<div class="panel"><div class="empty" style="text-align:left;padding:20px">📦 Aún no hay artefactos de diseño publicados.<br><br>${hint}</div></div>`;
    return;
  }
  const FOLDER_LBL = { "diseno": "📐 Diseño", "diseno/soporte": "📎 Soporte", especificacion: "📄 Especificación", prototipo: "🖼 Prototipo", "test-plan": "🧪 Casos de prueba", "diseno-soporte": "📎 Soporte", entregables: "📦 Entregables" };
  const ICON = (ext) => ({ md: "📄", html: "🖼", json: "🔧", pdf: "📕", png: "🖼", jpg: "🖼", jpeg: "🖼", gif: "🖼", svg: "🖼", webp: "🖼", xlsx: "📊", csv: "📊", docx: "📝", zip: "🗜" }[ext] || "📎");
  const groups = {};
  // Agrupado: el material de apoyo (diseno/soporte/...) va a su propio grupo; el
  // resto por carpeta de primer nivel.
  files.forEach(f => { const fp = (f.folder || ""); const top = fp.startsWith("diseno/soporte") ? "diseno/soporte" : fp.split("/")[0]; (groups[top] = groups[top] || []).push(f); });
  const fileLink = (f) => `<a href="#" class="art-file" data-path="${escapeHtml(f.path)}" data-ext="${f.ext}" data-name="${escapeHtml(f.name)}" title="${f.date ? "Modificado: " + f.date.slice(0,10) : ""}" style="display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:5px;text-decoration:none;color:#333;font-size:13px">${ICON(f.ext)} <span style="flex:1;word-break:break-all">${escapeHtml(f.name)}</span>${f.date ? `<span style="color:#8a94a6;font-size:10.5px;white-space:nowrap">${f.date.slice(0,10)}</span>` : ""}<span style="color:#aaa;font-size:11px">${f.size ? (f.size / 1024).toFixed(0) + "k" : ""}</span></a>`;
  const tree = Object.keys(groups).sort().map(g => `
    <div style="margin-bottom:8px">
      <div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;padding:4px 8px">${FOLDER_LBL[g] || g}</div>
      ${groups[g].sort((a, b) => (a.name || "").localeCompare(b.name || "")).map(fileLink).join("")}
    </div>`).join("");
  // Referencia al clone del repo SIEMPRE visible (aunque ya haya diseño/artefactos):
  // el humano puede necesitar clonar para seguir trabajando el paquete localmente.
  const _cloneBar = repoCloneHint(d);
  $tc.innerHTML = `
    ${_cloneBar ? `<div class="panel" style="padding:8px 14px;margin-bottom:10px">${_cloneBar}</div>` : ""}
    <div class="panel" style="display:flex;gap:14px;align-items:flex-start">
      <div style="flex:0 0 240px;max-height:72vh;overflow:auto;border-right:1px solid #eee;padding-right:8px">
        <h3 style="margin:0 0 8px 0;font-size:14px">📦 Artefactos</h3>${tree}
      </div>
      <div id="art-viewer" style="flex:1;min-width:0;max-height:72vh;overflow:auto"><div class="empty">Elige un artefacto.</div></div>
    </div>`;
  const $viewer = $tc.querySelector("#art-viewer");
  // Repo fuente por prefijo: el paquete de diseño (diseno/…) vive en el repo del
  // requerimiento (cliente, rama main); lo legacy en la rama req-N del repo de
  // fábrica. fileUrl va siempre al endpoint propio (que enruta por prefijo).
  const dr = d.dev_repo || {};
  const src = (path) => (path.startsWith("diseno/") && dr.org)
    ? { owner: dr.org, repo: dr.repo, branch: "main",
        web: `${dr.web_url || (d.gitea_web_url + "/" + dr.org + "/" + dr.repo)}/src/branch/main` }
    : { owner: d.artifacts_owner, repo: d.artifacts_repo, branch: d.branch,
        web: `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}` };
  const open = async (path, ext, name) => {
    const s = src(path);
    const fileUrl = `/api/wp/${d.wp_id}/file/${path}`;
    const giteaUrl = `${s.web}/${path}`;
    const bar = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap"><b style="flex:1;word-break:break-all">${escapeHtml(name)}</b><a href="${fileUrl}?dl=1" style="font-size:12px">⬇ descargar</a><a href="${giteaUrl}" target="_blank" rel="noopener" style="font-size:12px">ver en Gitea ↗</a></div>`;
    if (ext === "md") {
      $viewer.innerHTML = bar + `<div class="md-body"><div class="empty">cargando…</div></div>`;
      try { const r = await api(`/api/artifact/${s.owner}/${s.repo}/${s.branch}/${path}`); $viewer.querySelector(".md-body").innerHTML = r.html_render || "(vacío)"; }
      catch (e) { $viewer.querySelector(".md-body").innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
    } else if (ext === "html") {
      $viewer.innerHTML = bar + `<iframe src="${fileUrl}" style="width:100%;height:66vh;border:1px solid #e0e0e0;border-radius:6px;background:#fff"></iframe>`;
    } else if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) {
      $viewer.innerHTML = bar + `<img src="${fileUrl}" style="max-width:100%;border:1px solid #eee;border-radius:6px">`;
    } else if (ext === "pdf") {
      $viewer.innerHTML = bar + `<embed src="${fileUrl}" type="application/pdf" style="width:100%;height:70vh;border:1px solid #e0e0e0;border-radius:6px">`;
    } else if (ext === "json") {
      $viewer.innerHTML = bar + `<pre style="font-size:12px;background:#f7f9fc;padding:12px;border-radius:6px;overflow:auto;white-space:pre-wrap" id="art-json">cargando…</pre>`;
      try { const r = await api(`/api/artifact/${s.owner}/${s.repo}/${s.branch}/${path}`); $viewer.querySelector("#art-json").textContent = r.raw || JSON.stringify(r.parsed, null, 2); }
      catch (e) { $viewer.querySelector("#art-json").textContent = e.message; }
    } else {
      $viewer.innerHTML = bar + `<div class="empty" style="padding:20px">Sin vista previa para <code>.${escapeHtml(ext || "?")}</code>. <a href="${fileUrl}?dl=1">Descárgalo</a> o ábrelo en Gitea.</div>`;
    }
  };
  $tc.querySelectorAll(".art-file").forEach(a => a.onclick = (e) => {
    e.preventDefault();
    $tc.querySelectorAll(".art-file").forEach(x => x.style.background = "");
    a.style.background = "#eef4fc";
    open(a.dataset.path, a.dataset.ext, a.dataset.name);
  });
  const first = $tc.querySelector('.art-file[data-name="spec.md"]') || $tc.querySelector(".art-file");
  if (first) first.click();
}

// Visor de ENTREGABLES DE CÓDIGO del repo del requerimiento (org/repo del cliente,
// rama main, bajo entregables/). Mismo navegador+visor que Diseño, pero contra el
// repo de código — para revisar los entregables cuando el dev libera a Pruebas
// Funcionales. Ver [[rediseno-fase-desarrollo-repo]].
async function renderDevArtefactos($wrap, d, repo) {
  $wrap.innerHTML = `<div class="panel"><div class="empty">cargando entregables…</div></div>`;
  let files = [];
  try { files = (await api(`/api/wp/${d.wp_id}/dev-artifacts`)).files || []; } catch (e) {}
  if (!files.length) {
    $wrap.innerHTML = `<div class="panel"><div class="empty" style="text-align:left;padding:20px">📦 Aún no hay entregables de código publicados.<br><br>El dev los publica vía el MCP <code>fabrica</code> al cerrar desarrollo; cuando libere a <b>Pruebas Funcionales</b> aparecerán aquí para revisarlos.</div></div>`;
    return;
  }
  // Extensiones de texto/código que el visor muestra inline (lo demás: descarga).
  const TEXT_EXTS = new Set(["abap","js","mjs","ts","tsx","jsx","py","sql","xml","yaml","yml","sh","java","cds","json","txt","csv","log","ini","conf","properties","gitignore","cls","prog","ddls","bdef","srvd","md","html"]);
  const ICON = (ext) => ({ md:"📄", html:"🖼", json:"🔧", pdf:"📕", png:"🖼", jpg:"🖼", jpeg:"🖼", gif:"🖼", svg:"🖼", webp:"🖼", xlsx:"📊", csv:"📊", docx:"📝", zip:"🗜", abap:"📜", sql:"🗄", py:"🐍", js:"📜", ts:"📜", xml:"📑", yaml:"📑", yml:"📑" }[ext] || "📎");
  // Agrupar por versión/carpeta de primer nivel bajo entregables/.
  const groups = {};
  files.forEach(f => {
    const parts = (f.path || "").split("/");           // entregables/<grupo>/...
    const top = parts.length > 2 ? parts[1] : "entregables";
    (groups[top] = groups[top] || []).push(f);
  });
  const relName = (f) => (f.path || "").split("/").slice(2).join("/") || f.name;
  const fileLink = (f) => `<a href="#" class="dev-file" data-path="${escapeHtml(f.path)}" data-ext="${f.ext}" data-name="${escapeHtml(f.name)}" style="display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:5px;text-decoration:none;color:#333;font-size:13px">${ICON(f.ext)} <span style="flex:1;word-break:break-all">${escapeHtml(relName(f))}</span><span style="color:#aaa;font-size:11px">${f.size ? (f.size/1024).toFixed(0)+"k" : ""}</span></a>`;
  const tree = Object.keys(groups).sort().reverse().map(g => `
    <div style="margin-bottom:8px">
      <div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;padding:4px 8px">📦 ${escapeHtml(g)}</div>
      ${groups[g].sort((a,b) => (a.path||"").localeCompare(b.path||"")).map(fileLink).join("")}
    </div>`).join("");
  $wrap.innerHTML = `
    <div class="panel" style="display:flex;gap:14px;align-items:flex-start">
      <div style="flex:0 0 240px;max-height:72vh;overflow:auto;border-right:1px solid #eee;padding-right:8px">
        <h3 style="margin:0 0 8px 0;font-size:14px">📦 Entregables de código</h3>${tree}
      </div>
      <div id="dev-viewer" style="flex:1;min-width:0;max-height:72vh;overflow:auto"><div class="empty">Elige un entregable.</div></div>
    </div>`;
  const $viewer = $wrap.querySelector("#dev-viewer");
  const open = async (path, ext, name) => {
    const fileUrl = `/api/wp/${d.wp_id}/dev-file/${path}`;
    const giteaUrl = `${repo.web_url}/src/branch/main/${path}`;
    const bar = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap"><b style="flex:1;word-break:break-all">${escapeHtml(name)}</b><a href="${fileUrl}?dl=1" style="font-size:12px">⬇ descargar</a><a href="${giteaUrl}" target="_blank" rel="noopener" style="font-size:12px">ver en Gitea ↗</a></div>`;
    if (ext === "md") {
      $viewer.innerHTML = bar + `<div class="md-body"><div class="empty">cargando…</div></div>`;
      try { const r = await api(`/api/artifact/${repo.org}/${repo.repo}/main/${path}`); $viewer.querySelector(".md-body").innerHTML = r.html_render || "(vacío)"; }
      catch (e) { $viewer.querySelector(".md-body").innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
    } else if (ext === "html") {
      $viewer.innerHTML = bar + `<iframe src="${fileUrl}" style="width:100%;height:66vh;border:1px solid #e0e0e0;border-radius:6px;background:#fff"></iframe>`;
    } else if (["png","jpg","jpeg","gif","svg","webp"].includes(ext)) {
      $viewer.innerHTML = bar + `<img src="${fileUrl}" style="max-width:100%;border:1px solid #eee;border-radius:6px">`;
    } else if (ext === "pdf") {
      $viewer.innerHTML = bar + `<embed src="${fileUrl}" type="application/pdf" style="width:100%;height:70vh;border:1px solid #e0e0e0;border-radius:6px">`;
    } else if (TEXT_EXTS.has(ext) || ext === "") {
      $viewer.innerHTML = bar + `<pre style="font-size:12px;background:#1e1e1e;color:#e6e6e6;padding:12px;border-radius:6px;overflow:auto" id="dev-code">cargando…</pre>`;
      try { const t = await (await fetch(fileUrl)).text(); const el = $viewer.querySelector("#dev-code"); el.textContent = t || "(vacío)"; }
      catch (e) { $viewer.querySelector("#dev-code").textContent = e.message; }
    } else {
      $viewer.innerHTML = bar + `<div class="empty" style="padding:20px">Sin vista previa para <code>.${escapeHtml(ext || "?")}</code>. <a href="${fileUrl}?dl=1">Descárgalo</a> o ábrelo en Gitea.</div>`;
    }
  };
  $wrap.querySelectorAll(".dev-file").forEach(a => a.onclick = (e) => {
    e.preventDefault();
    $wrap.querySelectorAll(".dev-file").forEach(x => x.style.background = "");
    a.style.background = "#eef4fc";
    open(a.dataset.path, a.dataset.ext, a.dataset.name);
  });
  const first = $wrap.querySelector(".dev-file");
  if (first) first.click();
}

async function renderDisenoLegacy($tc, d) {
  // Modelo nuevo de versionado: el "vigente" se sobreescribe en cada iter;
  // las "versiones" son snapshots inmutables creados al aprobar. El bloque
  // 'diseno' produce 3 artefactos coordinados (spec + prototipo + test plan)
  // que se aprueban juntos como una unidad — el "paquete funcional".
  const specState = d.spec_state || {};
  const versiones = specState.versiones || [];
  const meta = specState.meta || {};
  const hayVigente = !!specState.has_vigente;
  // Test plan state (mismo modelo de versionado). Si no existe es que el
  // requisito venía del pipeline v3; el agente lo creará en la próxima iter.
  const tpState = d.test_plan_state || {};
  const hayTestPlan = !!tpState.has_vigente;

  if (!hayVigente && !versiones.length) {
    // Modelo nuevo (git-native): el paquete lo produce el humano localmente con
    // Claude Code en el repo del requerimiento, en `diseno/`, y lo publica por
    // git push. Ver [[rediseno-fase-diseno-mcp]]. Si el repo ya está
    // provisionado mostramos cómo clonarlo; si no, informamos que falta validar.
    const repo = d.dev_repo || {};
    const cuerpo = repo.clone_url
      ? `<div class="empty" style="padding:24px;text-align:left">📦 El paquete de diseño (spec + prototipo + casos + soporte) aún no se ha publicado.<br><br>
           Trabájalo <b>localmente con Claude Code</b> en el repo del requerimiento, escríbelo en <code>diseno/</code> y publícalo por <b>git push</b>; aparecerá aquí.
           ${repoCloneHint(d)}</div>`
      : `<div class="empty">El paquete de diseño aún no se generó. El repo del requerimiento se provisiona al <b>validar</b> el requisito en <b>Info general</b>; luego lo trabajas con Claude Code y publicas <code>diseno/</code> por git push.</div>`;
    $tc.innerHTML = `<div class="panel">${cuerpo}</div>`;
    return;
  }

  // Opciones del selector: "Vigente (label según status)" + cada versión aprobada
  const opciones = [];
  if (hayVigente) {
    let label;
    if (meta.status === "approved") {
      label = `Vigente · aprobado ${meta.version_actual || ""}`.trim();
    } else if (meta.status === "in_change") {
      label = `Vigente · en cambio (${meta.cr_actual || "CR"})`;
    } else {
      label = `Vigente · borrador · iter ${meta.iter_actual || "?"}`;
    }
    opciones.push({value: "vigente", label});
  }
  for (const v of versiones) {
    const por = v.aprobado_por ? ` por ${v.aprobado_por}` : "";
    opciones.push({value: v.version, label: `${v.version} (aprobada${por})`});
  }

  // Default: vigente si está; sino la última aprobada
  if (!currentDisenoSel || !opciones.find(o => o.value === currentDisenoSel)) {
    currentDisenoSel = hayVigente ? "vigente" : versiones[versiones.length - 1].version;
  }
  const sel = currentDisenoSel;
  const isVigente = sel === "vigente";
  const isApprovedVigente = isVigente && meta.status === "approved";
  const canAnnotate = isVigente && meta.status !== "approved";
  const vista = currentPaqVista === "test" ? "test" : "split";

  // Paths según la selección y vista
  const specPath = isVigente ? "especificacion/spec.md"
                              : `especificacion/versiones/${sel}/spec.md`;
  const protoPath = isVigente ? "prototipo/prototipo.html"
                              : `prototipo/versiones/${sel}/prototipo.html`;
  const tpPath = isVigente ? "test-plan/test-plan.md"
                            : `test-plan/versiones/${sel}/test-plan.md`;
  const specUrl = `/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/${specPath}`;
  const protoUrl = `/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/${protoPath}`;
  const tpUrl = `/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/${tpPath}`;
  const giteaBase = `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}`;
  const giteaUrl = isVigente ? `${giteaBase}/especificacion` : `${giteaBase}/especificacion/versiones/${sel}`;

  // Banner de status (visible cuando estamos viendo el vigente)
  let statusBanner = "";
  if (isVigente) {
    let bg, fg, txt;
    if (meta.status === "approved") {
      bg = "#e8f5e9"; fg = "#107e3e";
      txt = `✅ Paquete funcional aprobado · ${meta.version_actual || ""} por ${meta.aprobado_por || "—"} (spec + prototipo + test plan congelados como ${meta.version_actual || "v01"})`;
    } else if (meta.status === "in_change") {
      bg = "#fff8e1"; fg = "#df6e0c";
      const motivo = meta.cr_motivo ? ` — ${meta.cr_motivo}` : "";
      txt = `🔧 En cambio · ${meta.cr_actual || "CR"}${motivo}`;
    } else {
      bg = "#e3f2fd"; fg = "#0a6ed1";
      const mod = meta.ultimo_modelo ? ` · ${meta.ultimo_modelo}` : "";
      const tpMissing = !hayTestPlan ? " · ⚠ test plan no generado aún (¿requisito viejo del pipeline v3?)" : "";
      txt = `📝 Borrador · iter ${meta.iter_actual || "?"}${mod}${tpMissing}`;
    }
    statusBanner = `<div style="background:${bg};color:${fg};padding:8px 14px;border-radius:4px;font-weight:600;font-size:13px;">${escapeHtml(txt)}</div>`;
  } else {
    statusBanner = `<div style="background:#eee;color:#555;padding:8px 14px;border-radius:4px;font-weight:600;font-size:13px;">📚 Viendo snapshot inmutable · ${escapeHtml(sel)} (solo lectura)</div>`;
  }

  // Barra inferior según estado
  let actionBar;
  if (canAnnotate) {
    actionBar = `
      <div class="ann-list-bar" id="ann-bar">
        <span class="count" id="ann-count">0</span> anotaciones pendientes sobre el paquete vigente
        <span class="spacer"></span>
        <button id="inputs-btn" title="Abre los documentos de entrada (lo que entró al requisito) para comparar contra la spec" style="border:1px solid #0a6ed1;color:#0a6ed1;background:#fff;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:600">📥 Documentos de entrada</button>
        <button id="ann-global-btn">+ Comentario general</button>
        <button id="ann-view-btn">Ver / editar lista</button>
        <button id="ann-clear-btn" title="Archiva las anotaciones a anotaciones-descartadas/ y limpia las pendientes (no se pierden — quedan en Gitea)" style="color:#856404">🧹 Limpiar</button>
        <button id="ann-synth-btn" class="primary" title="Regenera SOLO la especificación (el contrato) con tu feedback. El prototipo y los casos NO se tocan — regéneralos aparte con ♻️ cuando quieras.">🧠 Iterar spec (feedback)</button>
        <button id="spec-edit-btn" title="Edita la spec a mano (sin agente) y ciérrala. Ideal para ajustes finos sin que el modelo cambie lo que ya está bien." style="border:1px solid #0a6ed1;color:#0a6ed1;background:#fff;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:600">✏️ Editar / cerrar spec</button>
        <button id="regen-tp-btn" title="Regenera el test-plan desde la spec vigente (NO toca la spec)" style="border:1px solid #6b4fbb;color:#6b4fbb;background:#fff;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:600">♻️ Test-plan</button>
        <button id="regen-proto-btn" title="Regenera el prototipo desde la spec vigente (NO toca la spec)" style="border:1px solid #6b4fbb;color:#6b4fbb;background:#fff;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:600">♻️ Prototipo</button>
        <button id="afinar-proto-btn" title="Afina el prototipo VIGENTE con tus comentarios (el agente lo refina, NO lo rehace y NO toca la spec)" style="border:1px solid #6b4fbb;color:#6b4fbb;background:#fff;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:600">✏️ Afinar prototipo</button>
        <button id="ann-approve-btn" style="background:#107e3e;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:600" title="Aprueba spec + prototipo + test plan juntos como snapshot inmutable v01">✅ Aprobar paquete funcional</button>
      </div>`;
  } else if (isApprovedVigente) {
    actionBar = `<div class="ann-list-bar" style="background:#e8f5e9;color:#107e3e;justify-content:center;">✅ Paquete funcional aprobado — pasa al gate del cliente. Para reabrirlo, solicita un CR (Sprint B).</div>`;
  } else {
    actionBar = `<div class="ann-list-bar" style="background:#f5f5f5;color:#666;justify-content:center;">Versión aprobada (solo lectura). Para cambios, regresa al vigente y abre un CR.</div>`;
  }

  // Sub-toggle de vista del paquete funcional
  const vistaToggle = `
    <div class="split-toggle" id="paq-vista">
      <button data-vista="split" class="${vista === "split" ? "active" : ""}" title="Spec + prototipo lado a lado">⬛ Spec + Prototipo</button>
      <button data-vista="test" class="${vista === "test" ? "active" : ""}" title="Plan de pruebas funcional">📋 Test Plan</button>
    </div>`;

  // Cuerpo según vista
  let bodyHtml;
  if (vista === "split") {
    bodyHtml = `
      <div class="diseno-split ${currentDisenoOrient}" id="diseno-split">
        <div class="pane">
          <h3 style="font-size:12px;color:#555;margin-bottom:8px">Especificación · ${escapeHtml(sel)} ${canAnnotate ? '<span style="color:#888;font-weight:400">— selecciona texto para comentar</span>' : ''}</h3>
          <div class="md-body" id="diseno-spec" data-ann-target="spec"><div class="empty">cargando...</div></div>
        </div>
        <div class="divider" id="diseno-divider" title="Arrastra para redimensionar"></div>
        <div class="pane" style="padding:0">
          ${(isVigente && !((d.proto_state || {}).has_vigente))
            ? `<div class="empty" style="padding:28px;text-align:center;color:#666;line-height:1.7">🖼️ <b>Prototipo aún no generado.</b><br>Primero estabiliza la especificación (izquierda) y luego usa <b>♻️ Prototipo</b> para generarlo desde el contrato.</div>`
            : `<iframe class="prototipo" id="diseno-iframe" src="${protoUrl}" sandbox="allow-scripts"></iframe>`}
        </div>
      </div>`;
  } else {
    bodyHtml = `
      <div class="panel" style="min-height:60vh">
        <h3 style="font-size:12px;color:#555;margin-bottom:8px">Plan de Pruebas · ${escapeHtml(sel)} ${canAnnotate ? '<span style="color:#888;font-weight:400">— selecciona texto para comentar</span>' : ''}</h3>
        <div class="md-body" id="diseno-testplan" data-ann-target="test-plan"><div class="empty">cargando...</div></div>
      </div>`;
  }

  // Toolbar: en vista split incluye orient + reset; en vista test solo el toggle.
  const orientControls = (vista === "split") ? `
        <div class="split-toggle" id="diseno-orient">
          <button data-orient="horizontal" class="${currentDisenoOrient === "horizontal" ? "active" : ""}">⬛ Lado a lado</button>
          <button data-orient="vertical" class="${currentDisenoOrient === "vertical" ? "active" : ""}">▤ Apilado</button>
        </div>
        <button class="split-toggle" id="diseno-reset" style="padding:4px 10px;font-size:12px;border:1px solid #c0c0c0;border-radius:4px;background:#fff;cursor:pointer">⟲ 50/50</button>` : "";
  // Para los .md, link directo al renderer de Gitea (que ya renderiza
  // Markdown con tablas, code blocks, etc.). El endpoint /api/artifact
  // sirve JSON {text, html_render}, útil para JS pero feo si lo abre el
  // usuario directo. Para .html sí usamos /api/artifact (sirve raw).
  const specGiteaUrl = isVigente
    ? `${giteaBase}/especificacion/spec.md`
    : `${giteaBase}/especificacion/versiones/${sel}/spec.md`;
  const tpGiteaUrl = isVigente
    ? `${giteaBase}/test-plan/test-plan.md`
    : `${giteaBase}/test-plan/versiones/${sel}/test-plan.md`;
  const externalLinks = (vista === "split")
    ? `<a href="${specGiteaUrl}" target="_blank" title="spec.md renderizada en Gitea">spec.md</a>
       <a href="${protoUrl}" target="_blank" title="Prototipo HTML interactivo">prototipo.html</a>
       <a href="${giteaUrl}" target="_blank" title="Carpeta en Gitea">📁 Gitea</a>`
    : `<a href="${tpGiteaUrl}" target="_blank" title="test-plan.md renderizado en Gitea (tablas)">test-plan.md</a>
       <a href="${giteaBase}/test-plan" target="_blank" title="Carpeta test-plan/ en Gitea">📁 Gitea</a>`;

  $tc.innerHTML = `
    <div class="panel" style="padding:10px 14px;">${statusBanner}</div>
    <div class="panel" style="padding-bottom:6px">
      <div class="split-toolbar">
        <label>Ver</label>
        <select id="diseno-sel">
          ${opciones.map(o => `<option value="${o.value}" ${o.value === sel ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}
        </select>
        ${vistaToggle}
        ${externalLinks}
        <span style="flex:1"></span>
        ${orientControls}
      </div>
    </div>
    ${bodyHtml}
    ${actionBar}`;

  // Handlers comunes
  document.getElementById("diseno-sel").onchange = (e) => {
    currentDisenoSel = e.target.value;
    showTab("paquete funcional");
  };
  document.querySelectorAll("#paq-vista button").forEach(btn => {
    btn.onclick = () => {
      currentPaqVista = btn.dataset.vista;
      localStorage.setItem("paqVista", currentPaqVista);
      showTab("paquete funcional");
    };
  });

  // Handlers solo en vista split
  if (vista === "split") {
    const splitEl = document.getElementById("diseno-split");
    applySplitSize(splitEl, currentDisenoOrient);
    document.querySelectorAll("#diseno-orient button").forEach(btn => {
      btn.onclick = () => {
        currentDisenoOrient = btn.dataset.orient;
        localStorage.setItem("disenoOrient", currentDisenoOrient);
        showTab("paquete funcional");
      };
    });
    document.getElementById("diseno-reset").onclick = () => {
      if (currentDisenoOrient === "horizontal") { currentSplitHSize = 0; localStorage.removeItem("disenoSplitH"); }
      else { currentSplitVSize = 0; localStorage.removeItem("disenoSplitV"); }
      applySplitSize(splitEl, currentDisenoOrient);
    };
    setupDividerDrag(splitEl, currentDisenoOrient);
  }

  // Anotaciones (path único: especificacion/anotaciones-pendientes.json).
  // Cubren los tres artefactos via data-ann-target (spec | prototipo | test-plan).
  if (canAnnotate) {
    setupAnnotations(d.wp_id || currentWpId, "vigente");
    const $specEdit = document.getElementById("spec-edit-btn");
    if ($specEdit) $specEdit.onclick = () => renderSpecEditor($tc, d);
    // instructions: si viene, AFINA el artefacto vigente con esos comentarios
    // (el agente lo refina, no lo rehace). Sin instructions = regen limpia.
    const regen = async (kind, btn, label, instructions) => {
      const afinar = !!instructions;
      if (!afinar && !confirm(`¿Regenerar ${label} desde la especificación vigente? El agente lo deriva del contrato; NO toca la spec. Puede tardar ~30-60s.`)) return;
      const orig = btn.textContent; btn.disabled = true; btn.textContent = afinar ? "Afinando…" : "Regenerando…";
      try {
        const body = afinar ? JSON.stringify({ instructions }) : undefined;
        const r = await api(`/api/wp/${d.wp_id || currentWpId}/regenerate/${kind}`, { method: "POST", body });
        if (r.ok === false && r.blocked) {
          toast("El agente pidió aclaraciones: " + (r.open_questions || []).join(" / "), "error");
        } else {
          const m = r.model ? ` · ${r.model}` : "";
          toast(`✅ ${label} ${afinar ? "afinado" : "regenerado"}${m}${r.summary ? " — " + r.summary : ""}`, "ok");
          // Recargar el detalle del servidor: el artefacto recién generado
          // (prototipo/test-plan) no está en el `d` cacheado (se cargó antes de
          // generar). Sin esto, showTab re-pinta el estado viejo ("aún no
          // generado") aunque el archivo ya esté en Gitea.
          await loadDetail(d.wp_id || currentWpId);
          showTab("paquete funcional");
        }
      } catch (e) { toast(e.message, "error"); }
      finally { btn.disabled = false; btn.textContent = orig; }
    };
    const $rtp = document.getElementById("regen-tp-btn");
    if ($rtp) $rtp.onclick = () => regen("test-plan", $rtp, "test-plan");
    const $rp = document.getElementById("regen-proto-btn");
    if ($rp) $rp.onclick = () => regen("prototipo", $rp, "prototipo");
    // Afinar prototipo con comentarios (refina el vigente, no toca la spec).
    const $afp = document.getElementById("afinar-proto-btn");
    if ($afp) $afp.onclick = () => {
      const c = prompt("✏️ Comentarios para afinar el prototipo (el agente refina el actual, NO toca la spec).\n\nEj: «usa tabla en vez de tarjetas», «agrega filtro por fecha», «mueve Guardar arriba a la derecha».");
      if (c === null) return;
      const t = c.trim();
      if (!t) { toast("Escribe algún comentario", "error"); return; }
      regen("prototipo", $afp, "prototipo", t);
    };
  }

  // Carga del contenido según vista
  if (vista === "split") {
    // Spec
    try {
      const md = await api(specUrl);
      document.getElementById("diseno-spec").innerHTML = md.html_render || "(vacio)";
    } catch (e) {
      document.getElementById("diseno-spec").innerHTML = `<div class="empty">No se pudo cargar ${escapeHtml(specPath)}.<br>${escapeHtml(e.message)}</div>`;
    }
    // Prototipo (probe GET; FastAPI no acepta HEAD)
    try {
      const r = await fetch(protoUrl, { method: "GET" });
      if (!r.ok || (r.headers.get("Content-Length") || "1") === "0") throw new Error("HTTP " + r.status);
    } catch (e) {
      const iframe = document.getElementById("diseno-iframe");
      if (iframe) {
        iframe.outerHTML = '<div class="empty" style="padding:30px;text-align:center;background:#fff8e1;border:1px solid #f0d060;border-radius:4px;height:100%">No se encontró <code>' + escapeHtml(protoPath) + '</code>.<br><br>Probable causa: el modelo agotó su cap de output_tokens antes del HTML.<br>Itera promoviendo el modelo: en Acciones → action=<b>iterate</b>, model=<b>claude-sonnet-4-6</b> (cap 64k) u <b>opus</b> (cap 32k).</div>';
      }
    }
  } else {
    // Test plan
    try {
      const md = await api(tpUrl);
      document.getElementById("diseno-testplan").innerHTML = md.html_render || "(vacio)";
    } catch (e) {
      document.getElementById("diseno-testplan").innerHTML = `<div class="empty">No se generó <code>${escapeHtml(tpPath)}</code> todavía.<br>${escapeHtml(e.message)}<br><br>Si es un requisito viejo del pipeline v3, itera una vez con feedback para que el agente lo genere.</div>`;
    }
  }
}

// Render del historial de aprendizaje: el .md APPEND-ONLY que acumula
// prompts + feedback + cambios de cada iteración del agente. Sirve para
// fine-tune del prompt + auditoría. Ver [[versionado-artefactos]].
async function renderAprendizaje($tc, d) {
  const url = `/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/especificacion/historial-aprendizaje.md`;
  const giteaUrl = `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}/especificacion/historial-aprendizaje.md`;
  $tc.innerHTML = `
    <div class="panel">
      <div class="row" style="margin-bottom:10px;align-items:center;">
        <h3 style="margin:0;flex:1">📚 Historial de aprendizaje · especificacion</h3>
        <a href="${giteaUrl}" target="_blank" style="font-size:12px">ver en Gitea</a>
      </div>
      <div style="color:#666;font-size:12px;margin-bottom:14px">
        APPEND-ONLY: cada iteración del agente agrega prompt + feedback recibido + cambio resultante.
        Sobrevive a las versiones (acumula a través de v01, v02…). Sirve para fine-tune del prompt + auditoría.
      </div>
      <div class="md-body" id="aprendizaje-content"><div class="empty">cargando...</div></div>
    </div>`;
  try {
    const md = await api(url);
    document.getElementById("aprendizaje-content").innerHTML = md.html_render || "(vacio)";
  } catch (e) {
    document.getElementById("aprendizaje-content").innerHTML = `<div class="empty">Aún no hay entradas de historial. La primera iteración del agente lo creará.</div>`;
  }
}

function applySplitSize(splitEl, orient) {
  if (!splitEl) return;
  if (orient === "horizontal") {
    if (currentSplitHSize > 0) {
      splitEl.style.setProperty("--split-left", currentSplitHSize + "px");
      splitEl.style.setProperty("--split-right", "1fr");
    } else {
      splitEl.style.setProperty("--split-left", "1fr");
      splitEl.style.setProperty("--split-right", "1fr");
    }
  } else {
    if (currentSplitVSize > 0) {
      splitEl.style.setProperty("--split-top", currentSplitVSize + "px");
      splitEl.style.setProperty("--split-bottom", "1fr");
    } else {
      splitEl.style.setProperty("--split-top", "1fr");
      splitEl.style.setProperty("--split-bottom", "1fr");
    }
  }
}

function setupDividerDrag(splitEl, orient) {
  const divider = splitEl.querySelector(".divider");
  if (!divider) return;
  divider.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const rect = splitEl.getBoundingClientRect();
    const isH = orient === "horizontal";
    const startCoord = isH ? e.clientX : e.clientY;
    const startBase = isH ? rect.left : rect.top;
    const totalSize = isH ? rect.width : rect.height;
    document.body.style.cursor = isH ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
    // Disable iframe pointer events durante el drag para que no se trague el mousemove
    const iframe = splitEl.querySelector("iframe");
    if (iframe) iframe.style.pointerEvents = "none";
    function onMove(ev) {
      const curr = isH ? ev.clientX : ev.clientY;
      let newSize = curr - startBase;
      const minSize = 150, maxSize = totalSize - 150;
      if (newSize < minSize) newSize = minSize;
      if (newSize > maxSize) newSize = maxSize;
      if (isH) {
        currentSplitHSize = newSize;
        splitEl.style.setProperty("--split-left", newSize + "px");
        splitEl.style.setProperty("--split-right", "1fr");
      } else {
        currentSplitVSize = newSize;
        splitEl.style.setProperty("--split-top", newSize + "px");
        splitEl.style.setProperty("--split-bottom", "1fr");
      }
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (iframe) iframe.style.pointerEvents = "";
      if (isH) localStorage.setItem("disenoSplitH", String(currentSplitHSize));
      else     localStorage.setItem("disenoSplitV", String(currentSplitVSize));
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

async function renderPrototipo($tc, d) {
  const iters = d.prototipo_iterations || [];
  const hasVigente = d.has_prototipo_html || d.has_prototipo_md;
  if (!iters.length && !hasVigente) {
    $tc.innerHTML = '<div class="panel"><div class="empty">No hay prototipo aun.</div></div>';
    return;
  }

  // Opciones: cada iteracion + "vigente" si existe
  const options = [];
  for (const v of iters) options.push({ value: `iter:${v}`, label: `${v} (iteracion)` });
  if (hasVigente) options.push({ value: "vigente", label: "vigente (ultimo aprobado)" });
  // Default: si hay iteraciones, ultima; si no, vigente
  const defaultVal = iters.length ? `iter:${iters[iters.length - 1]}` : "vigente";
  if (!currentProtoIter || !options.find(o => o.value === currentProtoIter)) {
    currentProtoIter = defaultVal;
  }

  $tc.innerHTML = `
    <div class="panel">
      <div class="iter-row">
        <label>Version</label>
        <select id="proto-sel">
          ${options.map(o => `<option value="${o.value}" ${o.value === currentProtoIter ? "selected" : ""}>${o.label}</option>`).join("")}
        </select>
        <a id="proto-gitea-link" target="_blank">ver en Gitea</a>
      </div>
    </div>
    <div class="panel">
      <h3>Descripcion (prototipo.md)</h3>
      <div class="md-body" id="proto-md"><div class="empty">cargando...</div></div>
    </div>
    <div class="panel">
      <h3>Prototipo navegable (iframe sandboxed)</h3>
      <iframe class="prototipo" id="proto-iframe" src="about:blank" sandbox="allow-scripts"></iframe>
      <div style="margin-top: 8px;">
        <a id="proto-open" target="_blank">abrir en pestana nueva</a>
      </div>
    </div>`;
  document.getElementById("proto-sel").onchange = (e) => {
    currentProtoIter = e.target.value;
    loadProto(d, currentProtoIter);
  };
  await loadProto(d, currentProtoIter);
}

async function loadProto(d, sel) {
  // sel: "iter:vNN" o "vigente"
  let basePath, giteaPath;
  if (sel.startsWith("iter:")) {
    const v = sel.slice(5);
    basePath = `prototipo/iteraciones/${v}`;
    giteaPath = `prototipo/iteraciones/${v}`;
  } else {
    basePath = `prototipo`;
    giteaPath = `prototipo`;
  }
  const htmlUrl = `/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/${basePath}/prototipo.html`;
  document.getElementById("proto-iframe").src = htmlUrl;
  document.getElementById("proto-open").href = htmlUrl;
  document.getElementById("proto-gitea-link").href =
    `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}/${giteaPath}`;
  try {
    const md = await api(`/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/${basePath}/prototipo.md`);
    document.getElementById("proto-md").innerHTML = md.html_render || "(vacio)";
  } catch (e) {
    document.getElementById("proto-md").innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

// ============================================================
// Pestaña Plan — estimación persistida en Gitea + 3 modos (INITIAL/VIEW/EDIT)
// ============================================================
let _planCache = {};      // por wp_id: plan en MEMORIA (durante EDIT)
let _planOriginal = {};   // por wp_id: snapshot al entrar a EDIT (para Cancelar)
let _planMode = {};       // por wp_id: "loading" | "initial" | "view" | "edit"
let _planPersisted = {};  // por wp_id: plan persistido (lo que vino del GET)
let _planIterations = {}; // por wp_id: lista de versiones existentes en Gitea

async function renderPlan($tc, d) {
  const roles = (currentUser && currentUser.roles) || [];
  const canEstimate = roles.includes("swf-funcional") || roles.includes("admin")
    || roles.includes("project-manager") || roles.includes("swf-project-manager");

  // Si ya estamos en EDIT, mantener el state.
  if (_planMode[d.wp_id] === "edit" && _planCache[d.wp_id]) {
    renderPlanEditable($tc, d, _planCache[d.wp_id], canEstimate);
    return;
  }

  // Si no hay nada cargado todavía, hacer GET al backend.
  if (_planMode[d.wp_id] === undefined) {
    _planMode[d.wp_id] = "loading";
    $tc.innerHTML = '<div class="panel"><div class="empty">cargando plan persistido...</div></div>';
    try {
      const r = await api(`/api/wp/${d.wp_id}/plan`);
      _planIterations[d.wp_id] = r.iterations || [];
      if (r.status === "ok" && r.plan) {
        _planPersisted[d.wp_id] = r.plan;
        _planMode[d.wp_id] = "view";
      } else {
        _planMode[d.wp_id] = "initial";
      }
    } catch (e) {
      _planMode[d.wp_id] = "initial";
    }
    showTab("plan");  // re-render con el modo ya resuelto
    return;
  }

  // INITIAL — no hay plan persistido
  if (_planMode[d.wp_id] === "initial") {
    const iters = _planIterations[d.wp_id] || [];
    $tc.innerHTML = `
      <div class="panel">
        <h3>📐 Plan / Estimación</h3>
        <p>No hay plan vigente para este Requisito. Genéralo con el agente IA: lee spec.md + prototipo.html + plantilla + metodología.</p>
        ${iters.length ? `<p style="font-size:12px;color:#666">Hay ${iters.length} versión(es) histórica(s) en Gitea: ${iters.join(", ")}</p>` : ""}
        ${canEstimate
          ? `<button id="plan-estimar-btn" class="primary" style="background:#0a6ed1;color:#fff;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;font-size:14px">🧮 Generar estimación con IA</button>
             <div style="margin-top:8px;font-size:12px;color:#666">Sonnet, ~10-20 segundos. Después de generar, podrás editarla y guardarla.</div>`
          : `<div class="empty" style="background:#fff8e1;border:1px solid #f0d060;padding:12px;border-radius:4px">Solo <b>swf</b> o <b>admin</b> pueden estimar.</div>`
        }
      </div>`;
    if (canEstimate) document.getElementById("plan-estimar-btn").onclick = () => runEstimate(d);
    return;
  }

  // VIEW — plan persistido, mostrar read-only
  if (_planMode[d.wp_id] === "view") {
    renderPlanView($tc, d, _planPersisted[d.wp_id], canEstimate);
    return;
  }
}

let _estimateInFlight = false;
async function runEstimate(d) {
  if (_estimateInFlight) { toast("Ya hay una estimación en curso…", "error"); return; }
  _estimateInFlight = true;
  const btn = document.getElementById("plan-estimar-btn") || document.getElementById("plan-regen-btn");
  if (btn) { btn.disabled = true; btn.textContent = "🧠 Estimando con IA…"; }
  try {
    const plan = await api(`/api/wp/${d.wp_id}/estimate`, { method: "POST" });
    // Persistir automáticamente (queda en VIEW listo para editar si quieres).
    const saved = await api(`/api/wp/${d.wp_id}/plan`, {
      method: "POST",
      body: JSON.stringify(plan),
    });
    _planPersisted[d.wp_id] = saved.plan;
    _planIterations[d.wp_id] = (_planIterations[d.wp_id] || []).concat([`v${String(saved.version).padStart(2,"0")}`]);
    _planMode[d.wp_id] = "view";
    delete _planCache[d.wp_id];
    delete _planOriginal[d.wp_id];
    showTab("plan");
    toast(`Estimación v${saved.version} guardada en Gitea`, "ok");
  } catch (err) {
    toast("Error: " + err.message, "error");
    if (btn) { btn.disabled = false; btn.textContent = "🧮 Generar estimación con IA"; }
  } finally {
    _estimateInFlight = false;
  }
}

function planEnterEdit(d) {
  _planMode[d.wp_id] = "edit";
  _planCache[d.wp_id] = JSON.parse(JSON.stringify(_planPersisted[d.wp_id]));
  _planOriginal[d.wp_id] = JSON.parse(JSON.stringify(_planPersisted[d.wp_id]));
  showTab("plan");
}

function planCancelEdit(d) {
  if (planIsDirty(d.wp_id) && !confirm("¿Descartar cambios?")) return;
  _planMode[d.wp_id] = "view";
  delete _planCache[d.wp_id];
  delete _planOriginal[d.wp_id];
  showTab("plan");
}

async function planSaveEdit(d) {
  const plan = _planCache[d.wp_id];
  plan.edited_by = (currentUser && currentUser.username) || "anónimo";
  try {
    const saved = await api(`/api/wp/${d.wp_id}/plan`, {
      method: "POST",
      body: JSON.stringify(plan),
    });
    _planPersisted[d.wp_id] = saved.plan;
    _planIterations[d.wp_id] = (_planIterations[d.wp_id] || []).concat([`v${String(saved.version).padStart(2,"0")}`]);
    _planMode[d.wp_id] = "view";
    delete _planCache[d.wp_id];
    delete _planOriginal[d.wp_id];
    showTab("plan");
    toast(`Plan v${saved.version} guardado en Gitea`, "ok");
  } catch (err) { toast("Error guardando: " + err.message, "error"); }
}

async function planRegenerate(d) {
  if (!confirm("Esto descartará el plan vigente y generará uno nuevo con IA. ¿Continuar?")) return;
  try {
    await api(`/api/wp/${d.wp_id}/plan`, { method: "DELETE" });
    _planMode[d.wp_id] = "initial";
    delete _planPersisted[d.wp_id];
    await runEstimate(d);
  } catch (err) { toast(err.message, "error"); }
}

function tallaBadge(t) {
  if (!t || t === "none") return '<span style="color:#999;font-size:11px">—</span>';
  const colors = {
    "Tiny": "#107e3e", "Small": "#107e3e",
    "Medium": "#0a6ed1",
    "Large": "#df6e0c", "Very Large": "#bb0000",
  };
  const bgs = {
    "Tiny": "#e8f5e9", "Small": "#e8f5e9",
    "Medium": "#e3f2fd",
    "Large": "#fff3e0", "Very Large": "#ffebee",
  };
  return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;background:${bgs[t]||'#eee'};color:${colors[t]||'#555'}">${escapeHtml(t)}</span>`;
}

// Tabla de horas por talla (espejo de estimacion_metodologia.md).
// Si cambia el doc en Gitea, también ajustar aquí.
const TALLAS_HORAS = {
  "Tiny":       { back: 8,   front: 8,   pruebas: 2,  integ: 4  },
  "Small":      { back: 24,  front: 20,  pruebas: 6,  integ: 8  },
  "Medium":     { back: 48,  front: 40,  pruebas: 12, integ: 16 },
  "Large":      { back: 88,  front: 76,  pruebas: 20, integ: 28 },
  "Very Large": { back: 150, front: 130, pruebas: 36, integ: 44 },
  "none":       { back: 0,   front: 0,   pruebas: 0,  integ: 0  },
};
function tallaH(talla, comp) {
  return (TALLAS_HORAS[talla] && TALLAS_HORAS[talla][comp]) || 0;
}

// Recalcula horas de UN desarrollo según sus tallas y n_integraciones.
function recalcDevelopment(dv) {
  dv.horas_back = tallaH(dv.talla_back, "back");
  dv.horas_front = tallaH(dv.talla_front, "front");
  // Pruebas: SUMA back (si hay) + front (si hay), por componente desarrollado.
  let pruebas = 0;
  if (dv.talla_back && dv.talla_back !== "none") pruebas += tallaH(dv.talla_back, "pruebas");
  if (dv.talla_front && dv.talla_front !== "none") pruebas += tallaH(dv.talla_front, "pruebas");
  dv.horas_pruebas = pruebas;
  // Integraciones: tarifa según talla_back (donde vive el integration code).
  // Si solo hay front, usa talla_front como referencia.
  const tallaRef = (dv.talla_back !== "none" ? dv.talla_back : dv.talla_front) || "Medium";
  const porInteg = tallaH(tallaRef, "integ");
  dv.horas_integraciones = (Number(dv.n_integraciones) || 0) * porInteg;
  dv.total = dv.horas_back + dv.horas_front + dv.horas_pruebas + dv.horas_integraciones;
}

// Recalcula los WPs consolidados sumando los desarrollos.
// Solo toca los WPs cuyas horas DEBEN cambiar (Backend Task, UI Task,
// Technical QA). Test Plan/Functional/UAT/Deployment son fijos.
function recalcConsolidatedWPs(plan) {
  const devs = plan.developments || [];
  const sumBack = devs.reduce((s, d) => s + (d.horas_back || 0), 0);
  const sumFront = devs.reduce((s, d) => s + (d.horas_front || 0), 0);
  const sumPruebas = devs.reduce((s, d) => s + (d.horas_pruebas || 0), 0);
  const sumInteg = devs.reduce((s, d) => s + (d.horas_integraciones || 0), 0);
  for (const wp of plan.work_packages || []) {
    if (wp.type_name === "Backend Task") wp.hours = sumBack + sumInteg;
    else if (wp.type_name === "UI Task") wp.hours = sumFront;
    else if (wp.type_name === "Technical QA") wp.hours = sumPruebas;
    // Resto se queda fijo (Test Plan, Functional Testing, UAT, Deployment)
  }
}

// Recalcula SOLO los totales (sin, con contingencia, días, complexity)
// a partir de los WPs ya calculados. Usar cuando cambia la contingencia
// o las horas de un WP fijo (no derivado).
function recalcTotalsOnly(plan) {
  const wps = plan.work_packages || [];
  const sin = wps.reduce((s, wp) => s + (Number(wp.hours) || 0), 0);
  const ctg = Number(plan.contingencia_pct) || 0;
  const con = sin * (1 + ctg / 100);
  plan.total_horas_sin_contingencia = Math.round(sin * 10) / 10;
  plan.total_horas_con_contingencia = Math.round(con * 10) / 10;
  plan.calendar_days_realistic = Math.round((con / 8) * 10) / 10;
  if (con < 40) plan.complexity = "S";
  else if (con < 100) plan.complexity = "M";
  else if (con < 250) plan.complexity = "L";
  else plan.complexity = "XL";
}

// Recálculo completo: desarrollos → WPs derivados → totales.
// Usar cuando cambian tallas o n_integraciones.
function recalcPlanTotals(plan) {
  (plan.developments || []).forEach(recalcDevelopment);
  recalcConsolidatedWPs(plan);
  recalcTotalsOnly(plan);
  return plan;
}

function planIsDirty(wpId) {
  const a = JSON.stringify(_planCache[wpId] || {});
  const b = JSON.stringify(_planOriginal[wpId] || {});
  return a !== b;
}

// VIEW: muestra el plan persistido en read-only.
function renderPlanView($tc, d, plan, canEstimate) {
  const techBadges = (plan.tech_stack || [])
    .map(t => `<span class="tech-badge${t === "DYNPRO_LEGACY" ? " warn" : ""}">${escapeHtml(t)}</span>`)
    .join("");
  const devs = plan.developments || [];
  const wps = plan.work_packages || [];
  const iters = _planIterations[d.wp_id] || [];
  const versionLabel = plan.version ? `v${String(plan.version).padStart(2,"0")}` : "—";
  const savedAt = plan.saved_at || "—";
  const savedBy = plan.saved_by ? `por ${escapeHtml(plan.saved_by)}` : "";
  const edited = plan.edited_by ? `<span style="font-size:11px;background:#fff3e0;color:#df6e0c;padding:2px 8px;border-radius:10px;margin-left:6px">✏️ editado por ${escapeHtml(plan.edited_by)}</span>` : "";

  const devRows = devs.map(dv => `
    <tr>
      <td><b>${escapeHtml(dv.name)}</b>
        ${dv.rationale ? `<div class="rationale">${escapeHtml(dv.rationale)}</div>` : ""}
      </td>
      <td>${tallaBadge(dv.talla_back)}</td>
      <td>${tallaBadge(dv.talla_front)}</td>
      <td class="hours">${dv.n_integraciones || 0}</td>
      <td class="hours">${(dv.horas_back || 0).toFixed(0)}</td>
      <td class="hours">${(dv.horas_front || 0).toFixed(0)}</td>
      <td class="hours">${(dv.horas_pruebas || 0).toFixed(0)}</td>
      <td class="hours">${(dv.horas_integraciones || 0).toFixed(0)}</td>
      <td class="hours"><b>${(dv.total || 0).toFixed(0)}</b></td>
    </tr>`).join("");

  const wpRows = wps.map(wp => `
    <tr>
      <td><b>${escapeHtml(wp.subject || "")}</b>
        <div style="color:#666;font-size:11px">${escapeHtml(wp.type_name || "")}</div>
        ${wp.description ? `<div class="rationale">${escapeHtml(wp.description)}</div>` : ""}
      </td>
      <td class="hours"><b>${(wp.hours || 0).toFixed(0)}</b></td>
    </tr>`).join("");

  $tc.innerHTML = `
    <div class="panel">
      <h3>📐 Plan vigente · <span style="color:#0a6ed1;font-size:14px">${versionLabel}</span>${edited}
        <span style="font-size:11px;color:#888;font-weight:400;margin-left:8px">${savedAt} ${savedBy} · ${iters.length} versión(es) en Gitea</span>
      </h3>
      <div class="plan-summary">
        <div class="plan-kpi complexity-${plan.complexity || 'M'}">
          <div class="lbl">Complejidad</div><div class="val">${escapeHtml(plan.complexity || "?")}</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Con contingencia</div><div class="val">${(plan.total_horas_con_contingencia || 0).toFixed(0)} h</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Sin contingencia</div><div class="val" style="font-size:16px">${(plan.total_horas_sin_contingencia || 0).toFixed(0)} h</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Contingencia</div><div class="val" style="font-size:16px">${plan.contingencia_pct ?? 15}%</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Días laborables</div><div class="val">${(plan.calendar_days_realistic || 0).toFixed(1)}</div>
        </div>
      </div>
      <div style="margin-bottom:12px"><b>Stack:</b> ${techBadges}</div>
      <div style="margin-bottom:12px"><b>Resumen:</b> ${escapeHtml(plan.summary || "")}</div>
      <h3 style="font-size:13px;margin-top:18px">Desarrollos</h3>
      <table class="wp-table">
        <thead><tr>
          <th>Desarrollo</th><th>Back</th><th>Front</th>
          <th class="hours">N Integ</th><th class="hours">h Back</th><th class="hours">h Front</th>
          <th class="hours">h Pruebas</th><th class="hours">h Integ</th><th class="hours">Total</th>
        </tr></thead>
        <tbody>${devRows || `<tr><td colspan="9" style="text-align:center;color:#888">(sin desarrollos)</td></tr>`}</tbody>
      </table>
      <h3 style="font-size:13px;margin-top:18px">Work Packages</h3>
      <table class="wp-table">
        <thead><tr><th>WP</th><th class="hours">Horas</th></tr></thead>
        <tbody>${wpRows}</tbody>
      </table>
      ${(plan.assumptions || []).length ? `
        <h3 style="font-size:13px;margin-top:18px">Asunciones</h3>
        <ul style="font-size:13px;color:#555">${(plan.assumptions || []).map(a => `<li>${escapeHtml(a)}</li>`).join("")}</ul>
      ` : ""}
      ${canEstimate ? `
      <div style="margin-top:16px;padding:10px 12px;border:1px solid #e0e6ef;border-radius:6px;background:#f7f9fc">
        <div style="font-size:12px;font-weight:700;color:#33415c;margin-bottom:8px">Horas del plan (capturables)</div>
        <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap">
          <label style="font-size:12px;color:#555">Horas Técnicas<br><input type="number" id="plan-htec" min="0" step="4" value="${d.horas_tecnicas!=null?d.horas_tecnicas:""}" style="width:120px;padding:6px 8px;border:1px solid #ccc;border-radius:4px;font:inherit;font-size:13px;text-align:right"></label>
          <label style="font-size:12px;color:#555">Horas Funcionales<br><input type="number" id="plan-hfunc" min="0" step="4" value="${d.horas_funcionales!=null?d.horas_funcionales:""}" style="width:120px;padding:6px 8px;border:1px solid #ccc;border-radius:4px;font:inherit;font-size:13px;text-align:right"></label>
          <div style="font-size:13px;color:#1a3a52;padding-bottom:6px"><b>Total:</b> <span id="plan-htotal">${((+d.horas_tecnicas||0)+(+d.horas_funcionales||0))||0}</span> h</div>
          <button id="plan-hsave" style="background:#107e3e;color:#fff;border:none;padding:8px 14px;border-radius:4px;cursor:pointer;font-weight:600">💾 Guardar horas</button>
        </div>
        <div style="font-size:11px;color:#8693a8;margin-top:6px">La barra del Gantt refleja la suma (Técnicas + Funcionales).</div>
      </div>` : ""}
      <div style="margin-top:20px;padding-top:12px;border-top:1px solid #e0e0e0;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${canEstimate ? `
          <button id="plan-edit-btn" class="primary" style="background:#0a6ed1;color:#fff;border:none;padding:10px 16px;border-radius:4px;cursor:pointer">✏️ Editar</button>
          <button id="plan-regen-btn" style="padding:8px 14px;border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer">🔄 Re-generar con IA</button>
          <span style="display:inline-flex;align-items:center;gap:6px;margin-left:6px">
            <label for="plan-inicio" style="font-size:12px;color:#555;font-weight:600">📅 Inicio</label>
            <input type="date" id="plan-inicio" value="${plan.start_date || ""}" style="padding:6px 8px;border:1px solid #ccc;border-radius:4px;font:inherit;font-size:13px"/>
          </span>
          <button id="plan-guardar-btn" style="background:#107e3e;color:#fff;border:none;padding:10px 16px;border-radius:4px;cursor:pointer;font-weight:600" title="Guarda en el tablero de proyecto: fecha de inicio + fecha fin (= inicio + días laborables del estimado)">💾 Guardar plan</button>
          <button id="plan-blog-btn" style="background:#5b3da8;color:#fff;border:none;padding:10px 16px;border-radius:4px;cursor:pointer;font-weight:600" title="Postea un resumen general de la estimación al blog del requerimiento para que el equipo lo revise/acepte">📋 Enviar estimación al blog</button>
        ` : ""}
        <span style="color:#888;font-size:12px;margin-left:auto">
          <a href="${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}/estimacion/estimation.md" target="_blank">ver en Gitea</a>
        </span>
      </div>
    </div>`;
  if (canEstimate) {
    document.getElementById("plan-edit-btn").onclick = () => planEnterEdit(d);
    document.getElementById("plan-regen-btn").onclick = () => planRegenerate(d);
    // 💾 Guardar plan: fecha de inicio (manual) + fin (= inicio + días
    // laborables del estimado) en el board (proyecto-db). Reemplaza el sync a OP.
    const guardarBtn = document.getElementById("plan-guardar-btn");
    if (guardarBtn) guardarBtn.onclick = async () => {
      const sd = (document.getElementById("plan-inicio").value || "").trim();
      if (!sd) { toast("Elige la fecha de inicio", "error"); return; }
      guardarBtn.disabled = true;
      try {
        const r = await api(`/api/wp/${d.wp_id}/guardar-plan`, { method: "POST", body: JSON.stringify({ start_date: sd }) });
        toast(`Plan guardado: ${r.start_date}${r.due_date ? " → " + r.due_date : ""}`, "ok");
      } catch (e) { toast(e.message, "error"); }
      finally { guardarBtn.disabled = false; }
    };
    // Horas del plan partidas (mig-014): Técnicas + Funcionales capturables. La
    // suma se guarda como horas_plan (mueve la barra del Gantt) vía /campo.
    const $htec = document.getElementById("plan-htec");
    const $hfunc = document.getElementById("plan-hfunc");
    const $htotal = document.getElementById("plan-htotal");
    const $hsave = document.getElementById("plan-hsave");
    const recalcTot = () => { if ($htotal) $htotal.textContent = ((+$htec.value||0)+(+$hfunc.value||0))||0; };
    if ($htec) $htec.oninput = recalcTot;
    if ($hfunc) $hfunc.oninput = recalcTot;
    if ($hsave) $hsave.onclick = async () => {
      $hsave.disabled = true;
      try {
        await api(`/api/wp/${d.wp_id}/campo`, { method: "POST", body: JSON.stringify({ field: "horas_tecnicas", value: ($htec.value || "").trim() }) });
        await api(`/api/wp/${d.wp_id}/campo`, { method: "POST", body: JSON.stringify({ field: "horas_funcionales", value: ($hfunc.value || "").trim() }) });
        toast(`Horas guardadas: ${((+$htec.value||0)+(+$hfunc.value||0))||0} h (téc + func)`, "ok");
      } catch (e) { toast(e.message, "error"); }
      finally { $hsave.disabled = false; }
    };
    // 📋 Enviar un resumen GENERAL de la estimación al blog (para revisión/
    // aceptación). El detalle por camisas ya vive en sus tablas; esto es el
    // registro de "se estimó X" en la conversación.
    const blogBtn = document.getElementById("plan-blog-btn");
    if (blogBtn) blogBtn.onclick = async () => {
      const lines = [
        `📐 **Estimación** ${versionLabel} · complejidad ${plan.complexity || "?"}`,
        `- Esfuerzo: **${(plan.total_horas_con_contingencia || 0).toFixed(0)} h** (con contingencia ${plan.contingencia_pct ?? 15}%)`,
        `- Días laborables: ${(plan.calendar_days_realistic || 0).toFixed(1)}${plan.start_date ? ` · inicio ${plan.start_date}` : ""}`,
      ];
      if (plan.summary) lines.push(`- ${plan.summary}`);
      if ((plan.developments || []).length) lines.push(`- Desarrollos: ${plan.developments.length}`);
      lines.push("", "_Estimación enviada para revisión / aceptación del equipo._");
      blogBtn.disabled = true;
      try {
        await api(`/api/wp/${d.wp_id}/comment`, { method: "POST", body: JSON.stringify({ text: lines.join("\n") }) });
        toast("Estimación enviada al blog", "ok");
      } catch (e) { toast(e.message, "error"); }
      finally { blogBtn.disabled = false; }
    };
  }
}

// EDIT: tallas/integraciones/horas/subject editables con recálculo en vivo.
function renderPlanEditable($tc, d, plan, canEstimate) { /* alias */
  renderPlanCached($tc, d, plan);
}

function renderPlanCached($tc, d, plan) {
  const techBadges = (plan.tech_stack || [])
    .map(t => `<span class="tech-badge${t === "DYNPRO_LEGACY" ? " warn" : ""}">${escapeHtml(t)}</span>`)
    .join("");
  const devs = plan.developments || [];
  const wps = plan.work_packages || [];
  // --- Tabla de desarrollos (tallas + n_integraciones EDITABLES) ---
  const tallaOpts = (sel) => ["none","Tiny","Small","Medium","Large","Very Large"]
    .map(t => `<option value="${t}" ${t === sel ? "selected" : ""}>${t === "none" ? "—" : t}</option>`).join("");
  const devRows = devs.map((dv, i) => `
    <tr data-dev-idx="${i}">
      <td><b>${escapeHtml(dv.name)}</b>
        ${dv.rationale ? `<div class="rationale">${escapeHtml(dv.rationale)}</div>` : ""}
      </td>
      <td><select class="dev-edit-talla" data-idx="${i}" data-field="talla_back" style="padding:2px 4px;font-size:11px;border:1px solid #ccc;border-radius:4px">${tallaOpts(dv.talla_back || "none")}</select></td>
      <td><select class="dev-edit-talla" data-idx="${i}" data-field="talla_front" style="padding:2px 4px;font-size:11px;border:1px solid #ccc;border-radius:4px">${tallaOpts(dv.talla_front || "none")}</select></td>
      <td class="hours"><input type="number" class="dev-edit-int" data-idx="${i}" value="${dv.n_integraciones || 0}" min="0" step="1" style="width:50px;padding:2px 6px;border:1px solid #ddd;border-radius:4px;text-align:right;font-family:monospace"/></td>
      <td class="hours" id="dv-hb-${i}">${(dv.horas_back || 0).toFixed(0)}</td>
      <td class="hours" id="dv-hf-${i}">${(dv.horas_front || 0).toFixed(0)}</td>
      <td class="hours" id="dv-hp-${i}">${(dv.horas_pruebas || 0).toFixed(0)}</td>
      <td class="hours" id="dv-hi-${i}">${(dv.horas_integraciones || 0).toFixed(0)}</td>
      <td class="hours" id="dv-tot-${i}"><b>${(dv.total || 0).toFixed(0)}</b></td>
    </tr>`).join("");
  // --- Sub-tabla de tareas fijas: filas adicionales en la misma matriz ---
  const DERIVED = new Set(["Backend Task", "UI Task", "Technical QA"]);
  const fixedWPs = wps.map((wp, idx) => ({ wp, idx })).filter(x => !DERIVED.has(x.wp.type_name));
  const fixedRows = fixedWPs.map(({ wp, idx }) => `
    <tr data-fixed-wp-idx="${idx}" style="background:#fafafa">
      <td colspan="2" style="padding-left:14px">
        <b>${escapeHtml(wp.subject || wp.type_name)}</b>
        <div style="color:#666;font-size:11px">${escapeHtml(wp.type_name)} · tarea fija (no escala con tallas)</div>
      </td>
      <td colspan="6" style="color:#999;font-size:11px;text-align:center">—</td>
      <td class="hours">
        <input type="number" class="wp-fixed-hours" data-idx="${idx}"
               value="${(wp.hours || 0).toFixed(0)}" min="0" step="1"
               style="width:60px;padding:4px 6px;border:1px solid #ddd;border-radius:4px;text-align:right;font-family:monospace;font-weight:600;color:#0a6ed1"/>
      </td>
    </tr>`).join("");
  // --- Descripción read-only por WP (debajo de la matriz, sin inputs) ---
  function wpCompositionTxt(wp) {
    const parts = [];
    if (wp.type_name === "Backend Task") {
      devs.forEach(dv => {
        if ((dv.horas_back || 0) > 0) parts.push(`${dv.name} (back ${dv.talla_back}: ${(dv.horas_back||0).toFixed(0)}h)`);
        if ((dv.horas_integraciones || 0) > 0) parts.push(`${dv.name} (integ ×${dv.n_integraciones||0}: ${(dv.horas_integraciones||0).toFixed(0)}h)`);
      });
    } else if (wp.type_name === "UI Task") {
      devs.forEach(dv => {
        if ((dv.horas_front || 0) > 0) parts.push(`${dv.name} (front ${dv.talla_front}: ${(dv.horas_front||0).toFixed(0)}h)`);
      });
    } else if (wp.type_name === "Technical QA") {
      devs.forEach(dv => {
        const phb = (dv.talla_back && dv.talla_back !== "none") ? tallaH(dv.talla_back, "pruebas") : 0;
        const phf = (dv.talla_front && dv.talla_front !== "none") ? tallaH(dv.talla_front, "pruebas") : 0;
        if (phb > 0) parts.push(`${dv.name} (pruebas back: ${phb}h)`);
        if (phf > 0) parts.push(`${dv.name} (pruebas front: ${phf}h)`);
      });
    }
    return parts.join(" · ") || "tarea fija (horas editables en la matriz arriba)";
  }
  const wpDescLines = wps.map((wp, idx) => {
    const derived = DERIVED.has(wp.type_name);
    return `
      <div class="wp-desc" data-wp-idx="${idx}" style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px">
        <div><b>${escapeHtml(wp.subject || wp.type_name)}</b>
          <span style="color:#0a6ed1;font-family:monospace;float:right" id="wp-desc-h-${idx}">${(wp.hours || 0).toFixed(0)} h</span>
        </div>
        <div style="color:#666;font-size:11px;margin-top:2px" id="wp-desc-comp-${idx}">${escapeHtml(wpCompositionTxt(wp))}</div>
      </div>`;
  }).join("");
  const contingencia = plan.contingencia_pct ?? 15;  // 0 es válido (no caer al default)
  const dirty = planIsDirty(d.wp_id);
  $tc.innerHTML = `
    <div class="panel">
      <h3>📐 Estimación generada ${dirty ? '<span style="font-size:11px;background:#fff3e0;color:#df6e0c;padding:2px 8px;border-radius:10px;font-weight:600;margin-left:8px">✏️ EDITADA</span>' : ""}</h3>
      <div class="plan-summary">
        <div class="plan-kpi complexity-${plan.complexity || 'M'}">
          <div class="lbl">Complejidad</div>
          <div class="val" id="kpi-complexity">${escapeHtml(plan.complexity || "?")}</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Con contingencia</div>
          <div class="val" id="kpi-con">${(plan.total_horas_con_contingencia || 0).toFixed(0)} h</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Sin contingencia</div>
          <div class="val" style="font-size:16px" id="kpi-sin">${(plan.total_horas_sin_contingencia || 0).toFixed(0)} h</div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Contingencia %</div>
          <div class="val" style="font-size:16px">
            <input type="number" id="kpi-contingencia" value="${contingencia}" min="0" max="100" step="1"
                   style="width:60px;padding:2px 6px;border:1px solid #ddd;border-radius:4px;text-align:right;font-size:16px;font-weight:600;color:#0a6ed1"/>%
          </div>
        </div>
        <div class="plan-kpi">
          <div class="lbl">Días laborables</div>
          <div class="val" id="kpi-days">${(plan.calendar_days_realistic || 0).toFixed(1)}</div>
        </div>
      </div>
      <div style="margin-bottom:12px"><b>Stack:</b> ${techBadges}</div>
      <div style="margin-bottom:12px"><b>Resumen:</b> ${escapeHtml(plan.summary || "")}</div>

      <h3 style="font-size:13px;margin-top:18px">Matriz editable: desarrollos + tareas fijas
        <span style="color:#666;font-weight:400;font-size:11px">(cambia tallas/integraciones arriba; horas de tareas fijas en las últimas filas)</span>
      </h3>
      <table class="wp-table">
        <thead><tr>
          <th>Desarrollo / Tarea</th>
          <th>Talla Back</th>
          <th>Talla Front</th>
          <th class="hours">N Integ.</th>
          <th class="hours">h Back</th>
          <th class="hours">h Front</th>
          <th class="hours">h Pruebas</th>
          <th class="hours">h Integ.</th>
          <th class="hours">Total</th>
        </tr></thead>
        <tbody>
          ${devRows || `<tr><td colspan="9" style="text-align:center;color:#888">(sin desarrollos)</td></tr>`}
          ${fixedRows}
        </tbody>
      </table>

      <h3 style="font-size:13px;margin-top:18px">Work Packages propuestos (desglose)
        <span style="color:#666;font-weight:400;font-size:11px">(read-only — composición derivada de la matriz)</span>
      </h3>
      <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px">${wpDescLines}</div>

      <h3 style="font-size:13px;margin-top:18px">Asunciones <span style="color:#666;font-weight:400;font-size:11px">(una por línea)</span></h3>
      <textarea id="plan-assumptions" style="width:100%;min-height:80px;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:13px;font-family:inherit">${escapeHtml((plan.assumptions || []).join("\n"))}</textarea>

      <div style="margin-top:20px;padding-top:12px;border-top:1px solid #e0e0e0;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button id="plan-save-btn" class="primary" style="background:#107e3e;color:#fff;border:none;padding:10px 18px;border-radius:4px;cursor:pointer;font-weight:600">💾 Guardar (nueva versión)</button>
        <button id="plan-cancel-btn" style="padding:8px 14px;border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer">✖ Cancelar</button>
        <span style="color:#888;font-size:12px;margin-left:auto">${dirty ? "✏️ con cambios sin guardar" : "(sin cambios)"} · model orig: ${escapeHtml(plan.model || "")}</span>
      </div>
    </div>`;

  // --- Handlers de edición en vivo ---
  // Tallas (dropdowns) y n_integraciones de desarrollos
  document.querySelectorAll(".dev-edit-talla").forEach(sel => {
    sel.onchange = (e) => {
      const i = +e.target.dataset.idx;
      const field = e.target.dataset.field;
      plan.developments[i][field] = e.target.value;
      recalcPlanTotals(plan);
      refreshDevRow(plan, i);
      refreshWPDescLines(plan);
      refreshPlanKPIs(plan);
      refreshDirtyBadge(plan);
    };
  });
  document.querySelectorAll(".dev-edit-int").forEach(inp => {
    inp.oninput = (e) => {
      const i = +e.target.dataset.idx;
      plan.developments[i].n_integraciones = Number(e.target.value) || 0;
      recalcPlanTotals(plan);
      refreshDevRow(plan, i);
      refreshWPDescLines(plan);
      refreshPlanKPIs(plan);
      refreshDirtyBadge(plan);
    };
  });
  // Edición horas de WPs FIJOS (filas extra de la matriz: Test Plan,
  // Functional Testing, UAT, Deployment). Los DERIVADOS ya no tienen
  // input — derivan de la suma de los desarrollos.
  document.querySelectorAll(".wp-fixed-hours").forEach(inp => {
    inp.oninput = (e) => {
      const idx = +e.target.dataset.idx;
      plan.work_packages[idx].hours = Number(e.target.value) || 0;
      recalcTotalsOnly(plan);
      refreshPlanKPIs(plan);
      refreshWPDescLines(plan);
      refreshDirtyBadge(plan);
    };
  });
  // Contingencia editable — solo afecta totales, no toca devs ni WPs
  const ctgInp = document.getElementById("kpi-contingencia");
  if (ctgInp) ctgInp.oninput = (e) => {
    plan.contingencia_pct = Number(e.target.value) || 0;
    recalcTotalsOnly(plan);
    refreshPlanKPIs(plan);
    refreshDirtyBadge(plan);
  };
  const asnTa = document.getElementById("plan-assumptions");
  if (asnTa) asnTa.oninput = (e) => {
    plan.assumptions = e.target.value.split("\n").map(s => s.trim()).filter(Boolean);
    refreshDirtyBadge(plan);
  };

  // Botones del footer en modo EDIT
  const saveBtn = document.getElementById("plan-save-btn");
  if (saveBtn) saveBtn.onclick = () => planSaveEdit(d);
  const cancelBtn = document.getElementById("plan-cancel-btn");
  if (cancelBtn) cancelBtn.onclick = () => planCancelEdit(d);
}

function refreshPlanKPIs(plan) {
  const $c = document.getElementById("kpi-complexity"); if ($c) $c.textContent = plan.complexity || "?";
  const $s = document.getElementById("kpi-sin"); if ($s) $s.textContent = (plan.total_horas_sin_contingencia || 0).toFixed(0) + " h";
  const $w = document.getElementById("kpi-con"); if ($w) $w.textContent = (plan.total_horas_con_contingencia || 0).toFixed(0) + " h";
  const $d = document.getElementById("kpi-days"); if ($d) $d.textContent = (plan.calendar_days_realistic || 0).toFixed(1);
  const kpiBox = document.querySelector(".plan-kpi.complexity-S, .plan-kpi.complexity-M, .plan-kpi.complexity-L, .plan-kpi.complexity-XL");
  if (kpiBox) kpiBox.className = `plan-kpi complexity-${plan.complexity || "M"}`;
}

// Refresca SOLO las celdas de horas de un desarrollo (más rápido que re-render).
function refreshDevRow(plan, i) {
  const dv = plan.developments[i];
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = (v || 0).toFixed(0); };
  set(`dv-hb-${i}`, dv.horas_back);
  set(`dv-hf-${i}`, dv.horas_front);
  set(`dv-hp-${i}`, dv.horas_pruebas);
  set(`dv-hi-${i}`, dv.horas_integraciones);
  const $tot = document.getElementById(`dv-tot-${i}`);
  if ($tot) $tot.innerHTML = `<b>${(dv.total || 0).toFixed(0)}</b>`;
}

// Refresca las líneas descriptivas read-only de los WPs cuando cambian las
// horas de los desarrollos (Backend/UI/QA derivados) o de los WPs fijos.
function refreshWPDescLines(plan) {
  const devs = plan.developments || [];
  function comp(wp) {
    const parts = [];
    if (wp.type_name === "Backend Task") {
      devs.forEach(dv => {
        if ((dv.horas_back || 0) > 0) parts.push(`${dv.name} (back ${dv.talla_back}: ${(dv.horas_back||0).toFixed(0)}h)`);
        if ((dv.horas_integraciones || 0) > 0) parts.push(`${dv.name} (integ ×${dv.n_integraciones||0}: ${(dv.horas_integraciones||0).toFixed(0)}h)`);
      });
    } else if (wp.type_name === "UI Task") {
      devs.forEach(dv => {
        if ((dv.horas_front || 0) > 0) parts.push(`${dv.name} (front ${dv.talla_front}: ${(dv.horas_front||0).toFixed(0)}h)`);
      });
    } else if (wp.type_name === "Technical QA") {
      devs.forEach(dv => {
        const phb = (dv.talla_back && dv.talla_back !== "none") ? tallaH(dv.talla_back, "pruebas") : 0;
        const phf = (dv.talla_front && dv.talla_front !== "none") ? tallaH(dv.talla_front, "pruebas") : 0;
        if (phb > 0) parts.push(`${dv.name} (pruebas back: ${phb}h)`);
        if (phf > 0) parts.push(`${dv.name} (pruebas front: ${phf}h)`);
      });
    }
    return parts.join(" · ") || "tarea fija (horas editables en la matriz arriba)";
  }
  (plan.work_packages || []).forEach((wp, idx) => {
    const $h = document.getElementById(`wp-desc-h-${idx}`);
    if ($h) $h.textContent = (wp.hours || 0).toFixed(0) + " h";
    const $c = document.getElementById(`wp-desc-comp-${idx}`);
    if ($c) $c.textContent = comp(wp);
  });
}

function refreshDirtyBadge(plan) {
  const wpId = currentWpId;
  if (!wpId) return;
  const isDirty = planIsDirty(wpId);
  const badge = document.querySelector(".panel h3 span");
  // El badge "EDITADA" se controla por re-render completo cuando es necesario.
  // Aquí solo actualizamos el estado del botón Restaurar.
  const restoreBtn = document.getElementById("plan-restore-btn");
  if (restoreBtn) {
    restoreBtn.disabled = !isDirty;
    if (isDirty) {
      restoreBtn.style.cssText = "background:#fff;border:1px solid #df6e0c;color:#df6e0c;padding:8px 14px;border-radius:4px";
    } else {
      restoreBtn.style.cssText = "background:#f0f0f0;color:#aaa;border:1px solid #ddd;cursor:not-allowed;padding:8px 14px;border-radius:4px";
    }
  }
}

async function renderDesarrollo($tc, d) {
  // Modelo nuevo: repo por requerimiento. El desarrollo es LOCAL (Claude Code/
  // Codex) contra este repo; aquí mostramos cómo clonarlo y los entregables.
  const repo = d.dev_repo || {};
  if (repo.org && repo.repo) {
    const stack = repo.is_abap ? "ABAP / SAP · round-trip abapGit" : "git";
    $tc.innerHTML = `
      <div class="panel">
        <div class="row" style="justify-content:space-between;align-items:center;margin-bottom:8px">
          <h3 style="margin:0">🛠 Repo del requerimiento <span style="color:#888;font-weight:400;font-size:12px">· ${escapeHtml(stack)}</span></h3>
          <a href="${repo.web_url}" target="_blank" class="ext-link" style="border:1px solid #0a6ed1;color:#0a6ed1;padding:4px 10px;border-radius:4px;text-decoration:none;font-size:12px">📁 Abrir en Gitea</a>
        </div>
        <p style="font-size:13px;color:#444;margin:4px 0">Cliente: <b>${escapeHtml(repo.cliente || repo.org)}</b> · Org: <code>${escapeHtml(repo.org)}</code></p>
        <p style="font-size:13px;color:#444;margin:4px 0">Clónalo y trabájalo localmente con Claude Code/Codex:</p>
        <pre style="background:#1e1e1e;color:#e6e6e6;padding:8px 10px;border-radius:6px;font-size:12px;overflow:auto">git clone ${escapeHtml(repo.clone_url)}</pre>
        ${repo.mcp_seeded ? '<p style="font-size:12px;color:#666;margin:4px 0">Trae <code>.mcp.json</code>: el agente local consulta arquitectura/estándares/diseño y publica entregables vía el MCP <code>fabrica</code> (exporta <code>FABRICA_MCP_TOKEN</code> en tu entorno).</p>' : ''}
      </div>
      <div id="dev-artefactos"></div>`;
    await renderDevArtefactos($tc.querySelector("#dev-artefactos"), d, repo);
    return;
  }
  // Fallback legacy: entrega.md generada por el agente (workflows en vuelo del
  // modelo viejo, antes del rediseño repo-por-requerimiento).
  const st = d.desarrollo_state || {};
  const giteaDev = `${d.gitea_web_url}/${d.artifacts_owner}/${d.artifacts_repo}/src/branch/${d.branch}/desarrollo`;
  if (!st.has_vigente) {
    $tc.innerHTML = `<div class="panel"><div class="empty" style="padding:24px">🛠 El repo del requerimiento aún no se ha creado.<br><br>Se crea al entrar a <b>Desarrollo</b>: la fábrica genera un repo (bajo la org del cliente) con el paquete de diseño + adjuntos para que lo trabajes localmente con Claude Code/Codex.</div></div>`;
    return;
  }
  const meta = st.meta || {};
  const vers = st.versiones || [];
  const versHtml = vers.length
    ? `<div class="panel"><h3 style="margin:0 0 8px">Versiones aprobadas</h3>${vers.map(v => `<div style="font-size:12px;color:#555">📌 <b>${escapeHtml(v.version)}</b> — ${escapeHtml(v.aprobado_por || v.trigger || "")}</div>`).join("")}</div>`
    : "";
  $tc.innerHTML = `
    <div class="panel">
      <div class="row" style="justify-content:space-between;align-items:center;margin-bottom:10px">
        <h3 style="margin:0">🛠 Entrega de desarrollo <span style="color:#888;font-weight:400;font-size:12px">· ${escapeHtml(meta.version_actual || "")} · iter ${meta.iter_actual || "?"} · ${escapeHtml(meta.ultimo_modelo || "")} · ${escapeHtml(meta.status || "")}</span></h3>
        <a href="${giteaDev}" target="_blank" class="ext-link" style="border:1px solid #0a6ed1;color:#0a6ed1;padding:4px 10px;border-radius:4px;text-decoration:none;font-size:12px">📁 Ver código en Gitea</a>
      </div>
      <div class="md-body" id="dev-entrega"><div class="empty">cargando entrega…</div></div>
    </div>
    ${versHtml}`;
  try {
    const r = await api(`/api/artifact/${d.artifacts_owner}/${d.artifacts_repo}/${d.branch}/desarrollo/entrega.md`);
    document.getElementById("dev-entrega").innerHTML = r.html_render || "(vacío)";
  } catch (e) {
    document.getElementById("dev-entrega").innerHTML = `<div class="empty">No se pudo cargar la entrega: ${escapeHtml(e.message)}</div>`;
  }
}

function adjuntosHtml(d) {
  // Documentos de entrada unificados (MinIO + snapshot del agente + transcripción).
  const inputs = (d.input_docs && d.input_docs.length) ? d.input_docs : (d.inputs_files || []);
  const canAdd = !!d.can_add_files;         // D4: enriquecer antes de validar
  const canDel = !!d.can_delete_files;      // borrar: todo el ciclo (fábrica/admin)

  const inputsSection = inputs.length ? `
    <div class="panel">
      <h3 style="margin:0 0 4px 0">📥 Documentos de entrada · para validar la spec</h3>
      <div style="font-size:12px;color:#666;margin-bottom:10px">Lo que entró al requisito (lo que leyó la IA). Ábrelos para comparar contra la especificación generada.</div>
      <table style="width:100%; border-collapse:collapse;">
        <thead><tr style="background:#f5f5f5"><th align="left">Documento</th><th align="left">Clasificación</th><th align="left">Tipo</th><th align="left">Subido</th><th align="right">Tamaño</th><th align="center">Abrir</th></tr></thead>
        <tbody>
          ${inputs.map(f => {
            const isImg = (f.content_type || "").startsWith("image/");
            const thumb = (isImg && f.url) ? `<a href="${f.url}" target="_blank" rel="noopener"><img src="${f.url}" style="max-height:38px;max-width:60px;border-radius:3px;border:1px solid #ddd;vertical-align:middle;margin-right:8px"></a>` : "";
            const ver = f.url ? `<a href="${f.url}" target="_blank" rel="noopener" style="color:#0a6ed1;font-weight:600;text-decoration:none">👁 Ver</a>` : `<span style="color:#bbb">—</span>`;
            const _tipoMap = { especificacion: "Especificación", reporte: "Reporte", pantallas: "Pantallas", transcripcion: "Transcripción", cuestionario: "Cuestionario", otro: "Otro" };
            const clasif = f.tipo ? `<span style="background:#eef4fc;color:#0a6ed1;border-radius:10px;padding:2px 8px;font-size:11px;font-weight:600">${escapeHtml(_tipoMap[f.tipo] || f.tipo)}</span>` : `<span style="color:#bbb">—</span>`;
            const del = (f.key && canDel) ? `<a href="#" class="del-input" data-key="${escapeHtml(f.key)}" title="Borrar adjunto" style="color:#c0392b;text-decoration:none;margin-left:10px">🗑</a>` : "";
            const sz = f.size ? `${((f.size||0) / 1024 / 1024).toFixed(2)} MB` : "—";
            const subido = f.uploaded_at ? f.uploaded_at.slice(0,10) : "—";
            return `<tr>
              <td style="padding:6px">${thumb}<code>${escapeHtml(f.name || f.filename || "")}</code></td>
              <td style="padding:6px">${clasif}</td>
              <td style="padding:6px;color:#666;font-size:12px">${escapeHtml(f.content_type || "")}</td>
              <td style="padding:6px;color:#666;font-size:12px;white-space:nowrap">${subido}</td>
              <td style="padding:6px;text-align:right;color:#666;font-size:12px">${sz}</td>
              <td style="padding:6px;text-align:center;white-space:nowrap">${ver}${del}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>` : `
    <div class="panel">
      <h3 style="margin:0 0 10px 0">📦 Inputs en MinIO · lo que el agente IA va a leer</h3>
      <div class="empty" style="padding:14px;background:#fff8e1;border:1px solid #f0d060;border-radius:4px;color:#856404">
        ⚠ <b>Sin archivos para el agente.</b> El requisito sólo tiene título y descripción.
        ${canAdd ? "Si el cliente no aportó transcripción / spec / contexto, <b>agrégalo aquí antes de validar</b>." : "Pídele al SwF Funcional / admin que adjunte la transcripción o spec antes de validar."}
      </div>
    </div>`;

  // Agregar archivos: botón directo (sube a MinIO + manifest) en Recepción/
  // Diseño para fábrica/admin; también se puede desde la pestaña Blog.
  const addBtnSection = `
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;color:#555">
        ${canAdd ? `<button id="add-files-btn" class="primary" style="background:#0a6ed1;color:#fff;border:none;border-radius:4px;padding:8px 16px;cursor:pointer;font-weight:600">📎 Agregar archivos</button>` : ""}
        <span>${canAdd ? "Suben a MinIO y los lee el agente al arrancar. " : ""}También puedes adjuntar desde la pestaña <b>Blog</b> (se vuelven inputs y se publican en Gitea <code>/inputs/</code>).</span>
      </div>
    </div>`;

  return inputsSection + addBtnSection;
}

// Cablea los controles de adjuntos (agregar / borrar) dentro de $scope.
function wireAdjuntos($scope, d) {
  const $btn = $scope.querySelector("#add-files-btn");
  if ($btn) $btn.onclick = () => openAddFilesModal(d);
  // Borrar adjunto (🗑) — aparece en todo el ciclo si can_delete_files.
  $scope.querySelectorAll(".del-input").forEach(a => a.onclick = async (ev) => {
    ev.preventDefault();
    if (!confirm("¿Borrar este adjunto? (también se elimina del object storage)")) return;
    try {
      await api(`/api/wp/${d.wp_id}/delete-file`, { method: "POST", body: JSON.stringify({ key: a.dataset.key }) });
      toast("Adjunto borrado", "ok");
      await loadDetail(d.wp_id);
    } catch (e) { toast(e.message, "error"); }
  });
}

// Etiquetas legibles de roles y etapas (para el blog dirigido por fase).
const ROL_LBL = {
  "swf-funcional": "Funcional Fábrica", "admin": "Admin",
  "swf-desarrollador": "Desarrollador", "funcional": "Funcional",
  "usuario-clave": "Usuario", "project-manager": "Líder Proyecto",
  "swf-project-manager": "Líder Proyecto", "swf-tester": "Tester",
};
const ETAPA_LBL = {
  recepcion: "Recepción", diseno: "Diseño", desarrollo: "Desarrollo",
  pruebas_funcionales: "Pruebas Funcionales", pruebas_usuario: "Pruebas de Usuario",
  cerrado: "Cerrado",
};
const rolLbl = (r) => ROL_LBL[r] || r || "—";

// Sube archivos a MinIO (presigned) y devuelve los refs para el manifest/blog.
async function uploadBlogFiles($tc, d, fs) {
  const out = [];
  if (!fs.length) return out;
  const $stat = $tc.querySelector("#blog-status");
  const batch_id = crypto.randomUUID();
  const usedNames = new Set();
  for (const file of fs) {
    const newName = buildFileName(d.subject || ("req-" + d.wp_id), "otro", file.name, usedNames);
    if ($stat) $stat.textContent = `Subiendo ${newName}…`;
    const urlR = await api("/api/inbox/upload-url", {
      method: "POST",
      body: JSON.stringify({ batch_id, filename: newName, content_type: file.type || "application/octet-stream" }),
    });
    const putR = await fetch(urlR.put_url, { method: "PUT", body: file, headers: { "Content-Type": urlR.content_type } });
    if (!putR.ok) throw new Error(`MinIO PUT ${newName} → HTTP ${putR.status}`);
    out.push({ filename: newName, key: urlR.key, tipo: "otro", size: file.size, content_type: file.type || urlR.content_type });
  }
  return out;
}

// Blog colaborativo que DIRIGE el flujo (hub-and-spoke). Pinta la pelota
// (quién y desde cuándo), el hilo, y acciones según el rol (driver/radio).
// Archivos adjuntos = INPUTS (se committean a Gitea /inputs/ para pull).
function renderBlog($tc, d) {
  const coms = d.comentarios || [];
  const bf = d.blog_fase || {};
  const fmtTs = (s) => { try { return new Date(s).toLocaleString("es"); } catch (e) { return s || ""; } };
  const nl2br = (s) => escapeHtml(s || "").replace(/\n/g, "<br>");
  const adjHtml = (a) => {
    const kb = a.size ? ` <span style="color:#999">(${(a.size / 1024).toFixed(1)} KB)</span>` : "";
    const name = escapeHtml(a.filename || "archivo");
    return a.url
      ? `<a href="${a.url}" target="_blank" rel="noopener" style="color:#0a6ed1;text-decoration:none">📎 ${name}</a>${kb}`
      : `📎 ${name}${kb}`;
  };
  const btn = (id, txt, bg) => `<button id="${id}" style="background:${bg};color:#fff;border:none;border-radius:4px;padding:8px 14px;cursor:pointer;font-weight:600">${txt}</button>`;
  const accBadge = (c) => {
    if (c.accion === "delegar") return ` <span style="background:#5b3da8;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px">→ ${escapeHtml(c.destino_usuario || rolLbl(c.destino_rol))}</span>`;
    if (c.accion === "aprobar") return ` <span style="background:#107e3e;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px">aprobó</span>`;
    if (c.accion === "rechazar") return ` <span style="background:#bb0000;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px">rechazó</span>`;
    if (c.accion === "devolver") return ` <span style="background:#d97400;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px">devolvió</span>`;
    if (c.accion === "tomar") return ` <span style="background:#d97400;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px">tomó</span>`;
    return "";
  };
  const thread = coms.length ? coms.map(c => `
    <div style="border:1px solid #e7e7e7;border-radius:8px;padding:10px 12px;margin-bottom:8px;background:#fff">
      <div style="font-size:12px;color:#555;margin-bottom:4px"><b>${escapeHtml(c.autor_nombre || c.autor || "—")}</b> <span style="color:#999">· ${fmtTs(c.created_at)}</span>${accBadge(c)}</div>
      <div style="font-size:14px;line-height:1.5">${nl2br(c.cuerpo)}</div>
      ${(c.adjuntos && c.adjuntos.length) ? `<div style="margin-top:6px;font-size:13px">${c.adjuntos.map(adjHtml).join(" · ")}</div>` : ""}
    </div>`).join("") : `<div class="empty">Sin comentarios todavía. Inicia la conversación.</div>`;

  // Banner de estado: sin iniciar / en pool / tomado.
  let banner = "";
  if (!bf.workflow_running) {
    // Workflow apagado → indicador "sin iniciar" + botón ▶ Iniciar (fábrica/admin).
    banner = `<div style="background:#eef1f5;border:1px solid #d3d9e0;border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span>⚪ <b>Workflow sin iniciar.</b> El requerimiento está en Recepción esperando que lleguen sus inputs.</span>
      ${bf.can_iniciar ? btn("b-iniciar", "▶ Iniciar", "#107e3e") : '<span style="color:#888">Un funcional de fábrica lo iniciará.</span>'}
    </div>`;
  } else if (bf.etapa) {
    const enPool = !bf.pelota_usuario;
    const quien = enPool
      ? `<span style="color:#d97400;font-weight:600">pool de ${escapeHtml(rolLbl(bf.pelota_rol))} — sin tomar</span>`
      : `<b>${escapeHtml(bf.pelota_usuario)}</b>`;
    const miRol = bf.soy_driver ? ' · <span style="color:#107e3e;font-weight:600">eres driver</span>'
                : bf.soy_radio ? ' · <span style="color:#0a6ed1;font-weight:600">puedes responder</span>' : "";
    banner = `<div style="background:#fff8e1;border:1px solid #ffe0a3;border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span>🎾 <b>Pelota:</b> ${quien} <span style="color:#999">· desde ${fmtTs(bf.pelota_desde)}</span> &nbsp;·&nbsp; Fase <b>${escapeHtml(ETAPA_LBL[bf.etapa] || bf.etapa)}</b>${miRol}</span>
      ${bf.puedo_tomar ? btn("b-tomar", "✋ Tomar", "#d97400") : ""}
    </div>`;
  }

  // Controles de acción. Sin workflow corriendo: solo comentar (las decisiones de
  // fase requieren un workflow vivo). Con workflow: acciones según rol.
  const destinos = [...new Set([...(bf.drivers || []), ...(bf.radios || [])])];
  const destinoSel = `<select id="blog-destino" style="font-size:12px;padding:4px 6px;border:1px solid #ccc;border-radius:5px">${destinos.map(r => `<option value="${r}">${escapeHtml(rolLbl(r))}</option>`).join("")}</select>`;
  let actions;
  if (bf.workflow_running && bf.soy_driver) {
    const avanzaLbl = ETAPA_LBL[bf.avanza_a] || bf.avanza_a || "siguiente";
    const devLbl = ETAPA_LBL[bf.devuelve_a] || bf.devuelve_a;
    const devBtn = bf.devuelve_a ? btn("b-devolver", "↩️ Devolver → " + devLbl, "#d97400") : "";
    // Al aprobar Recepción→Diseño: elegir el PERFIL/stack (gobierna el design
    // skill + convenciones del stack para la spec). Se puebla desde /api/profiles.
    const stackSel = bf.avanza_a === "diseno"
      ? `<span style="display:inline-flex;align-items:center;gap:4px;border-left:1px solid #eee;padding-left:8px"><label style="font-size:12px;color:#555">Stack:</label><select id="blog-profile" title="Stack/perfil que gobierna spec + skill de diseño" style="font-size:12px;padding:4px 6px;border:1px solid #ccc;border-radius:5px"><option value="">(default)</option></select></span>`
      : "";
    actions = `
      ${btn("b-responder", "💬 Comentar", "#6a6d70")}
      <span style="display:inline-flex;align-items:center;gap:4px;border-left:1px solid #eee;padding-left:8px">🔎 Revisar → ${destinoSel} ${btn("b-delegar", "Pasar pelota", "#5b3da8")}</span>
      <span style="margin-left:auto;display:inline-flex;align-items:center;gap:6px">${stackSel} ${devBtn} ${btn("b-aprobar", "✅ Aprobar → " + avanzaLbl, "#107e3e")} ${btn("b-rechazar", "⛔ Rechazar", "#bb0000")}</span>`;
  } else if (bf.workflow_running && bf.soy_radio) {
    actions = btn("b-responder", "↩ Responder (vuelve al hub)", "#0a6ed1");
  } else {
    actions = btn("b-responder", "💬 Comentar", "#0a6ed1");
  }

  $tc.innerHTML = `
    <div class="panel">
      <h3 style="margin:0 0 6px 0">💬 Blog del requisito</h3>
      <div style="font-size:12px;color:#888;margin-bottom:12px">El blog dirige el flujo: los <b>drivers</b> aprueban / rechazan / pasan la pelota; los <b>radios</b> responden y la pelota vuelve al hub. Los archivos adjuntos son <b>inputs</b> (se publican en Gitea <code>/inputs/</code>; se obtienen con <code>git pull</code>).</div>
      ${banner}
      <div id="blog-thread">${thread}</div>
      ${d.es_terminal ? `<div style="margin-top:14px;border-top:1px solid #eee;padding-top:12px;color:#922b21;font-size:13px">🔒 Requisito ${d.es_desestimado ? "<b>desestimado</b>" : "cerrado"} — el blog está bloqueado para nuevas acciones.</div>` : `<div style="margin-top:14px;border-top:1px solid #eee;padding-top:12px">
        <textarea id="blog-text" rows="3" placeholder="Escribe un comentario…" style="width:100%;border:1px solid #ccc;border-radius:6px;padding:8px;font-size:14px;resize:vertical;box-sizing:border-box"></textarea>
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap">
          <input id="blog-files" type="file" multiple accept=".pdf,.txt,.md,.docx,.xlsx,.csv,image/*" style="font-size:12px"/>
          ${actions}
        </div>
        <div id="blog-status" style="font-size:12px;color:#666;margin-top:6px"></div>
      </div>`}
    </div>`;

  const $text = $tc.querySelector("#blog-text");
  const $files = $tc.querySelector("#blog-files");
  const $stat = $tc.querySelector("#blog-status");
  const post = async (accion, destino_rol) => {
    const text = ($text.value || "").trim();
    const fs = Array.from($files.files || []);
    if (accion === "responder" && !text && !fs.length) { toast("Escribe un comentario o adjunta un archivo", "error"); return; }
    if (accion === "rechazar" && !confirm("¿Rechazar y terminar el requerimiento?")) return;
    $tc.querySelectorAll("button").forEach(b => b.disabled = true);
    try {
      const uploaded = await uploadBlogFiles($tc, d, fs);
      $stat.textContent = "Publicando…";
      const cbody = { text, files: uploaded, accion, destino_rol };
      const $pf = $tc.querySelector("#blog-profile");
      if (accion === "aprobar" && $pf) cbody.profile = $pf.value || "";
      await api(`/api/wp/${d.wp_id}/comment`, { method: "POST", body: JSON.stringify(cbody) });
      toast(accion === "responder" ? "Comentario publicado" : "Acción registrada", "ok");
      const fresh = await api(`/api/wp/${d.wp_id}`);
      d.comentarios = fresh.comentarios || [];
      d.blog_fase = fresh.blog_fase || {};
      renderBlog($tc, d);
    } catch (e) {
      $tc.querySelectorAll("button").forEach(b => b.disabled = false);
      $stat.innerHTML = `<span style="color:#c33">${escapeHtml(e.message)}</span>`;
    }
  };
  const wire = (id, fn) => { const b = $tc.querySelector("#" + id); if (b) b.onclick = fn; };
  wire("b-responder", () => post("responder", null));
  wire("b-delegar", () => post("delegar", ($tc.querySelector("#blog-destino") || {}).value));
  wire("b-aprobar", () => post("aprobar", null));
  wire("b-devolver", () => post("devolver", null));
  wire("b-rechazar", () => post("rechazar", null));
  wire("b-tomar", () => post("tomar", null));
  // Poblar el selector de stack/perfil (solo aparece al aprobar Recepción→Diseño).
  const $blogProfile = $tc.querySelector("#blog-profile");
  if ($blogProfile) {
    api("/api/profiles").then(cat => {
      const profs = cat.profiles || [];
      if (!profs.length) return;
      $blogProfile.innerHTML = profs.map(p =>
        `<option value="${p.key}"${p.key === cat.default_profile ? " selected" : ""}>${escapeHtml(p.title || p.key)}</option>`).join("");
    }).catch(() => {});
  }
  // ▶ Iniciar: arranca el workflow (Recepción) para un requerimiento sin iniciar.
  wire("b-iniciar", async () => {
    const b = $tc.querySelector("#b-iniciar"); if (b) b.disabled = true;
    try {
      await api(`/api/wp/${d.wp_id}/approve-and-start`, { method: "POST", body: JSON.stringify({}) });
      toast("Workflow iniciado en Recepción", "ok");
      await loadDetail(d.wp_id);
    } catch (e) { if (b) b.disabled = false; toast(e.message, "error"); }
  });
}

// Sección "Capacidad": muestra la capacidad (parent) actual y permite
// reasignarla (swf-funcional/admin) dentro del mismo proyecto.
function capacidadHtml(d) {
  const cap = d.capability || {};
  const can = !!d.can_manage_assoc;
  return `<div class="panel">
    <h3 style="margin:0 0 8px 0">🎯 Capacidad</h3>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px">
      <span>Actual: <b>${escapeHtml(cap.title || "(sin capacidad)")}</b>${cap.id ? ` <span style="color:#888">#${cap.id}</span>` : ""}</span>
      ${can ? `<select id="cap-reassign" style="font-size:13px;padding:5px 8px;border:1px solid #ccc;border-radius:6px"><option value="">(cargando…)</option></select>
      <button id="cap-reassign-btn" style="background:#5b3da8;color:#fff;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;font-weight:600">Cambiar</button>` : ""}
    </div>
  </div>`;
}

// Sección "Requisitos relacionados": lista las asociaciones 'relates' y
// permite agregar (por #) o quitar (swf-funcional/admin).
function relacionesHtml(d) {
  const rels = d.relations || [];
  const can = !!d.can_manage_assoc;
  const rows = rels.length ? rels.map(r => `<li style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #f0f0f0">
      <a href="#" class="rel-open" data-wp="${r.wp_id}" style="color:#0a6ed1;text-decoration:none">#${r.wp_id} ${escapeHtml(r.subject || "")}</a>
      ${can ? `<a href="#" class="rel-del" data-rel="${r.relation_id}" title="Quitar relación" style="color:#c0392b;text-decoration:none;margin-left:auto">✕</a>` : ""}
    </li>`).join("") : `<li style="color:#888;padding:4px 0">Sin requisitos relacionados.</li>`;
  return `<div class="panel">
    <h3 style="margin:0 0 8px 0">🔗 Requisitos relacionados</h3>
    <ul style="list-style:none;margin:0;padding:0">${rows}</ul>
    ${can ? `<div style="display:flex;align-items:center;gap:8px;margin-top:8px">
      <input id="rel-add-input" type="text" inputmode="numeric" placeholder="# de requisito" style="width:140px;padding:5px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px" />
      <button id="rel-add-btn" style="background:#0a6ed1;color:#fff;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;font-weight:600">+ Relacionar</button>
    </div>` : ""}
  </div>`;
}

function wireCapacidad($scope, d) {
  if (!d.can_manage_assoc || !d.project_id) return;
  const sel = $scope.querySelector("#cap-reassign");
  const btn = $scope.querySelector("#cap-reassign-btn");
  if (sel) api(`/api/projects/${d.project_id}/capabilities`).then(r => {
    const caps = r.items || [];
    const curId = (d.capability || {}).id;
    sel.innerHTML = caps.length
      ? caps.map(c => `<option value="${c.id}"${c.id === curId ? " selected" : ""}>${escapeHtml(c.subject)} (#${c.id})</option>`).join("")
      : '<option value="">(sin capacidades en el proyecto)</option>';
  }).catch(() => { sel.innerHTML = '<option value="">(error al cargar)</option>'; });
  if (btn) btn.onclick = async () => {
    const newCap = sel && sel.value;
    if (!newCap) { toast("Elige una capacidad", "error"); return; }
    if (String(newCap) === String((d.capability || {}).id)) { toast("Ya es esa capacidad", "error"); return; }
    btn.disabled = true;
    try {
      await api(`/api/wp/${d.wp_id}/capability`, { method: "POST", body: JSON.stringify({ capability_id: Number(newCap) }) });
      toast("Capacidad reasignada", "ok");
      await loadInbox(); await loadDetail(d.wp_id);
    } catch (e) { toast(e.message, "error"); btn.disabled = false; }
  };
}

function wireRelaciones($scope, d) {
  $scope.querySelectorAll(".rel-open").forEach(a => a.onclick = (ev) => {
    ev.preventDefault(); loadDetail(parseInt(a.dataset.wp, 10));
  });
  if (!d.can_manage_assoc) return;
  $scope.querySelectorAll(".rel-del").forEach(a => a.onclick = async (ev) => {
    ev.preventDefault();
    if (!confirm("¿Quitar esta relación?")) return;
    try {
      await api(`/api/wp/${d.wp_id}/relations/${a.dataset.rel}`, { method: "DELETE" });
      toast("Relación quitada", "ok"); await loadDetail(d.wp_id);
    } catch (e) { toast(e.message, "error"); }
  });
  const addBtn = $scope.querySelector("#rel-add-btn");
  const addIn = $scope.querySelector("#rel-add-input");
  if (addBtn && addIn) addBtn.onclick = async () => {
    const v = (addIn.value || "").trim().replace(/^#/, "");
    if (!/^\d+$/.test(v)) { toast("Escribe el # del requisito", "error"); return; }
    addBtn.disabled = true;
    try {
      const r = await api(`/api/wp/${d.wp_id}/relations`, { method: "POST", body: JSON.stringify({ to_wp_id: Number(v) }) });
      toast(r.already ? "Ya estaba relacionado" : "Relacionado", "ok");
      await loadDetail(d.wp_id);
    } catch (e) { toast(e.message, "error"); addBtn.disabled = false; }
  };
}

// Tab "Info general": datos del requisito (editar/validar en Recibido, vía el
// banner) + capacidad + requisitos relacionados + adjuntos. Absorbe lo que
// antes era la pestaña "Adjuntos".
async function renderInfoGeneral($tc, d) {
  $tc.innerHTML = recibidoBannerHtml(d) + capacidadHtml(d) + relacionesHtml(d) + adjuntosHtml(d);
  wireRecibidoBanner(d);
  wireCapacidad($tc, d);
  wireRelaciones($tc, d);
  wireAdjuntos($tc, d);
}

// --- Tipado + renombrado de adjuntos (etapa de requerimiento) ---
const TIPOS_ARCHIVO = [["especificacion", "Especificación"], ["reporte", "Reporte"],
  ["pantallas", "Pantallas"], ["transcripcion", "Transcripción"],
  ["cuestionario", "Cuestionario"], ["otro", "Otro"]];
// Artículos/preposiciones a omitir del slug (nombre del requerimiento).
const _SLUG_STOP = new Set(["de", "del", "la", "el", "los", "las", "un", "una",
  "unos", "unas", "y", "o", "u", "e", "a", "al", "en", "para", "con", "por",
  "lo", "su", "sus", "the", "of", "and", "to"]);
function slugify(s) {
  const words = String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(/\s+/)
    .filter(w => w && !_SLUG_STOP.has(w));
  return words.join("-") || "req";
}
function tipoSelectHtml(cls) {
  return `<select class="${cls}" style="font-size:11px;padding:2px 4px">${TIPOS_ARCHIVO.map(t => `<option value="${t[0]}">${t[1]}</option>`).join("")}</select>`;
}
// Nombre = slug-del-requerimiento + "-" + tipo + extensión, único en el lote.
function buildFileName(subject, tipo, origName, used) {
  const ext = (origName.includes(".") ? origName.split(".").pop() : "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const base = `${slugify(subject)}-${tipo}`;
  let name = base + (ext ? "." + ext : ""), i = 2;
  while (used.has(name)) { name = `${base}-${i}` + (ext ? "." + ext : ""); i++; }
  used.add(name);
  return name;
}

// Modal compacto de subida directa a MinIO (presigned PUT) + agregar al
// manifest del WP via /api/wp/{id}/add-files. Reutiliza el endpoint
// /api/inbox/upload-url ya disponible.
async function openAddFilesModal(d) {
  const modal = document.createElement("div");
  modal.style.cssText = "position:fixed;inset:0;background:#0008;z-index:100;display:flex;align-items:center;justify-content:center";
  modal.innerHTML = `
    <div style="background:#fff;border-radius:6px;padding:24px;width:520px;max-width:90vw">
      <h3 style="margin:0 0 12px 0">📎 Agregar archivos al requisito #${d.wp_id}</h3>
      <div style="font-size:12px;color:#666;margin-bottom:12px">
        Los archivos van directo a MinIO (object storage). El agente IA los lee al arrancar el workflow.
      </div>
      <input id="add-files-input" type="file" multiple accept=".pdf,.txt,.md,.docx,.xlsx,.csv,image/*"
             style="border:1px dashed #ccc;padding:8px;background:#fafafa;width:100%"/>
      <div id="add-files-list" style="font-size:11px;color:#666;margin-top:6px"></div>
      <div style="display:flex;gap:8px;margin-top:14px;justify-content:flex-end">
        <button id="add-files-cancel">Cancelar</button>
        <button id="add-files-submit" class="primary" style="background:#0a6ed1;color:#fff;border:none;border-radius:4px;padding:8px 16px" disabled>Subir y registrar</button>
      </div>
      <div id="add-files-status" style="margin-top:8px;font-size:12px;color:#666"></div>
    </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector("#add-files-cancel").onclick = close;
  const $input = modal.querySelector("#add-files-input");
  const $list  = modal.querySelector("#add-files-list");
  const $sub   = modal.querySelector("#add-files-submit");
  const $stat  = modal.querySelector("#add-files-status");
  $input.onchange = () => {
    const fs = Array.from($input.files || []);
    if (!fs.length) { $list.textContent = ""; $sub.disabled = true; return; }
    $list.innerHTML = fs.map(f => `<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
        <span style="flex:1">📎 ${escapeHtml(f.name)} <span style="color:#999">(${(f.size/1024/1024).toFixed(2)} MB)</span></span>
        <span style="color:#666">Tipo:</span> ${tipoSelectHtml("af-ftype")}</div>`).join("");
    $sub.disabled = false;
  };
  $sub.onclick = async () => {
    const fs = Array.from($input.files || []);
    if (!fs.length) return;
    $sub.disabled = true; $sub.textContent = "Subiendo…"; $stat.textContent = "";
    try {
      const batch_id = crypto.randomUUID();
      const ftypes = [...modal.querySelectorAll(".af-ftype")];
      const usedNames = new Set();
      const uploaded = [];
      for (let i = 0; i < fs.length; i++) {
        const file = fs[i];
        const tipo = (ftypes[i] && ftypes[i].value) || "otro";
        const newName = buildFileName(d.subject || ("req-" + d.wp_id), tipo, file.name, usedNames);
        $stat.textContent = `Subiendo ${newName}…`;
        const urlR = await api("/api/inbox/upload-url", {
          method: "POST",
          body: JSON.stringify({ batch_id, filename: newName, content_type: file.type || "application/octet-stream" }),
        });
        const putR = await fetch(urlR.put_url, {
          method: "PUT", body: file,
          headers: { "Content-Type": urlR.content_type },
        });
        if (!putR.ok) throw new Error(`MinIO PUT ${newName} → HTTP ${putR.status}`);
        uploaded.push({ filename: newName, key: urlR.key, tipo: tipo, size: file.size, content_type: file.type || urlR.content_type });
      }
      $stat.textContent = "Registrando en manifest…";
      const r = await api(`/api/wp/${d.wp_id}/add-files`, {
        method: "POST", body: JSON.stringify({ files: uploaded }),
      });
      toast(`${r.added} archivo(s) agregado(s) al manifest (total: ${r.total})`, "ok");
      close();
      await loadDetail(d.wp_id);
    } catch (e) {
      $sub.disabled = false; $sub.textContent = "Subir y registrar";
      $stat.innerHTML = `<span style="color:#c33">${escapeHtml(e.message)}</span>`;
    }
  };
}

// Bloque compacto con el repo del requerimiento (clone URL + link a Gitea) para
// que el humano sepa dónde trabajar el paquete con Claude Code + MCP. Vacío si el
// repo aún no se provisionó. Ver [[rediseno-fase-diseno-mcp]].
function repoCloneHint(d) {
  const repo = d.dev_repo || {};
  if (!repo.clone_url) return "";
  const web = repo.web_url
    ? ` &nbsp;·&nbsp; <a href="${repo.web_url}" target="_blank" style="color:#0a6ed1">📁 Abrir en Gitea</a>` : "";
  return `<div style="margin-top:8px"><b>Repo del requerimiento</b>${web}` +
    `<pre style="background:#1e1e1e;color:#e6e6e6;padding:6px 9px;border-radius:5px;font-size:12px;overflow:auto;margin:4px 0 0">git clone ${escapeHtml(repo.clone_url)}</pre></div>`;
}

function renderAcciones($tc, d) {
  const stage = d.stage || "";
  const pending = stage.includes("esperando_revision") ? "review"
    : (stage === "esperando_aprobacion_humana" || stage.includes("esperando_vobo") || stage === "esperando_liberacion_pruebas" || stage === "esperando_paquete_diseno" || stage === "esperando_estimacion") ? "gate"
    : stage === "esperando_clarificacion" ? "clarification"
    : null;
  // Bloque que esta esperando feedback (especificacion / prototipo / ...)
  const reviewBlock = pending === "review"
    ? (stage.split(".")[0] || "bloque") : "";
  let actionsHtml = '<div class="panel"><h3>Acciones disponibles</h3>';
  if (pending === "review") {
    const reviewBanner = reviewBlock === "desarrollo"
      ? `🔴 <b>Revisión de Desarrollo</b> — el agente entregó los artefactos de código. Revisa / despliega en dev / prueba y decide: iterar con comentarios (vuelve al agente) o aprobar para QA. Rol: <span class="role-badge role-swf-desarrollador">SwF Desarrollador</span>`
      : `🔴 <b>Decisión 1 · Revisión Fábrica</b> — validación funcional <b>interna</b> de la fábrica. Rol: <span class="role-badge role-swf-funcional">SwF Funcional</span>`;
    actionsHtml += `
      <div style="background:#e3f2fd;border-left:4px solid #0a6ed1;padding:8px 12px;border-radius:4px;margin-bottom:10px;font-size:13px;">${reviewBanner}</div>
      <p>Esperando revision del bloque <b>${escapeHtml(reviewBlock)}</b> (stage actual: <code>${escapeHtml(stage)}</code>).</p>
      <form id="review-form">
        <label>Accion</label>
        <select name="action">
          <option value="iterate">iterate (re-correr el agente con feedback)</option>
          <option value="approve">approve (promover esta iteracion y avanzar)</option>
          <option value="reject">reject (abortar el bloque)</option>
        </select>
        <label>Modelo (solo si iterate y quieres cambiarlo)</label>
        <select name="model">
          <option value="">(mantener el actual)</option>
          <option value="claude-haiku-4-5-20251001">Haiku 4.5 (rapido y barato)</option>
          <option value="claude-sonnet-4-6">Sonnet 4.6 (mas potencia)</option>
          <option value="claude-opus-4-7">Opus 4.7 (maxima)</option>
        </select>
        ${reviewBlock === "desarrollo" ? `
        <label>🛠️ Skill de desarrollo (solo si iterate y quieres CAMBIARLO)</label>
        <select name="skill_key" id="rev-skill-select"><option value="">(mantener el actual)</option></select>` : ""}
        <label>Instrucciones para la siguiente iteracion</label>
        <textarea name="instructions" placeholder="Ej: expandi la seccion de NFRs, recorta los mockups conceptuales"></textarea>
        <label>Puntos a mejorar (uno por linea)</label>
        <textarea name="focus_points" placeholder="Ej: Sesiones expiradas y refresh&#10;Mapeo de roles SAP"></textarea>
        <label>Contexto adicional (respuestas a preguntas, decisiones, etc.)</label>
        <textarea name="additional_context"></textarea>
        <label>Tu nombre (actor)</label>
        <input name="actor" placeholder="rodolfo"/>
        <button type="submit" class="primary">Enviar feedback</button>
      </form>`;
  } else if (pending === "gate") {
    const gateBanner = stage === "esperando_vobo_funcional"
      ? `🔴 <b>VoBo 1 · Consultor funcional</b> — validación funcional del lado cliente (1º de 2). Rol: <span class="role-badge role-funcional">Consultor funcional</span>. Al aprobar, pasa al Usuario clave.`
      : stage === "esperando_vobo_usuario_clave"
      ? `🔴 <b>VoBo 2 · Usuario clave</b> — validación FINAL del usuario (2º de 2). Rol: <span class="role-badge role-usuario-clave">Usuario clave</span>. Al aprobar, queda listo para desarrollo.`
      : stage === "esperando_liberacion_pruebas"
      ? `🚀 <b>Liberar a pruebas</b> — el desarrollo (local, en el repo del requerimiento) está listo y los entregables publicados. Al aprobar, pasa a <b>Lista para pruebas</b> (QA). Rol: <span class="role-badge role-swf-desarrollador">SwF Desarrollador</span>. Ver el repo en la pestaña <b>Desarrollo</b>.`
      : stage === "esperando_estimacion"
      ? `📐 <b>Estimación requerida</b> — el flujo NO sigue sin estimación. Genera/ajusta el estimado en el tab <b>Plan</b> (define la <b>fecha inicial</b>) y luego aprueba esta compuerta. Rol: <span class="role-badge role-swf-funcional">SwF Funcional</span>`
      : stage === "esperando_paquete_diseno"
      ? `📦 <b>Generando paquete</b> — produce el <b>paquete de diseño</b> (spec + prototipo + casos + soporte) <b>localmente con Claude Code</b> en el repo del requerimiento, escríbelo en <code>diseno/</code> y publícalo por <b>git push</b> (<code>git add diseno/ && git commit && git push</code>). Cuando esté listo, aprueba aquí (o usa la tool <code>enviar_a_revision</code>) para pasar a <b>Revisión Fábrica</b>. Rol: <span class="role-badge role-swf-funcional">SwF Funcional</span>.${repoCloneHint(d)}`
      : `🔴 <b>Validación Cliente</b> — VoBo del <b>cliente</b>. Roles: <span class="role-badge role-funcional">Consultor</span> <span class="role-badge role-usuario-clave">Usuario clave</span>`;
    // Ayudante OPCIONAL en la compuerta de diseño: borrador one-shot del
    // paquete con IA (♻️ si ya hay uno). No avanza la etapa. Ver
    // [[rediseno-fase-diseno-mcp]].
    const hayBorrador = !!((d.spec_state || {}).has_vigente);
    const draftHtml = stage === "esperando_paquete_diseno" ? `
      <div style="background:#f3e5f5;border-left:4px solid #8e24aa;padding:8px 12px;border-radius:4px;margin-bottom:10px;font-size:13px">
        <div style="margin-bottom:6px">🤖 <b>Ayudante opcional</b> — genera un <b>borrador</b> de la spec con IA y lo <b>postea al blog</b> como punto de partida (no toca el repo). Cópialo a <code>diseno/spec.md</code> en tu clon, refínalo con Claude Code y publícalo por git push. No avanza la etapa.</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <select id="draft-model" style="font:inherit;font-size:12px;padding:4px 6px;border:1px solid #c0a0d0;border-radius:4px">
            <option value="">(modelo por defecto)</option>
            <option value="claude-haiku-4-5-20251001">Haiku 4.5 (rapido)</option>
            <option value="claude-sonnet-4-6">Sonnet 4.6</option>
            <option value="claude-opus-4-7">Opus 4.7 (maxima)</option>
          </select>
          <button id="draft-btn" style="background:#8e24aa;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:600;font-size:12px">${hayBorrador ? "♻️ Regenerar borrador con IA" : "🤖 Generar borrador con IA"}</button>
        </div>
      </div>` : "";
    actionsHtml += `
      <div style="background:#fff3e0;border-left:4px solid #df6e0c;padding:8px 12px;border-radius:4px;margin-bottom:10px;font-size:13px;">${gateBanner}</div>
      ${draftHtml}
      <form id="decision-form">
        ${stage === "esperando_estimacion" ? `<label>📅 Fecha inicial del desarrollo (manual)</label><input type="date" name="start_date" required/>` : ""}
        <label>Decision del gate humano</label>
        <select name="decision">
          <option value="approved">approve</option>
          <option value="rejected">reject</option>
        </select>
        <label>Nota</label>
        <textarea name="note"></textarea>
        <label>Tu nombre</label>
        <input name="actor" placeholder="rodolfo"/>
        <button type="submit" class="primary">Enviar decision</button>
      </form>`;
  } else if (pending === "clarification") {
    const qs = d.open_questions || [];
    const qsHtml = qs.length
      ? `<ol style="margin:6px 0 10px;padding-left:20px">${qs.map(q => `<li style="margin-bottom:5px">${escapeHtml(q)}</li>`).join("")}</ol>`
      : `<p style="color:#842029">El agente pidió aclaraciones, pero no se pudieron listar las preguntas aquí. Revísalas en el blog del requerimiento.</p>`;
    const prevModel = d.clarification_model || "";
    actionsHtml += `
      <div style="background:#fff3f3;border:1px solid #f0c0c0;border-radius:4px;padding:10px 12px;margin-bottom:10px">
        <div style="font-weight:600;color:#842029;margin-bottom:6px">❓ El agente necesita aclaraciones para poder especificar${prevModel ? ` (modelo: ${escapeHtml(prevModel)})` : ""}:</div>
        ${qsHtml}
        <p style="font-size:13px;color:#555;margin:8px 0 4px">Corrige el insumo (p.ej. <b>re-sube el PDF de la transcripción</b> con esos detalles) y re-envía. Sugerencia: usa un modelo mayor para reducir bloqueos.</p>
        <button id="clar-upload-btn" type="button" class="secondary" style="margin-top:4px">📎 Re-subir transcripción / archivo</button>
      </div>
      <form id="clarified-form">
        <label>Modelo al re-enviar</label>
        <select name="model" id="clar-model">
          <option value="claude-sonnet-4-6" selected>claude-sonnet-4-6 (sugerido)</option>
          <option value="">Mantener el actual${prevModel ? ` (${escapeHtml(prevModel)})` : ""}</option>
          <option value="claude-opus-4-7">claude-opus-4-7 (máxima calidad)</option>
        </select>
        <button type="submit" class="primary">🔄 Re-enviar al agente</button>
      </form>`;
  } else {
    actionsHtml += '<div class="empty">No hay acciones pendientes — el workflow no esta esperando humano.</div>';
  }
  actionsHtml += '</div>';

  actionsHtml += `
    <div class="panel">
      <h3>Comentar al Work Package</h3>
      <form id="comment-form">
        <label>Comentario</label>
        <textarea name="text" required></textarea>
        <label>Tu nombre</label>
        <input name="actor"/>
        <button type="submit" class="secondary">Postear comentario</button>
      </form>
    </div>`;

  $tc.innerHTML = actionsHtml;

  // Poblar el catálogo de skills en la revisión de DESARROLLO (cambiar skill al iterar).
  const revSkillSel = document.getElementById("rev-skill-select");
  if (revSkillSel) {
    api("/api/skills").then(cat => {
      const skills = cat.skills || [];
      if (skills.length) {
        revSkillSel.innerHTML = `<option value="">(mantener el actual)</option>` +
          skills.map(s => `<option value="${escapeHtml(s.key)}">${escapeHtml(s.title || s.key)}</option>`).join("");
      }
    }).catch(() => {});
  }

  const reviewForm = document.getElementById("review-form");
  if (reviewForm) reviewForm.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(reviewForm);
    const body = {
      action: fd.get("action"),
      model: fd.get("model") || "",
      instructions: fd.get("instructions") || "",
      focus_points: (fd.get("focus_points") || "").split("\n").map(s => s.trim()).filter(Boolean),
      additional_context: fd.get("additional_context") || "",
      actor: fd.get("actor") || "",
      skill_key: fd.get("skill_key") || "",
    };
    try {
      await api(`/api/wp/${currentWpId}/review`, { method: "POST", body: JSON.stringify(body) });
      toast("Feedback enviado. El workflow re-corre o avanza segun la accion.", "ok");
      setTimeout(() => loadDetail(currentWpId), 1500);
    } catch (err) { toast(err.message, "error"); }
  };

  // Poblar el catálogo de skills (multi-select) en la compuerta liberar desarrollo.
  const skillMulti = document.getElementById("skill-multi");
  if (skillMulti) {
    api("/api/skills").then(cat => {
      const skills = cat.skills || [];
      if (!skills.length) {
        skillMulti.innerHTML = '<span style="color:#888">(sin catálogo — el agente usa el skill por defecto)</span>';
        return;
      }
      skillMulti.innerHTML = skills.map(s =>
        `<label style="display:block;padding:3px 2px;white-space:nowrap"><input type="checkbox" class="skill-cb" value="${escapeHtml(s.key)}"${s.key === cat.default ? " checked" : ""}/> <b>${escapeHtml(s.title || s.key)}</b>${s.when_to_use ? ` <span style="color:#999;font-size:11px">— ${escapeHtml(s.when_to_use)}</span>` : ""}</label>`
      ).join("");
    }).catch(() => {
      skillMulti.innerHTML = '<span style="color:#c0392b">(no se pudo cargar el catálogo)</span>';
    });
  }

  const decForm = document.getElementById("decision-form");
  if (decForm) decForm.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(decForm);
    const body = { decision: fd.get("decision"), note: fd.get("note"), actor: fd.get("actor") };
    const sd = fd.get("start_date"); if (sd) body.start_date = sd;
    const sks = [...decForm.querySelectorAll(".skill-cb:checked")].map(c => c.value);
    if (sks.length) { body.skill_keys = sks; body.skill_key = sks[0]; }
    const pkg = fd.get("sap_package"); if (pkg) body.sap_package = pkg.trim();
    try {
      await api(`/api/wp/${currentWpId}/decision`, { method: "POST", body: JSON.stringify(body) });
      toast("Decision del gate enviada.", "ok");
      setTimeout(() => loadDetail(currentWpId), 1500);
    } catch (err) { toast(err.message, "error"); }
  };

  // Ayudante opcional: borrador one-shot del paquete con IA (compuerta de diseño).
  const draftBtn = document.getElementById("draft-btn");
  if (draftBtn) draftBtn.onclick = async () => {
    const model = (document.getElementById("draft-model") || {}).value || "";
    if (!confirm("¿Generar un borrador de spec con IA" +
                 (model ? (" usando " + model) : "") +
                 "?\n\nSe postea al BLOG como punto de partida (no toca el repo). La etapa NO avanza.")) return;
    draftBtn.disabled = true; const prev = draftBtn.textContent;
    draftBtn.textContent = "⏳ Generando…";
    try {
      await api(`/api/wp/${currentWpId}/generar-borrador`, {
        method: "POST", body: JSON.stringify({ model }) });
      toast("Borrador solicitado. Aparecerá en el BLOG en ~1-2 min.", "ok");
      setTimeout(() => loadDetail(currentWpId), 3000);
    } catch (err) {
      draftBtn.disabled = false; draftBtn.textContent = prev;
      toast(err.message, "error");
    }
  };

  const clarUploadBtn = document.getElementById("clar-upload-btn");
  if (clarUploadBtn) clarUploadBtn.onclick = () => openAddFilesModal(d);

  const clarForm = document.getElementById("clarified-form");
  if (clarForm) clarForm.onsubmit = async (e) => {
    e.preventDefault();
    const model = (document.getElementById("clar-model") || {}).value || "";
    if (!confirm("Re-enviar al agente" + (model ? (" con " + model) : "") +
                 "?\n\nUsará el insumo actual (incluye lo que hayas re-subido).")) return;
    try {
      await api(`/factory/${currentWpId}/clarified`, {
        method: "POST", body: JSON.stringify({ model }) });
      toast("Re-enviado al agente" + (model ? (" con " + model) : "") + ".", "ok");
      setTimeout(() => loadDetail(currentWpId), 1500);
    } catch (err) { toast(err.message, "error"); }
  };

  const cForm = document.getElementById("comment-form");
  if (cForm) cForm.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(cForm);
    try {
      await api(`/api/wp/${currentWpId}/comment`, { method: "POST",
        body: JSON.stringify({ text: fd.get("text"), actor: fd.get("actor") }) });
      toast("Comentario posteado.", "ok");
      cForm.reset();
    } catch (err) { toast(err.message, "error"); }
  };
}

// ============================================================
// Usuario actual (de la sesión OIDC con Keycloak)
// ============================================================
let currentUser = null;

async function loadCurrentUser() {
  try {
    const r = await api("/auth/me");
    if (r.authenticated) {
      currentUser = r.user;
      const badge = document.getElementById("user-badge");
      if (badge) {
        const roles = (currentUser.roles || []).join(", ") || "(sin rol)";
        badge.textContent = `${currentUser.username || currentUser.name} · ${roles}`;
      }
    }
  } catch (e) { /* sin auth, modo dev */ }
}

const logoutBtn = document.getElementById("logout-btn");
if (logoutBtn) logoutBtn.onclick = () => { window.location = "/auth/logout"; };

// Drawer lateral (menú ☰): abrir/cerrar con el botón, la ✕ o el overlay.
// Al elegir una vista (Inbox/Estado del proyecto) se cierra solo.
(function () {
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("drawer-overlay");
  if (!drawer || !overlay) return;
  const openDrawer = () => { drawer.classList.add("show"); overlay.classList.add("show"); };
  const closeDrawer = () => { drawer.classList.remove("show"); overlay.classList.remove("show"); };
  const mb = document.getElementById("menu-btn");
  if (mb) mb.onclick = openDrawer;
  const dc = document.getElementById("drawer-close");
  if (dc) dc.onclick = closeDrawer;
  overlay.onclick = closeDrawer;
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
  document.querySelectorAll("#drawer #view-switch button").forEach(b =>
    b.addEventListener("click", closeDrawer));
})();

// ============================================================
// ANOTACIONES sobre la iteración actual del bloque 'diseno'
// ============================================================
let currentAnnIteration = null;
let currentAnnWpId = null;

function setupAnnotations(wpId, iteration) {
  currentAnnWpId = wpId;
  currentAnnIteration = iteration;
  // Cargar y mostrar contador
  refreshAnnCount();
  // Botones del panel
  const gBtn = document.getElementById("ann-global-btn");
  if (gBtn) gBtn.onclick = () => openAnnModal({ target: "global" });
  const vBtn = document.getElementById("ann-view-btn");
  if (vBtn) vBtn.onclick = () => openAnnListModal();
  const cBtn = document.getElementById("ann-clear-btn");
  if (cBtn) cBtn.onclick = () => clearAnnotations();
  const sBtn = document.getElementById("ann-synth-btn");
  if (sBtn) sBtn.onclick = () => openSynthModal();
  const apBtn = document.getElementById("ann-approve-btn");
  if (apBtn) apBtn.onclick = () => approveDesign();
  const inBtn = document.getElementById("inputs-btn");
  if (inBtn) inBtn.onclick = () => showTab("adjuntos");
  // Selección de texto sobre la spec
  const specEl = document.getElementById("diseno-spec");
  if (specEl && !specEl.dataset.annBound) {
    specEl.addEventListener("mouseup", onSpecSelection);
    specEl.dataset.annBound = "1";
  }
}

// D8: descarta las anotaciones pendientes archivándolas en
// anotaciones-descartadas/. Recuperables desde Gitea — no se pierde nada.
async function clearAnnotations() {
  if (!currentAnnWpId) return;
  const cntR = await api(`/api/wp/${currentAnnWpId}/diseno/annotations`).catch(() => ({ count: 0 }));
  const n = cntR.count || 0;
  if (n === 0) { toast("No hay anotaciones pendientes", "ok"); return; }
  if (!confirm(`¿Descartar las ${n} anotación(es) pendientes?\n\nNO se eliminan permanentemente — se archivan en especificacion/anotaciones-descartadas/ por si las necesitas recuperar (visible en Gitea).`)) return;
  try {
    const r = await api(`/api/wp/${currentAnnWpId}/diseno/annotations`, { method: "DELETE" });
    toast(`${r.archived} anotación(es) archivadas en ${r.target.split("/").slice(-2).join("/")}`, "ok");
    await refreshAnnCount();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function refreshAnnCount() {
  if (!currentAnnWpId || !currentAnnIteration) return;
  try {
    const r = await api(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/annotations`);
    const c = document.getElementById("ann-count");
    if (c) c.textContent = r.count;
    const bar = document.getElementById("ann-bar");
    if (bar) bar.classList.toggle("empty", r.count === 0);
  } catch (e) { /* silencio */ }
}

function dismissAnnPopover() {
  const old = document.getElementById("ann-popover");
  if (old) old.remove();
}

function onSpecSelection(e) {
  setTimeout(() => {
    const sel = window.getSelection();
    const txt = sel ? sel.toString().trim() : "";
    dismissAnnPopover();
    if (!txt || txt.length < 4) return;
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    showAnnPopover(txt, "spec", rect);
  }, 10);
}

function showAnnPopover(text, target, rect) {
  const pop = document.createElement("div");
  pop.id = "ann-popover";
  pop.className = "ann-popover";
  pop.textContent = "💬 Comentar";
  pop.style.top = (rect.bottom + window.scrollY + 4) + "px";
  pop.style.left = (rect.left + window.scrollX) + "px";
  pop.onclick = () => {
    dismissAnnPopover();
    openAnnModal({ target: target, selected_text: text });
  };
  document.body.appendChild(pop);
  // Auto-dismiss tras 4s si no se usa
  setTimeout(() => { if (document.getElementById("ann-popover") === pop) pop.remove(); }, 4000);
}

// postMessage desde el iframe del prototipo
window.addEventListener("message", (e) => {
  if (!e.data || e.data.type !== "fabro:selection") return;
  const txt = e.data.text;
  if (!txt) return;
  dismissAnnPopover();
  // El rect viene relativo al iframe; convertir a viewport del parent
  const iframe = document.getElementById("diseno-iframe");
  if (!iframe || !e.data.rect) return;
  const ir = iframe.getBoundingClientRect();
  const rect = {
    top: ir.top + e.data.rect.top,
    bottom: ir.top + e.data.rect.bottom,
    left: ir.left + e.data.rect.left,
    right: ir.left + e.data.rect.right,
  };
  showAnnPopover(txt, "prototipo", rect);
});

function openAnnModal(opts) {
  const targetLabel = { spec: "Spec", prototipo: "Prototipo", global: "General" }[opts.target] || opts.target;
  const quotedHtml = opts.selected_text ? `<div class="quoted">${escapeHtml(opts.selected_text.slice(0, 400))}${opts.selected_text.length > 400 ? "…" : ""}</div>` : '';
  const userBadge = currentUser
    ? `<div style="font-size:12px;color:#666;margin-bottom:8px">Comentando como <b>${escapeHtml(currentUser.username || currentUser.name)}</b> (rol: <span class="role-badge role-${primaryRole(currentUser.roles)}">${primaryRole(currentUser.roles)}</span>)</div>`
    : '';
  const modal = document.createElement("div");
  modal.className = "ann-modal-bg";
  modal.innerHTML = `
    <div class="ann-modal">
      <h3>💬 Anotación sobre <b>${escapeHtml(targetLabel)}</b></h3>
      ${userBadge}
      ${quotedHtml}
      <label>Comentario</label>
      <textarea id="ann-comment" placeholder="Describe el cambio que pides, la duda, o la observación..."></textarea>
      <div class="buttons">
        <button id="ann-cancel">Cancelar</button>
        <button id="ann-save" class="primary" style="background:#0a6ed1;color:#fff;border:none;border-radius:4px">Guardar anotación</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.querySelector("#ann-cancel").onclick = () => modal.remove();
  modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
  setTimeout(() => modal.querySelector("#ann-comment").focus(), 50);
  modal.querySelector("#ann-save").onclick = async () => {
    const comment = modal.querySelector("#ann-comment").value.trim();
    if (!comment) { toast("escribe un comentario", "error"); return; }
    try {
      await api(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/annotation`, {
        method: "POST",
        body: JSON.stringify({
          target: opts.target,
          selected_text: opts.selected_text || "",
          comment,
        }),
      });
      toast("anotación guardada", "ok");
      modal.remove();
      refreshAnnCount();
    } catch (err) { toast(err.message, "error"); }
  };
}

function primaryRole(roles) {
  if (!roles) return "swf-funcional";
  for (const r of ["usuario-clave", "funcional", "swf-funcional",
                    "swf-desarrollador", "swf-tester"]) {
    if (roles.includes(r)) return r;
  }
  return roles[0] || "swf-funcional";
}

async function openAnnListModal() {
  const r = await api(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/annotations`);
  const modal = document.createElement("div");
  modal.className = "ann-modal-bg";
  const itemsHtml = r.items.length === 0
    ? '<div class="empty">No hay anotaciones todavía.</div>'
    : r.items.map(a => `
        <div class="ann-item" data-id="${a.id}">
          <div class="body">
            <div class="meta">
              <span class="role-badge role-${a.role}">${a.role}</span>
              <b>${escapeHtml(a.actor)}</b> · ${a.created_at} · target: <b>${a.target}</b>
            </div>
            ${a.selected_text ? `<div class="quoted">${escapeHtml(a.selected_text.slice(0, 200))}${a.selected_text.length > 200 ? "…" : ""}</div>` : ''}
            <div class="comment">${escapeHtml(a.comment)}</div>
          </div>
          <button class="del" title="Eliminar">✕</button>
        </div>`).join("");
  modal.innerHTML = `
    <div class="ann-modal" style="width: 720px;">
      <h3>📋 Anotaciones de la iteración <b>${escapeHtml(currentAnnIteration)}</b> (${r.count})</h3>
      <div class="ann-list">${itemsHtml}</div>
      <div class="buttons"><button id="ann-list-close">Cerrar</button></div>
    </div>`;
  document.body.appendChild(modal);
  modal.querySelector("#ann-list-close").onclick = () => modal.remove();
  modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
  modal.querySelectorAll(".ann-item .del").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.closest(".ann-item").dataset.id;
      if (!confirm("¿Eliminar esta anotación?")) return;
      try {
        const resp = await fetch(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/annotation/${id}`, { method: "DELETE" });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        btn.closest(".ann-item").remove();
        refreshAnnCount();
      } catch (err) { toast(err.message, "error"); }
    };
  });
}

let _synthInFlight = false;

async function openSynthModal() {
  // Guard contra doble click — solo un synth a la vez.
  if (_synthInFlight) {
    toast("Ya hay una síntesis en curso, espera…", "error");
    return;
  }
  _synthInFlight = true;
  // Deshabilitar el botón mientras dura la llamada
  const btn = document.getElementById("ann-synth-btn");
  if (btn) btn.disabled = true;

  // Mostrar el modal de inmediato (sin esperar a getAnnCount — esa request
  // ya se hizo y está cached en el badge "ann-count" del bar).
  const annCount = document.getElementById("ann-count")?.textContent || "?";
  const synthModal = document.createElement("div");
  synthModal.className = "ann-modal-bg";
  synthModal.innerHTML = `
    <div class="ann-modal synth-modal" style="width: 720px;">
      <h3>🧠 Sintetizando ${escapeHtml(String(annCount))} anotaciones con Sonnet…</h3>
      <div class="empty" id="synth-status">Llamando a la IA, esto toma 5-15 segundos…</div>
      <div class="buttons" style="margin-top: 12px;"><button id="synth-abort">Cancelar</button></div>
    </div>`;
  document.body.appendChild(synthModal);
  // Permitir cerrar el modal (cancela visualmente; la request al backend
  // sigue corriendo en background pero el modal se cierra).
  const close = () => { synthModal.remove(); _synthInFlight = false; if (btn) btn.disabled = false; };
  synthModal.querySelector("#synth-abort").onclick = close;
  synthModal.onclick = (e) => { if (e.target === synthModal) close(); };

  let synth;
  try {
    synth = await api(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/synthesize`, { method: "POST" });
  } catch (err) {
    if (!document.body.contains(synthModal)) { _synthInFlight = false; if (btn) btn.disabled = false; return; }
    synthModal.querySelector("#synth-status").innerHTML = `<span style="color:#c33">Error: ${escapeHtml(err.message)}</span>`;
    setTimeout(() => { synthModal.remove(); _synthInFlight = false; if (btn) btn.disabled = false; }, 5000);
    return;
  }
  // Si el usuario ya cerró el modal mientras esperábamos, no pintamos.
  if (!document.body.contains(synthModal)) { _synthInFlight = false; if (btn) btn.disabled = false; return; }
  // Pintar el prompt editable y permitir enviar
  const fp = (synth.focus_points || []).join("\n");
  synthModal.querySelector(".ann-modal").innerHTML = `
    <h3>🧠 Prompt sintetizado por <b>${escapeHtml(synth.model || "ia")}</b> — revisa y envía</h3>
    <label>Instrucciones (texto del feedback)</label>
    <textarea id="synth-instr">${escapeHtml(synth.instructions || "")}</textarea>
    <label>Focus points (uno por línea)</label>
    <textarea id="synth-fp" style="min-height: 100px">${escapeHtml(fp)}</textarea>
    <label>Modelo para esta iteración del agente de diseño</label>
    <select id="synth-model">
      <option value="">(mantener el actual)</option>
      <option value="claude-haiku-4-5-20251001">Haiku 4.5 (barato, cap 16k)</option>
      <option value="claude-sonnet-4-6">Sonnet 4.6 (potente, cap 64k)</option>
      <option value="claude-opus-4-7">Opus 4.7 (máxima calidad, cap 32k)</option>
    </select>
    <div class="buttons">
      <button id="synth-cancel">Cancelar</button>
      <button id="synth-send" class="primary" style="background:#0a6ed1;color:#fff;border:none;border-radius:4px;padding:8px 16px">Enviar al agente (iterate)</button>
    </div>`;
  const cleanup = () => { synthModal.remove(); _synthInFlight = false; if (btn) btn.disabled = false; };
  synthModal.querySelector("#synth-cancel").onclick = cleanup;
  synthModal.querySelector("#synth-send").onclick = async () => {
    const sendBtn = synthModal.querySelector("#synth-send");
    sendBtn.disabled = true;
    sendBtn.textContent = "Enviando...";
    const instr = synthModal.querySelector("#synth-instr").value;
    const fpLines = synthModal.querySelector("#synth-fp").value
      .split("\n").map(s => s.trim()).filter(Boolean);
    const model = synthModal.querySelector("#synth-model").value;
    try {
      await api(`/api/wp/${currentAnnWpId}/review`, {
        method: "POST",
        body: JSON.stringify({
          action: "iterate",
          instructions: instr,
          focus_points: fpLines,
          model: model,
          actor: (currentUser && currentUser.username) || "anónimo",
        }),
      });
      // D8: archivar las anotaciones aplicadas — se mueven a
      // anotaciones-aplicadas/iter-NNN.json y se limpian las pendientes
      // para que la próxima iter NO reciba las viejas mezcladas con nuevas.
      try {
        const arch = await api(`/api/wp/${currentAnnWpId}/diseno/annotations/archive`, {
          method: "POST", body: JSON.stringify({}),
        });
        if (arch.archived > 0) {
          toast(`feedback enviado — ${arch.archived} anotación(es) archivadas en aplicadas/`, "ok");
        } else {
          toast("feedback enviado — el agente está generando la próxima iter", "ok");
        }
      } catch (e) {
        // No falla el iterate si el archive falla; solo aviso
        toast(`feedback enviado, pero archivado falló: ${e.message}`, "error");
      }
      cleanup();
      setTimeout(() => loadDetail(currentAnnWpId), 1500);
    } catch (err) {
      sendBtn.disabled = false;
      sendBtn.textContent = "Enviar al agente (iterate)";
      toast(err.message, "error");
    }
  };
}

async function approveDesign() {
  if (!currentAnnWpId || !currentAnnIteration) {
    toast("No hay iteración activa para aprobar", "error");
    return;
  }
  const annR = await api(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/annotations`).catch(() => ({ count: 0 }));
  const pendingAnn = annR.count || 0;
  let msg = `¿Aprobar el diseño ${currentAnnIteration} del Requisito #${currentAnnWpId}?\n\n`;
  msg += `Esta versión queda como artefacto vigente y el workflow avanza al gate del consultor funcional.\n\n`;
  if (pendingAnn > 0) {
    msg += `⚠️ Hay ${pendingAnn} anotación(es) sin procesar — se PERDERÁN al aprobar (ya no entran al agente).\n`;
    msg += `Si querías iterar primero, cancela y usa "🧠 Iterar con feedback".\n\n`;
  }
  msg += `¿Continuar con APPROVE?`;
  if (!confirm(msg)) return;
  const apBtn = document.getElementById("ann-approve-btn");
  if (apBtn) { apBtn.disabled = true; apBtn.textContent = "Aprobando…"; }
  try {
    await api(`/api/wp/${currentAnnWpId}/review`, {
      method: "POST",
      body: JSON.stringify({
        action: "approve",
        actor: (currentUser && currentUser.username) || "anónimo",
        instructions: "",
        focus_points: [],
      }),
    });
    toast(`Diseño aprobado. El workflow avanza al gate funcional.`, "ok");
    setTimeout(() => loadDetail(currentAnnWpId), 1500);
  } catch (err) {
    if (apBtn) { apBtn.disabled = false; apBtn.textContent = "✅ Aprobar diseño"; }
    toast("Error: " + err.message, "error");
  }
}

async function getAnnCount() {
  try {
    const r = await api(`/api/wp/${currentAnnWpId}/diseno/${currentAnnIteration}/annotations`);
    return r.count;
  } catch (e) { return "?"; }
}

document.getElementById("refresh-btn").onclick = () => {
  loadInbox();
  if (currentWpId) loadDetail(currentWpId);
};

// Selector de proyecto: al cambiar, refiltra la vista activa.
const projSelect = document.getElementById("project-select");
if (projSelect) projSelect.onchange = async () => {
  currentProject = projSelect.value;
  localStorage.setItem("inboxProject", currentProject);
  // La capacidad es por-proyecto: al cambiar de proyecto, recargar capacidades.
  currentCapability = ""; localStorage.setItem("inboxCapability", "");
  await loadMyCapabilities();
  refreshActiveView();
};
const capSelect = document.getElementById("capability-select");
if (capSelect) capSelect.onchange = () => {
  currentCapability = capSelect.value;
  localStorage.setItem("inboxCapability", currentCapability);
  refreshActiveView();
};
// Búsqueda de texto libre: filtra la lista cargada en vivo (sin recargar).
const listFilter = document.getElementById("list-filter");
if (listFilter) {
  listFilter.value = currentFilter;
  listFilter.addEventListener("input", () => {
    currentFilter = listFilter.value;
    localStorage.setItem("inboxFilter", currentFilter);
    const shown = applyListFilter();   // instantáneo sobre lo cargado (todos los campos)
    clearTimeout(_filterTimer);
    // Si no hay coincidencia local, preguntar al servidor (por # o título en
    // TODO OpenProject, no solo la página cargada).
    if (currentFilter.trim() && shown === 0) {
      _filterTimer = setTimeout(() => loadInbox(currentFilter.trim()), 350);
    }
  });
}

// ============================================================
// Conmutador de vistas (Inbox / Estado del proyecto). Extensible.
// ============================================================
let currentView = "inbox";
function refreshActiveView() {
  if (currentView === "inbox") loadInbox();
  else if (currentView === "proyecto") renderResumen();
}
function showView(name) {
  currentView = name;
  document.querySelectorAll("#view-switch button").forEach(b =>
    b.classList.toggle("active", b.dataset.view === name));
  const vi = document.getElementById("view-inbox");
  const vp = document.getElementById("view-proyecto");
  if (vi) vi.style.display = (name === "inbox") ? "" : "none";
  if (vp) vp.style.display = (name === "proyecto") ? "" : "none";
  refreshActiveView();
}
document.querySelectorAll("#view-switch button").forEach(btn => {
  btn.onclick = () => showView(btn.dataset.view);
});

// Gantt editable del inbox: columnas (WP/Prioridad/Req/Funcional/Desarrollador/
// Usuario clave/Inicio/Fin/Horas) editables, filtros (prioridad/inicio/fin),
// total de esfuerzo y grid semanal. Se monta en $mount y maneja su propio estado.
function renderGantt(caps, opts, $mount) {
  opts = opts || {};
  const prios = opts.prios || [], canPrio = opts.canPrio && prios.length;
  const canEdit = !!opts.canEdit;
  const UF = opts.uf || [], UD = opts.ud || [], UC = opts.uc || [];
  const listFor = field => field==="funcional"?UF:(field==="desarrollador"?UD:UC);
  const byWp = {}; caps.forEach(c => c.requisitos.forEach(r => byWp[r.id] = r));
  const WK = 66, ROWH = 26, DAY = 86400000;
  const MES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
  const ETAPA_LBL = {recepcion:"Recepción",diseno:"Diseño",desarrollo:"Desarrollo",pruebas_funcionales:"Pruebas Func.",pruebas_usuario:"Pruebas Usuario",cerrado:"Cerrado"};
  const ETAPA_COL = {recepcion:"#6a6d70",diseno:"#0a6ed1",desarrollo:"#d97400",pruebas_funcionales:"#8a5cd1",pruebas_usuario:"#c0398a",cerrado:"#107e3e"};
  const ROL_LBL = {"swf-funcional":"Funcional Fábrica","funcional":"Funcional","usuario-clave":"Usuario clave","swf-desarrollador":"Desarrollador","swf-tester":"QA","admin":"Admin","project-manager":"Líder Proyecto","swf-project-manager":"Líder Proyecto"};
  const PRIO_COL = {urgente:"#bb0000",alta:"#d97400",media:"#0a6ed1",baja:"#6a6d70"};
  const pISO = s => { const [y,m,d] = s.split("-").map(Number); return new Date(y, m-1, d); };
  const isoOf = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
  const fmtD = iso => { if (!iso) return "—"; const d = pISO(iso); return `${String(d.getDate()).padStart(2,"0")} ${MES[d.getMonth()]}`; };
  const monday = iso => { const d = pISO(iso); const wd = (d.getDay()+6)%7; d.setDate(d.getDate()-wd); d.setHours(0,0,0,0); return d; };
  const wdBack = (finIso, n) => { const d = pISO(finIso); let s = Math.max(0,(n||1)-1); while(s>0){ d.setDate(d.getDate()-1); if(d.getDay()!==0&&d.getDay()!==6) s--; } return isoOf(d); };
  const person = (u, fb) => u ? escapeHtml(u) : `<span style="color:#aab;font-style:italic">${escapeHtml(fb)}</span>`;
  const NAMEOF = {}; [...UF, ...UD, ...UC].forEach(x => { NAMEOF[x.u] = x.n; });
  const nameOf = u => NAMEOF[u] || u || "";
  const userOpts = (sel, list) => `<option value="">—</option>` + (list||[]).map(x => `<option value="${escapeHtml(x.u)}" ${x.u===sel?"selected":""}>${escapeHtml(x.n)}</option>`).join("");
  const ediCell = (r, field) => {
    const fb = field==="usuario_clave"?"Usuario clave":(field==="funcional"?"Funcional":"Desarrollador");
    return canEdit
      ? `<select class="gedit" data-wp="${r.id}" data-field="${field}" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px;background:#fff;cursor:pointer">${userOpts(r[field], listFor(field))}</select>`
      : person(r[field] ? nameOf(r[field]) : "", fb);
  };
  const prioCell = r => {
    const et = r.etapa_key || r.etapa || "";
    if (et === "desestimado")
      return `<span style="font-size:11px;font-weight:600;color:#fff;background:#922b21;border-radius:10px;padding:1px 7px" title="Requisito desestimado">🚫 Desest.</span>`;
    const cur = r.prioridad || "media", col = PRIO_COL[cur] || "#6a6d70";
    if (!canPrio) { const lbl = (prios.find(p => p.codigo === cur) || {}).label || cur;
      return `<span style="font-size:11px;font-weight:600;color:#fff;background:${col};border-radius:10px;padding:1px 8px">${escapeHtml(lbl)}</span>`; }
    return `<select class="gprio" data-wp="${r.id}" style="font-size:11px;font-weight:600;border:none;border-radius:10px;padding:2px 6px;color:#fff;background:${col};cursor:pointer">`
      + prios.map(p => `<option value="${p.codigo}" ${p.codigo===cur?"selected":""}>${escapeHtml(p.label)}</option>`).join("") + `</select>`;
  };
  let fPrio = "", fIni = "", fFin = "";
  const passes = r => (!fPrio || (r.prioridad||"media") === fPrio)
    && (!fIni || (r.start_date && r.start_date >= fIni)) && (!fFin || (r.due_date && r.due_date <= fFin));
  const W = {wp:40, pr:86, req:176, fu:112, de:112, uc:112, ini:64, fin:110, ht:58, hf:58};
  const LW = W.wp+W.pr+W.req+W.fu+W.de+W.uc+W.ini+W.fin+W.ht+W.hf;

  function render() {
    // Conserva el scroll vertical de la vista al reconstruir el Gantt (edición de
    // un dato) → refresco suave, sin saltar al tope.
    const _vp = document.getElementById("view-proyecto");
    const _vy = _vp ? _vp.scrollTop : 0;
    requestAnimationFrame(() => { if (_vp) _vp.scrollTop = _vy; });
    const fcaps = caps.map(c => ({capability:c.capability, requisitos:c.requisitos.filter(passes)})).filter(c => c.requisitos.length);
    const visible = fcaps.flatMap(c => c.requisitos);
    const totalH = visible.reduce((s,r) => s + (+r.horas || 0), 0);
    const dated = visible.filter(r => r.start_date && r.due_date);
    let weeks = [], gridStart = null, nW = 0;
    if (dated.length) {
      const minI = dated.reduce((m,r) => r.start_date < m ? r.start_date : m, dated[0].start_date);
      const maxF = dated.reduce((m,r) => r.due_date > m ? r.due_date : m, dated[0].due_date);
      gridStart = monday(minI); const lastMon = monday(maxF);
      nW = Math.round((lastMon - gridStart)/DAY/7) + 1;
      weeks = Array.from({length:nW}, (_,i) => { const d = new Date(gridStart); d.setDate(d.getDate()+i*7); return d; });
    }
    // Requisitos SIN fechas: igual se muestran (fila) y ahora marcan un chip
    // "sin fechas" en la pista para que no parezca que faltan. Aseguramos un
    // ancho mínimo de pista aunque no haya ninguno con fecha.
    const hasUndated = visible.some(r => !r.start_date || !r.due_date);
    const undatedN = visible.filter(r => !r.start_date || !r.due_date).length;
    const TRACK = Math.max(nW*WK, hasUndated ? 240 : 0), TOT = LW+TRACK;
    const WEEKGRID = `repeating-linear-gradient(to right,transparent 0 ${WK-1}px,#c9d4e4 ${WK-1}px ${WK}px)`;
    const colHdr = (w,t) => `<div style="width:${w}px;padding:6px 6px;box-sizing:border-box">${t}</div>`;
    const bar = r => {
      if (!r.start_date || !r.due_date)
        return `<div title="Sin fechas de inicio/fin — asígnalas (columna Fin / horas) para verlo en la línea de tiempo" style="position:absolute;left:6px;top:5px;height:15px;display:flex;align-items:center;font-size:10px;font-weight:600;color:#b06a00;background:#fff3e0;border:1px dashed #e0a021;border-radius:3px;padding:0 7px;box-sizing:border-box">⚠ sin fechas</div>`;
      if (!gridStart) return "";
      const s = pISO(r.start_date), e = pISO(r.due_date);
      const left = (s-gridStart)/DAY/7*WK, w = Math.max(12, ((e-s)/DAY+1)/7*WK);
      const col = ETAPA_COL[r.etapa_key] || "#9aa7b8", pct = Math.max(0, Math.min(100, Math.round(r.percentage||0)));
      return `<div title="#${r.id} ${escapeHtml(r.subject||"")} · ${r.start_date} → ${r.due_date} · ${r.horas||0} h · ${ETAPA_LBL[r.etapa_key]||r.etapa_key||""} (${pct}%)" style="position:absolute;left:${left}px;width:${w}px;top:5px;height:15px;background:${col}26;border:1px solid ${col};border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${col}"></div></div>`;
    };
    const header = `<div style="display:flex;position:sticky;top:0;z-index:3;background:#eef2f8;border-bottom:2px solid #c9d4e4;font-size:11px;font-weight:700;color:#33415c">
        <div style="display:flex;flex-shrink:0;position:sticky;left:0;z-index:4;background:#eef2f8;width:${LW}px">
          ${colHdr(W.wp,"WP")}${colHdr(W.pr,"Prioridad")}${colHdr(W.req,"Requerimiento")}${colHdr(W.fu,"Funcional")}${colHdr(W.de,"Desarrollador")}${colHdr(W.uc,"Usuario clave")}${colHdr(W.ini,"Inicio")}${colHdr(W.fin,"Fin")}<div style="width:${W.ht}px;padding:6px 4px;box-sizing:border-box;text-align:right" title="Horas técnicas">H.Téc</div><div style="width:${W.hf}px;padding:6px 4px;box-sizing:border-box;text-align:right" title="Horas funcionales">H.Func</div>
        </div>
        <div style="position:relative;width:${TRACK}px;flex-shrink:0">
          ${weeks.map((d,i) => `<div style="position:absolute;left:${i*WK}px;width:${WK}px;box-sizing:border-box;border-left:1px solid #c9d4e4;padding:3px 2px;text-align:center;line-height:1.25">Sem ${i+1}<div style="color:#8693a8;font-weight:400">${d.getDate()} ${MES[d.getMonth()]}</div></div>`).join("")}
        </div></div>`;
    const rowHtml = r => `<div style="display:flex;border-bottom:1px solid #eef1f6;height:${ROWH}px;align-items:center;font-size:11px">
        <div style="display:flex;flex-shrink:0;position:sticky;left:0;z-index:2;background:#fff;width:${LW}px;height:100%;align-items:center">
          <div style="width:${W.wp}px;padding:0 6px;box-sizing:border-box"><a href="/inbox#wp=${r.id}" target="_blank" rel="noopener" title="Abrir (nueva pestaña)" style="color:#0a6ed1;text-decoration:none;font-weight:600">#${r.id}</a></div>
          <div style="width:${W.pr}px;padding:0 5px;box-sizing:border-box">${prioCell(r)}</div>
          <div style="width:${W.req}px;padding:0 6px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escapeHtml(r.subject||"")}"><b>${escapeHtml(r.subject||"")}</b></div>
          <div style="width:${W.fu}px;padding:0 4px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ediCell(r,"funcional")}</div>
          <div style="width:${W.de}px;padding:0 4px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ediCell(r,"desarrollador")}</div>
          <div style="width:${W.uc}px;padding:0 4px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ediCell(r,"usuario_clave")}</div>
          <div style="width:${W.ini}px;padding:0 6px;box-sizing:border-box;color:#556">${fmtD(r.start_date)}</div>
          <div style="width:${W.fin}px;padding:0 4px;box-sizing:border-box">${canEdit?`<input type="date" class="gfin" data-wp="${r.id}" value="${r.due_date||""}" title="Cambiar fin (recalcula inicio)" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px">`:fmtD(r.due_date)}</div>
          <div style="width:${W.ht}px;padding:0 3px;box-sizing:border-box">${canEdit?`<input type="number" class="ghtec" data-wp="${r.id}" value="${r.horas_tecnicas!=null?r.horas_tecnicas:""}" min="0" step="4" title="Horas técnicas (suma con funcionales → recalcula inicio)" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px;text-align:right;font-weight:600">`:(r.horas_tecnicas!=null?r.horas_tecnicas+"h":"—")}</div>
          <div style="width:${W.hf}px;padding:0 3px;box-sizing:border-box">${canEdit?`<input type="number" class="ghfunc" data-wp="${r.id}" value="${r.horas_funcionales!=null?r.horas_funcionales:""}" min="0" step="4" title="Horas funcionales (suma con técnicas → recalcula inicio)" style="width:100%;font-size:11px;border:1px solid #dde3ec;border-radius:3px;padding:1px 2px;text-align:right;font-weight:600">`:(r.horas_funcionales!=null?r.horas_funcionales+"h":"—")}</div>
        </div>
        <div style="position:relative;width:${TRACK}px;height:100%;flex-shrink:0;background:${WEEKGRID}">${bar(r)}</div>
      </div>`;
    const band = c => `<div style="width:${TOT}px;background:#e7edf6;border-top:1px solid #d3dceb;border-bottom:1px solid #d3dceb;font-weight:700;font-size:12px;color:#1a3a52;padding:4px 8px;box-sizing:border-box;position:sticky;left:0">🎯 ${escapeHtml(c.capability)} <span style="color:#8693a8;font-weight:400;margin-left:6px">(${c.requisitos.length})</span></div>`;
    const legend = Object.keys(ETAPA_LBL).map(k => `<span style="display:inline-flex;align-items:center;gap:4px;margin-right:8px"><span style="width:11px;height:11px;border-radius:2px;background:${ETAPA_COL[k]};display:inline-block"></span>${ETAPA_LBL[k]}</span>`).join("");
    const prioFilterOpts = `<option value="">Prioridad: todas</option>` + prios.map(p => `<option value="${p.codigo}" ${fPrio===p.codigo?"selected":""}>${escapeHtml(p.label)}</option>`).join("");
    $mount.innerHTML = `
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px;font-size:12px">
        <select id="gf-prio" style="font-size:12px;padding:5px 8px;border:1px solid #cdd6e4;border-radius:4px">${prioFilterOpts}</select>
        <label style="color:#667">Inicio ≥ <input type="date" id="gf-ini" value="${fIni}" style="font-size:12px;padding:4px 6px;border:1px solid #cdd6e4;border-radius:4px"></label>
        <label style="color:#667">Fin ≤ <input type="date" id="gf-fin" value="${fFin}" style="font-size:12px;padding:4px 6px;border:1px solid #cdd6e4;border-radius:4px"></label>
        <button id="gf-clear" style="font-size:12px;padding:5px 10px;border:1px solid #cdd6e4;border-radius:4px;background:#fff;cursor:pointer">Limpiar</button>
        <span style="margin-left:auto;font-weight:700;color:#1a3a52">💪 Esfuerzo: ${totalH.toLocaleString("es")} h <span style="font-weight:400;color:#8693a8">· ${visible.length} req${undatedN?` · <span style="color:#b06a00;font-weight:600">${undatedN} sin fechas</span>`:""}</span></span>
      </div>
      <div style="font-size:11px;color:#8693a8;margin-bottom:6px">${legend}</div>
      ${visible.length ? `<div style="overflow:auto;max-height:64vh;border:1px solid #d3dceb;border-radius:6px"><div style="min-width:${TOT}px">${header}${fcaps.map(c => band(c) + c.requisitos.map(rowHtml).join("")).join("")}</div></div>`
        : `<div class="empty" style="padding:18px">Ningún requerimiento cumple los filtros.</div>`}`;
    wire();
  }

  function wire() {
    const $p = $mount.querySelector("#gf-prio"), $i = $mount.querySelector("#gf-ini"), $f = $mount.querySelector("#gf-fin"), $c = $mount.querySelector("#gf-clear");
    if ($p) $p.onchange = () => { fPrio = $p.value; render(); };
    if ($i) $i.onchange = () => { fIni = $i.value; render(); };
    if ($f) $f.onchange = () => { fFin = $f.value; render(); };
    if ($c) $c.onclick = () => { fPrio = ""; fIni = ""; fFin = ""; render(); };
    $mount.querySelectorAll(".gprio").forEach(sel => sel.onchange = async () => {
      const wp = sel.dataset.wp; sel.style.background = PRIO_COL[sel.value] || "#6a6d70";
      try { await api(`/api/wp/${wp}/prioridad`, {method:"POST", body: JSON.stringify({prioridad: sel.value})}); byWp[wp].prioridad = sel.value; if (fPrio) render(); }
      catch(e){ alert("No se pudo fijar la prioridad: " + (e.message||e)); }
    });
    $mount.querySelectorAll(".gedit").forEach(sel => sel.onchange = async () => {
      const wp = sel.dataset.wp, field = sel.dataset.field;
      try { await api(`/api/wp/${wp}/campo`, {method:"POST", body: JSON.stringify({field, value: sel.value})}); byWp[wp][field] = sel.value || null; }
      catch(e){ alert("No se pudo guardar: " + (e.message||e)); }
    });
    $mount.querySelectorAll(".gfin").forEach(inp => inp.onchange = async () => {
      const wp = inp.dataset.wp, v = inp.value; if (!v) return;
      try { await api(`/api/wp/${wp}/campo`, {method:"POST", body: JSON.stringify({field:"fin", value:v})});
        const r = byWp[wp]; r.due_date = v; const n = Math.max(1, Math.round((+r.horas||8)/8)); r.start_date = wdBack(v, n); render(); }
      catch(e){ alert("No se pudo guardar la fecha: " + (e.message||e)); }
    });
    // Horas técnicas / funcionales (mig-014): guarda la componente, recompone
    // r.horas = suma (la que mueve la barra) y recalcula el inicio.
    const saveHoras = async (wp, field, v) => {
      await api(`/api/wp/${wp}/campo`, {method:"POST", body: JSON.stringify({field, value:v})});
      const r = byWp[wp];
      if (field === "horas_tecnicas") r.horas_tecnicas = v ? +v : null;
      else r.horas_funcionales = v ? +v : null;
      const tot = (+r.horas_tecnicas||0) + (+r.horas_funcionales||0);
      r.horas = tot || null;
      if (r.due_date && r.horas) { const n = Math.max(1, Math.round(r.horas/8)); r.start_date = wdBack(r.due_date, n); }
      render();
    };
    $mount.querySelectorAll(".ghtec").forEach(inp => inp.onchange = async () => {
      try { await saveHoras(inp.dataset.wp, "horas_tecnicas", inp.value); }
      catch(e){ alert("No se pudo guardar horas técnicas: " + (e.message||e)); }
    });
    $mount.querySelectorAll(".ghfunc").forEach(inp => inp.onchange = async () => {
      try { await saveHoras(inp.dataset.wp, "horas_funcionales", inp.value); }
      catch(e){ alert("No se pudo guardar horas funcionales: " + (e.message||e)); }
    });
  }

  render();
}

// Render de la vista "Estado del proyecto": KPIs + capacidades + tabla/gantt.
let resumenMode = (localStorage.getItem("resumenMode") || "simple");  // simple | full | gantt
let resumenFilters = { q: "", fase: "", asig: "", prio: "" };  // filtros (cliente) del resumen
let _resfFocus = false;       // re-enfocar el buscador tras re-render por filtro
async function renderResumen(soft, cached) {
  // soft = refresco tras editar un dato: NO mostramos "cargando" y conservamos
  // la posición de scroll, para que la vista no salte ni parpadee.
  // cached = re-render por cambio de filtro: reusa los datos ya traídos (sin red).
  const $v = document.getElementById("view-proyecto");
  const _scroll = $v ? $v.scrollTop : 0;
  if (!currentProject) {
    $v.innerHTML = `<div class="empty" style="padding:40px;text-align:center;color:#888">Elige un proyecto arriba para ver su estado.</div>`;
    return;
  }
  let d;
  if (cached && window._resumenData) {
    d = window._resumenData;
  } else {
    if (!soft) $v.innerHTML = `<div class="empty" style="padding:40px">cargando estado del proyecto...</div>`;
    try { d = await api(`/api/proyecto/${encodeURIComponent(currentProject)}/resumen` + (currentCapability ? `?capability=${encodeURIComponent(currentCapability)}` : "")); }
    catch (e) { $v.innerHTML = `<div class="empty" style="padding:40px;color:#c0392b">Error: ${escapeHtml(e.message)}</div>`; return; }
    window._resumenData = d;
  }
  const k = d.kpis || {};
  const order = d.etapas_order || [];
  const allCaps = d.capabilities || [];
  // Filtros (cliente): texto (#id/asunto) / fase / asignado / prioridad.
  const _F = resumenFilters;
  const _matchR = (r) => {
    if (_F.q) { const q = _F.q.toLowerCase(); if (!((`#${r.id} ${r.subject||""}`).toLowerCase().includes(q))) return false; }
    if (_F.fase && (r.etapa_key || "") !== _F.fase) return false;
    if (_F.asig && (r.assignee || "") !== _F.asig) return false;
    if (_F.prio && (r.prioridad || "media") !== _F.prio) return false;
    return true;
  };
  const _filtered = allCaps.map(c => Object.assign({}, c, { requisitos: (c.requisitos || []).filter(_matchR) })).filter(c => c.requisitos.length);
  const _hasFilter = !!(_F.q || _F.fase || _F.asig || _F.prio);
  const caps = _filtered;
  // username → Nombre Apellido (para la tabla del resumen).
  const _NAMEOF = {}; [...(d.usuarios_funcional||[]), ...(d.usuarios_desarrollador||[]), ...(d.usuarios_clave||[])].forEach(x => { _NAMEOF[x.u] = x.n; });
  const nameOf = u => u ? (_NAMEOF[u] || u) : "—";
  // Insumos faltantes por requerimiento: spec / transcripción / consultor.
  const FALTA_LABEL = { spec: "sin diseño", transcript: "sin entradas" };
  // Link al requerimiento: abre el inbox en ese WP, en otra pestaña.
  const wpLink = (r) => `<a href="/inbox#wp=${r.id}" target="_blank" rel="noopener" title="Abrir en el inbox (nueva pestaña)" style="color:#0a6ed1;text-decoration:none">#${r.id} ${escapeHtml(r.subject||"")} <span style="font-size:10px;opacity:.7">↗</span></a>`;
  const faltaChips = (r) => (r.falta || []).map(f =>
    `<span style="display:inline-block;background:#fde7e7;color:#a3262c;border:1px solid #f1b6b6;border-radius:8px;padding:0 6px;font-size:10px;font-weight:600;margin-left:4px" title="Falta ${FALTA_LABEL[f]||f}">⚠ ${FALTA_LABEL[f]||f}</span>`).join("");
  const incompletos = [];
  caps.forEach(c => (c.requisitos || []).forEach(r => { if ((r.falta || []).length) incompletos.push(r); }));
  const faltaBanner = incompletos.length ? `
    <details style="background:#fff8e1;border:1px solid #f0c060;border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:13px">
      <summary style="cursor:pointer;color:#856404;font-weight:700">⚠ ${incompletos.length} requerimiento(s) con insumos faltantes</summary>
      <ul style="margin:8px 0 0;padding-left:20px;color:#5c4a1a">
        ${incompletos.map(r => `<li>${wpLink(r)} — falta: <b>${(r.falta||[]).map(f=>FALTA_LABEL[f]||f).join(", ")}</b></li>`).join("")}
      </ul>
    </details>` : "";
  // ---- Dashboard contraíble (totales + desgloses, estilo reporte) ----
  const _all = [];
  caps.forEach(c => (c.requisitos || []).forEach(r => _all.push(r)));
  const _tot = _all.length;
  const _pct = (n) => _tot ? (Math.round(n * 1000 / _tot) / 10) : 0;
  const _subOf = (r) => (r.pending === "review" || r.pending === "gate") ? "esperando"
    : r.pending === "clarification" ? "aclaración"
    : (r.etapa_key === "pruebas" || r.etapa_key === "cerrado") ? "terminado" : "en curso";
  // Estado pendiente — mutuamente excluyente, por prioridad (suma = total).
  const _estadoOf = (r) => {
    const F = r.falta || [];
    if (F.includes("transcript")) return "Sin entradas";
    if (F.includes("spec")) return "Sin diseño";
    if ((r.observaciones || 0) > 0) return "Con observaciones";
    return "Completo";
  };
  const _tally = (keyFn, orderArr) => {
    const m = {}; _all.forEach(r => { const k = keyFn(r); m[k] = (m[k] || 0) + 1; });
    const entries = Object.entries(m);
    if (orderArr) entries.sort((a, b) => orderArr.indexOf(a[0]) - orderArr.indexOf(b[0]));
    else entries.sort((a, b) => b[1] - a[1]);
    return entries;
  };
  const byCap = caps.map(c => [c.capability, (c.requisitos || []).length]).sort((a, b) => b[1] - a[1]);
  const byFase = _tally(r => r.etapa || "—", order.map(e => e.label));
  const bySub = _tally(_subOf, ["en curso", "aclaración", "esperando", "terminado"]);
  const byEstado = _tally(_estadoOf, ["Completo", "Sin entradas", "Sin diseño", "Con observaciones"]);
  const _estColor = (n) => n === "Completo" ? "#107e3e" : n === "Con observaciones" ? "#b9770e" : "#a3262c";
  const _subColor = (n) => (n === "en curso" || n === "terminado") ? "#107e3e" : n === "aclaración" ? "#b9770e" : "#a3262c";
  const dashTable = (rows, colorFn) => `<table style="width:100%;border-collapse:collapse;font-size:12px">
    ${rows.map(([name, n]) => `<tr style="border-top:1px solid #eee">
      <td style="padding:3px 8px;${colorFn ? ('color:' + colorFn(name) + ';font-weight:600') : ''}">${escapeHtml(String(name))}</td>
      <td style="padding:3px 8px;text-align:right;font-weight:600">${n}</td>
      <td style="padding:3px 8px;text-align:right;color:#888">${_pct(n)}%</td>
    </tr>`).join("")}</table>`;
  const dashPanel = (title, rows, colorFn) => `<details open style="background:#fff;border:1px solid #dce0e6;border-radius:6px;overflow:hidden">
    <summary style="cursor:pointer;font-weight:700;color:#fff;background:#34495e;padding:7px 12px;font-size:12px;letter-spacing:.03em">${title}</summary>
    <div style="padding:2px 2px 6px">${dashTable(rows, colorFn)}</div>
  </details>`;
  const dashboard = `<details open style="margin-bottom:14px">
    <summary style="cursor:pointer;font-weight:700;font-size:14px;color:#1a3a5c;margin-bottom:8px">📊 Dashboard · ${_tot} requerimiento(s)</summary>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
      ${dashPanel("POR CAPACIDAD", byCap)}
      ${dashPanel("POR ESTADO PENDIENTE", byEstado, _estColor)}
      ${dashPanel("POR FASE", byFase)}
      ${dashPanel("POR SUB-ESTADO", bySub, _subColor)}
    </div>
  </details>`;
  const projName = projSelect && projSelect.selectedOptions.length ? projSelect.selectedOptions[0].textContent : "";
  const kpis = `
    <div class="kpi-row">
      <div class="kpi"><div class="num">${k.total || 0}</div><div class="lbl">Desarrollos</div></div>
      <div class="kpi"><div class="num">${k.en_curso || 0}</div><div class="lbl">En curso</div></div>
      <div class="kpi warn"><div class="num">${k.esperando_humano || 0}</div><div class="lbl">Esperando acción</div></div>
      <div class="kpi accent"><div class="num">${k.terminados || 0}</div><div class="lbl">Terminados / a pruebas</div></div>
      <div class="kpi"><div class="num">${k.avance_proyecto || 0}%</div><div class="lbl">Avance del proyecto</div></div>
    </div>`;
  if (!allCaps.length) {
    $v.innerHTML = `<h2 style="margin:0 0 4px">📊 ${escapeHtml(projName)}</h2>${kpis}<div class="empty" style="padding:30px;color:#888">No tienes desarrollos visibles en este proyecto.${d.is_leader ? "" : "<br><small>Ves solo los requisitos en los que participaste.</small>"}</div>`;
    return;
  }
  const fdt = (iso) => { if (!iso) return ""; const dt = new Date(iso); return isNaN(dt) ? "" : dt.toLocaleString('es',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  // Siempre muestra la FASE (etapa); el ícono indica el sub-estado.
  const pendPill = (r) => {
    const fase = escapeHtml(r.etapa || "—");
    if (r.pending === "review" || r.pending === "gate")
      return `<span class="pill esp" title="Esperando acción humana">⏳ ${fase}</span>`;
    if (r.pending === "clarification")
      return `<span class="pill esp" title="Esperando aclaración">❓ ${fase}</span>`;
    if (r.etapa_key === "pruebas" || r.etapa_key === "cerrado")
      return `<span class="pill fin">✓ ${fase}</span>`;
    return `<span class="pill curso">▶ ${fase}</span>`;
  };
  const toggle = `<div style="display:flex;gap:8px;align-items:center;margin:0 0 12px">
      <span style="font-size:12px;color:#666">Detalle:</span>
      <div id="resumen-mode" style="display:flex;gap:2px;background:#eee;border-radius:10px;padding:2px;font-size:12px">
        <button data-mode="simple" style="border:none;border-radius:9px;padding:3px 10px;cursor:pointer;${resumenMode==='simple'?'background:#0a6ed1;color:#fff':'background:transparent;color:#444'}">Resumen</button>
        <button data-mode="full" style="border:none;border-radius:9px;padding:3px 10px;cursor:pointer;${resumenMode==='full'?'background:#0a6ed1;color:#fff':'background:transparent;color:#444'}">Horas por etapa</button>
        <button data-mode="gantt" style="border:none;border-radius:9px;padding:3px 10px;cursor:pointer;${resumenMode==='gantt'?'background:#0a6ed1;color:#fff':'background:transparent;color:#444'}">📅 Gantt</button>
      </div>
    </div>`;
  // ---- Barra de filtros (cliente) ----
  const _asigSet = new Map();
  allCaps.forEach(c => (c.requisitos || []).forEach(r => { if (r.assignee) _asigSet.set(r.assignee, r.assignee_nombre || nameOf(r.assignee)); }));
  const _prios = d.prioridades || [];
  const _opt = (val, lbl, cur) => `<option value="${escapeHtml(val)}" ${cur === val ? "selected" : ""}>${escapeHtml(lbl)}</option>`;
  const _shown = caps.reduce((n, c) => n + c.requisitos.length, 0);
  const _total = allCaps.reduce((n, c) => n + (c.requisitos || []).length, 0);
  const filterBar = `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 12px;font-size:12px">
      <input id="resf-q" type="search" placeholder="🔎 Buscar #id o texto" value="${escapeHtml(_F.q)}" style="flex:1;min-width:180px;padding:5px 9px;border:1px solid #cdd3da;border-radius:6px;font-size:12px">
      <select id="resf-fase" style="padding:5px;border:1px solid #cdd3da;border-radius:6px">${_opt("", "Fase: todas", _F.fase)}${order.map(e => _opt(e.key, e.label, _F.fase)).join("")}</select>
      <select id="resf-asig" style="padding:5px;border:1px solid #cdd3da;border-radius:6px">${_opt("", "Asignado: todos", _F.asig)}${[..._asigSet].map(([u, n]) => _opt(u, n, _F.asig)).join("")}</select>
      ${_prios.length ? `<select id="resf-prio" style="padding:5px;border:1px solid #cdd3da;border-radius:6px">${_opt("", "Prioridad: todas", _F.prio)}${_prios.map(p => _opt(p.codigo, p.label, _F.prio)).join("")}</select>` : ""}
      <button id="resf-clear" style="border:1px solid #cdd3da;background:#fff;border-radius:6px;padding:5px 10px;cursor:pointer">Limpiar</button>
      <span style="color:#888" title="Mostrados / total">${_shown}/${_total}</span>
    </div>`;
  // UNA sola tabla con filas de grupo por capacidad (columnas alineadas), en
  // vez de N tablas con anchos distintos.
  const capBar = (c, span) => `<tr class="cap-row"><td colspan="${span}">🎯 ${escapeHtml(c.capability)} <span style="color:#888;font-weight:400">(${c.requisitos.length})</span><span class="cap-inlinebar"><span style="width:${c.avance}%"></span></span><span class="pct">${c.avance}%</span></td></tr>`;
  let tableHtml;
  if (resumenMode === "full") {
    const span = order.length + 2;
    const head = `<tr><th>Requerimiento</th>${order.map(e=>`<th style="text-align:right">${escapeHtml(e.label)}</th>`).join("")}<th style="text-align:right">Total</th></tr>`;
    const body = caps.map(c => capBar(c, span) + c.requisitos.map(r => {
      const m = {}; (r.etapas || []).forEach(x => m[x.etapa_key] = x);
      const cells = order.map(e => {
        const x = m[e.key];
        if (!x) return `<td style="color:#ccc;text-align:center">—</td>`;
        const cur = (e.key === r.current_key);
        return `<td${cur?' class="cell-cur"':''} style="text-align:right"><div style="color:#999;font-size:11px">${fdt(x.desde)}</div><div style="font-weight:600">${cur?'▶ ':''}${x.horas}h</div></td>`;
      }).join("");
      return `<tr><td>${wpLink(r)}</td>${cells}<td style="text-align:right"><b>${r.horas_total||0}h</b></td></tr>`;
    }).join("")).join("");
    tableHtml = `<div style="overflow:auto"><table class="resumen">${head}${body}</table></div>`;
  } else {
    const flag = (cond) => cond
      ? '<span style="color:#a3262c;font-weight:700" title="Falta">⚠</span>'
      : '<span style="color:#107e3e" title="OK">✓</span>';
    const prios = d.prioridades || [], canPrio = d.can_set_prioridad && prios.length;
    const PRIO_C = {urgente:"#bb0000",alta:"#d97400",media:"#0a6ed1",baja:"#6a6d70"};
    const prioCell = (r) => {
      const cur = r.prioridad || "media", col = PRIO_C[cur] || "#6a6d70";
      if (!canPrio) {
        const lbl = (prios.find(p => p.codigo === cur) || {}).label || cur;
        return `<span style="font-size:11px;font-weight:600;color:#fff;background:${col};border-radius:10px;padding:1px 8px">${escapeHtml(lbl)}</span>`;
      }
      return `<select class="gprio" data-wp="${r.id}" style="font-size:11px;font-weight:600;border:none;border-radius:10px;padding:2px 6px;color:#fff;background:${col};cursor:pointer">`
        + prios.map(p => `<option value="${p.codigo}" ${p.codigo===cur?"selected":""}>${escapeHtml(p.label)}</option>`).join("") + `</select>`;
    };
    const canAvance = !!d.can_set_prioridad;  // el % del líder lo captura admin/líder
    // Avance del WORKFLOW (por etapa, no editable).
    const avWf = (r) => `<span class="minibar"><span style="width:${r.avance_workflow}%"></span></span> <b style="font-size:11px">${r.avance_workflow}%</b>`;
    // Avance del LÍDER de proyecto (editable; vacío = usa el del workflow).
    const avLider = (r) => {
      if (!canAvance) return r.avance_lider == null ? '<span style="color:#bbb">—</span>' : `<b style="font-size:11px">${r.avance_lider}%</b>`;
      return `<input class="gav" data-wp="${r.id}" type="number" min="0" max="100" value="${r.avance_lider == null ? '' : r.avance_lider}" placeholder="—" title="Avance capturado por el líder de proyecto (vacío = usa el del workflow). El acumulado se basa en este." style="width:52px;font-size:11px;border:1px solid #cdd3da;border-radius:4px;padding:1px 3px;text-align:right">%`;
    };
    const head = `<tr><th>Requerimiento</th><th>Prioridad</th><th>Estado (fase)</th><th style="text-align:center" title="Tiene insumos/adjuntos cargados">Entradas</th><th style="text-align:center" title="Tiene paquete de diseño (especificación)">Diseño</th><th>Asignado</th><th>Funcional · Usuario clave</th><th>Fechas</th><th title="Avance automático por etapa del flujo">Avance flujo</th><th title="Avance capturado por el líder de proyecto (base del acumulado)">Avance líder</th></tr>`;
    const body = caps.map(c => capBar(c, 10) + c.requisitos.map(r => `<tr>
        <td>${wpLink(r)}</td>
        <td>${prioCell(r)}</td>
        <td>${pendPill(r)}</td>
        <td style="text-align:center">${flag((r.falta||[]).includes("transcript"))}</td>
        <td style="text-align:center">${flag((r.falta||[]).includes("spec"))}</td>
        <td>${escapeHtml(r.assignee_nombre || nameOf(r.assignee))}</td>
        <td style="font-size:11px">${escapeHtml(r.funcional_nombre || nameOf(r.funcional))} · ${escapeHtml(r.usuario_clave_nombre || nameOf(r.usuario_clave))}</td>
        <td style="font-size:11px;white-space:nowrap">${r.start_date||"—"} → ${r.due_date||"—"}</td>
        <td style="white-space:nowrap">${avWf(r)}</td>
        <td style="white-space:nowrap">${avLider(r)}</td>
      </tr>`).join("")).join("");
    tableHtml = `<table class="resumen">${head}${body}</table>`;
  }
  $v.innerHTML = `<div style="display:flex;align-items:center;gap:12px;margin:0 0 4px">
      <h2 style="margin:0;flex:1">📊 ${escapeHtml(projName)}</h2>
      <button id="csv-btn" style="background:#107e3e;color:#fff;border:none;border-radius:4px;padding:7px 14px;cursor:pointer;font-weight:600;font-size:13px">⬇ Excel resumen</button>
      <button id="bill-btn" title="Horas a facturar = % avance del líder × horas técnicas (desarrollo)" style="background:#0a6ed1;color:#fff;border:none;border-radius:4px;padding:7px 14px;cursor:pointer;font-weight:600;font-size:13px">⬇ Excel facturación</button>
      ${d.is_leader ? `<button id="snap-btn" title="Congela el avance actual como FOTO (guarda historial) para facturar el delta del período" style="background:#6c3fd1;color:#fff;border:none;border-radius:4px;padding:7px 14px;cursor:pointer;font-weight:600;font-size:13px">📸 Congelar avance</button>
      <button id="delta-btn" title="Excel del INCREMENTO de avance desde la última foto × horas técnicas" style="background:#8a5cd1;color:#fff;border:none;border-radius:4px;padding:7px 14px;cursor:pointer;font-weight:600;font-size:13px">⬇ Excel Δ facturación</button>
      <span id="snap-info" style="font-size:11px;color:#888;white-space:nowrap"></span>` : ''}
    </div>
    <div style="font-size:12px;color:#888;margin-bottom:12px">${d.is_leader ? "Líder de proyecto: ves todos los desarrollos." : "Ves los desarrollos en los que participaste."}</div>
    ${dashboard}${faltaBanner}${kpis}${toggle}${filterBar}${!caps.length ? `<div class="empty" style="padding:30px;color:#888">Ningún requerimiento coincide con el filtro.</div>` : (resumenMode==='gantt' ? `<div id="gantt-mount" class="panel" style="padding:0;border:none;background:transparent"></div>` : `<div class="panel" style="padding:0;overflow:hidden">${tableHtml}</div>`)}`;
  // Conserva el scroll tras un refresco suave (sin saltar al tope).
  if ($v && soft) $v.scrollTop = _scroll;
  document.querySelectorAll("#resumen-mode button").forEach(b => b.onclick = () => {
    resumenMode = b.dataset.mode; localStorage.setItem("resumenMode", resumenMode); renderResumen();
  });
  // Filtros: re-render LOCAL (sin red) al cambiar; conserva el foco del buscador.
  const _ref = () => renderResumen(true, true);
  const $q = document.getElementById("resf-q");
  if ($q) $q.oninput = () => { resumenFilters.q = $q.value; _resfFocus = true; _ref(); };
  const $ff = document.getElementById("resf-fase");
  if ($ff) $ff.onchange = () => { resumenFilters.fase = $ff.value; _ref(); };
  const $fa = document.getElementById("resf-asig");
  if ($fa) $fa.onchange = () => { resumenFilters.asig = $fa.value; _ref(); };
  const $fp = document.getElementById("resf-prio");
  if ($fp) $fp.onchange = () => { resumenFilters.prio = $fp.value; _ref(); };
  const $fc = document.getElementById("resf-clear");
  if ($fc) $fc.onclick = () => { resumenFilters = { q: "", fase: "", asig: "", prio: "" }; _ref(); };
  if (_resfFocus) { const e = document.getElementById("resf-q"); if (e) { e.focus(); try { e.setSelectionRange(e.value.length, e.value.length); } catch (_) {} } _resfFocus = false; }
  const $csv = document.getElementById("csv-btn");
  if ($csv) $csv.onclick = () => downloadResumenXlsx(caps, projName, FALTA_LABEL);
  const $bill = document.getElementById("bill-btn");
  if ($bill) $bill.onclick = () => downloadFacturacionXlsx(caps, projName);
  const $snap = document.getElementById("snap-btn");
  if ($snap) $snap.onclick = () => congelarAvance();
  const $delta = document.getElementById("delta-btn");
  if ($delta) $delta.onclick = () => downloadDeltaFacturacionXlsx(caps, projName);
  if (d.is_leader) loadSnapInfo();
  // % de avance manual (solo admin/líder): guarda y refresca.
  document.querySelectorAll("input.gav").forEach(inp => {
    inp.onchange = async () => {
      const wp = inp.dataset.wp, v = (inp.value || "").trim();
      try {
        await api(`/api/wp/${wp}/campo`, { method: "POST",
          body: JSON.stringify({ field: "avance_pct", value: v }) });
        toast("Avance actualizado", "ok"); renderResumen(true);
      } catch (e) { toast("No se pudo guardar el avance: " + (e.message || e), "error"); }
    };
  });
  if (resumenMode === "gantt") {
    const $m = document.getElementById("gantt-mount");
    if ($m) renderGantt(caps, {prios: d.prioridades || [], canPrio: d.can_set_prioridad,
                               uf: d.usuarios_funcional || [], ud: d.usuarios_desarrollador || [],
                               uc: d.usuarios_clave || [], canEdit: d.can_edit}, $m);
  }
}

// Descarga Excel real (.xlsx) generado server-side (openpyxl). Incluye la
// columna "Pendiente" (insumo inicial / spec / consultor) para cada requisito.
async function downloadResumenXlsx(caps, projName, FALTA_LABEL) {
  const headers = ["Capacidad", "ID", "Requerimiento", "Prioridad", "Fase", "Sub-estado",
    "Entradas", "Diseño",
    "Asignado", "Funcional", "Usuario clave", "Avance flujo %", "Avance líder %",
    "Inicio", "Fin", "Horas técnicas", "Horas funcionales", "Horas totales",
    "Horas invertidas", "Comentarios"];
  // Comentarios del blog (solo de personas, sintetizados 'fecha - texto') por
  // requerimiento; se piden al backend solo al exportar.
  let comMap = {};
  try {
    const cd = await api(`/api/proyecto/${encodeURIComponent(currentProject)}/comentarios-export`
      + (currentCapability ? `?capability=${encodeURIComponent(currentCapability)}` : ""));
    comMap = (cd && cd.comentarios) || {};
  } catch (e) { /* si falla, exporta sin comentarios */ }
  const rows = [];
  caps.forEach(c => c.requisitos.forEach(r => {
    const sub = (r.pending === "review" || r.pending === "gate") ? "esperando"
      : r.pending === "clarification" ? "aclaracion"
      : (r.etapa_key === "pruebas" || r.etapa_key === "cerrado") ? "terminado" : "en curso";
    const F = r.falta || [];
    const SI = "Sí", NO = "No";
    // Prioridad: los desestimados muestran "Desestimado" (la prioridad ya no aplica).
    const prio = r.es_desestimado ? "Desestimado" : (r.prioridad_label || r.prioridad || "");
    // Horas del plan (mig-014): técnicas + funcionales + total (= suma).
    const hTec = r.horas_tecnicas != null ? r.horas_tecnicas : "";
    const hFun = r.horas_funcionales != null ? r.horas_funcionales : "";
    const hTot = r.horas != null ? r.horas
                 : ((r.horas_tecnicas != null || r.horas_funcionales != null)
                    ? (+r.horas_tecnicas || 0) + (+r.horas_funcionales || 0) : "");
    rows.push([c.capability, r.id, r.subject || "", prio, r.etapa || "", sub,
      F.includes("transcript") ? NO : SI,
      F.includes("spec") ? NO : SI,
      r.assignee_nombre || r.assignee || "", r.funcional_nombre || r.funcional || "",
      r.usuario_clave_nombre || r.usuario_clave || "",
      r.avance_workflow, (r.avance_lider == null ? "" : r.avance_lider),
      r.start_date || "", r.due_date || "", hTec, hFun, hTot, r.horas_total || 0,
      comMap[r.id] || ""]);
  }));
  const fname = `requerimientos-${(projName || currentProject || "proyecto").replace(/[^A-Za-z0-9_-]+/g, "_")}`;
  try {
    const resp = await fetch("/api/export/xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: fname, sheet: "Requerimientos", headers, rows }),
    });
    if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${fname}.xlsx`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (e) { toast("No se pudo generar el Excel: " + e.message, "error"); }
}

// Segundo Excel: reporte de FACTURACIÓN. Horas a facturar = % avance del LÍDER
// (avance_lider) × horas técnicas (las "horas del desarrollo"). Si el líder no
// --- Fotos de avance (facturación por DELTA) --------------------------------
// Congela el avance actual como una foto (historial en BD). El delta del período
// = avance ahora − avance de la última foto, × horas técnicas = horas a facturar.
async function congelarAvance() {
  if (!confirm("¿Congelar el avance actual como una nueva FOTO?\n\nSe usa para facturar el incremento (delta) del período. Queda en el historial.")) return;
  try {
    await api(`/api/proyecto/${currentProject}/snapshot`, { method: "POST", body: JSON.stringify({}) });
    toast("📸 Foto de avance guardada", "ok");
    loadSnapInfo();
  } catch (e) { toast("No se pudo congelar el avance: " + (e.message || e), "error"); }
}
async function loadSnapInfo() {
  const $i = document.getElementById("snap-info"); if (!$i) return;
  try {
    const d = await api(`/api/proyecto/${currentProject}/snapshots`);
    const last = (d.snapshots || [])[0];
    $i.textContent = last
      ? `📸 última foto: ${(last.tomado_en || "").slice(0, 16).replace("T", " ")} · ${d.snapshots.length} foto(s)`
      : "sin fotos aún";
  } catch (e) { $i.textContent = ""; }
}
async function downloadDeltaFacturacionXlsx(caps, projName) {
  let snap;
  try { snap = await api(`/api/proyecto/${currentProject}/snapshot/ultima`); }
  catch (e) { toast("No se pudo leer la última foto: " + (e.message || e), "error"); return; }
  if (!snap || !snap.id) { toast("Aún no hay ninguna foto. Congela el avance primero (📸).", "error"); return; }
  const prev = {}; (snap.datos || []).forEach(x => { prev[x.wp_id] = x; });
  const fecha = (snap.tomado_en || "").slice(0, 16).replace("T", " ");
  const headers = ["Capacidad", "ID", "Requerimiento", "Avance foto %", "Avance ahora %",
    "Δ avance %", "Horas técnicas", "Horas a facturar (Δ)"];
  const rows = [[`Foto base: ${fecha} — por ${snap.tomado_por || "?"}`, "", "", "", "", "", "", ""]];
  let totFact = 0; const r2 = n => Math.round(n * 100) / 100;
  caps.forEach(c => c.requisitos.forEach(r => {
    const now = (r.avance_lider != null) ? +r.avance_lider : 0;
    const before = prev[r.id] ? (+prev[r.id].avance || 0) : 0;
    const dAv = now - before;
    const hTec = (r.horas_tecnicas != null) ? +r.horas_tecnicas : 0;
    const fact = r2(hTec * dAv / 100);
    totFact += fact;
    rows.push([c.capability, r.id, r.subject || "", before, now, dAv,
      r.horas_tecnicas != null ? r.horas_tecnicas : "", fact]);
  }));
  rows.push(["", "", "TOTAL", "", "", "", "", r2(totFact)]);
  const fname = `facturacion-delta-${(projName || currentProject || "proyecto").replace(/[^A-Za-z0-9_-]+/g, "_")}`;
  try {
    const resp = await fetch("/api/export/xlsx", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: fname, sheet: "Delta facturación", headers, rows }),
    });
    if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
    const blob = await resp.blob(); const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${fname}.xlsx`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (e) { toast("No se pudo generar el Excel Δ: " + e.message, "error"); }
}

// capturó avance, el facturable es 0. Respeta los filtros activos del Resumen
// (usa el mismo `caps` que la vista) y agrega una fila TOTAL al final.
async function downloadFacturacionXlsx(caps, projName) {
  const headers = ["Capacidad", "ID", "Requerimiento", "Asignado", "Funcional",
    "Fase", "Horas técnicas (desarrollo)", "Avance líder %", "Horas a facturar",
    "Horas invertidas"];
  const rows = [];
  let totTec = 0, totFact = 0, totInv = 0;
  const r2 = (n) => Math.round(n * 100) / 100;  // 2 decimales
  caps.forEach(c => c.requisitos.forEach(r => {
    const hTec = (r.horas_tecnicas != null) ? +r.horas_tecnicas : 0;
    const av = (r.avance_lider == null) ? 0 : +r.avance_lider;
    const fact = r2(hTec * av / 100);
    const inv = +r.horas_total || 0;
    totTec += hTec; totFact += fact; totInv += inv;
    rows.push([c.capability, r.id, r.subject || "",
      r.assignee_nombre || r.assignee || "", r.funcional_nombre || r.funcional || "",
      r.etapa || "",
      r.horas_tecnicas != null ? r.horas_tecnicas : "",
      r.avance_lider == null ? "" : r.avance_lider,
      fact, inv]);
  }));
  rows.push(["", "", "TOTAL", "", "", "", r2(totTec), "", r2(totFact), r2(totInv)]);
  const fname = `facturacion-${(projName || currentProject || "proyecto").replace(/[^A-Za-z0-9_-]+/g, "_")}`;
  try {
    const resp = await fetch("/api/export/xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: fname, sheet: "Facturación", headers, rows }),
    });
    if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${fname}.xlsx`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (e) { toast("No se pudo generar el Excel de facturación: " + e.message, "error"); }
}

// Scope toggle "Mi inbox / Todos" — solo visible para admins (C2)
document.querySelectorAll("#scope-toggle button").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll("#scope-toggle button").forEach(b => {
      b.classList.remove("active");
      b.style.background = "transparent"; b.style.color = "#555";
    });
    btn.classList.add("active");
    btn.style.background = "#0a6ed1"; btn.style.color = "#fff";
    currentScope = btn.dataset.scope;
    localStorage.setItem("inboxScope", currentScope);
    loadInbox();
  };
});

// ============================================================
// Modal "Iniciar requerimiento" — crea WP en OP + sube adjuntos + arranca WF
// ============================================================
document.getElementById("new-req-btn").onclick = async () => {
  // D1: permitimos a roles del lado cliente (funcional, usuario-clave)
  // crear requisitos — quedan en "Pendiente validación" hasta que SwF
  // Funcional/admin los apruebe y arranque el workflow.
  const roles = (currentUser && currentUser.roles) || [];
  const allowed = ["swf-funcional", "admin", "funcional", "usuario-clave"];
  if (!roles.some(r => allowed.includes(r))) {
    toast("Tu rol no puede iniciar requerimientos", "error");
    return;
  }
  await openNewRequirementModal();
};

async function openNewRequirementModal() {
  // El requerimiento HEREDA el proyecto del combo del header (segmentación).
  // Cargamos solo los proyectos del usuario (hoja); default = el seleccionado.
  let projects = [];
  try {
    const r = await api("/api/mis-proyectos");
    projects = r.projects || [];
  } catch (e) {
    toast("Error cargando proyectos: " + e.message, "error");
    return;
  }
  if (!projects.length) {
    toast("No tienes proyectos asignados para crear requerimientos", "error");
    return;
  }
  // Proyecto por defecto: el del combo del header, si está entre los del usuario.
  const preId = (currentProject && projects.some(p => String(p.id) === String(currentProject)))
    ? String(currentProject) : "";

  const modal = document.createElement("div");
  modal.className = "ann-modal-bg";
  modal.innerHTML = `
    <div class="ann-modal" style="width: 680px; max-width: 92vw;">
      <h3>🚀 Iniciar nuevo requerimiento</h3>
      <p style="font-size:12px;color:#666;margin:4px 0 12px 0">
        Crea un Requisito dentro de una <b>Capability</b> del proyecto.
        Sube adjuntos (PDF/TXT/imágenes) que la IA leerá para generar la spec funcional.
      </p>
      <label>Proyecto</label>
      <select id="nr-proyecto">
        ${preId ? "" : `<option value="">— elige proyecto —</option>`}
        ${projects.map(p => `<option value="${p.id}" ${String(p.id)===preId?"selected":""}>${escapeHtml(p.name)}</option>`).join("")}
      </select>
      <label>Capability</label>
      <select id="nr-capability" disabled><option value="">— cargando capabilities… —</option></select>
      <input id="nr-new-cap" type="text" placeholder="…o escribe nombre para crear nueva capability"
             disabled
             style="margin-top:4px;background:#fafafa;font-style:italic;color:#666"/>
      <hr style="margin:16px 0;border:none;border-top:1px solid #e0e0e0"/>
      <label>Nombre del Requisito</label>
      <input id="nr-subject" placeholder="REQ-XXX: Aplicación Fiori de ..."/>
      <label>Descripción (opcional)</label>
      <textarea id="nr-desc" placeholder="Breve descripción; el detalle va en adjuntos." style="min-height:60px"></textarea>
      <label>Relacionar con requisito # (opcional)</label>
      <input id="nr-related" type="text" inputmode="numeric" placeholder="ej. 40 — asocia este requisito con otro"/>
      <label>Adjuntos (PDF / TXT / imágenes)</label>
      <input id="nr-files" type="file" multiple accept=".pdf,.txt,.md,image/*"
             style="border:1px dashed #ccc;padding:8px;background:#fafafa;width:100%"/>
      <div id="nr-files-list" style="font-size:11px;color:#666;margin-top:4px"></div>
      <div class="buttons" style="margin-top:14px">
        <button id="nr-cancel">Cancelar</button>
        <button id="nr-submit" class="primary" style="background:#0a6ed1;color:#fff;border:none;border-radius:4px;padding:8px 16px" disabled>🚀 Crear requerimiento</button>
      </div>
      <div id="nr-status" style="margin-top:8px;font-size:12px;color:#666"></div>
    </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector("#nr-cancel").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };

  const $proy = modal.querySelector("#nr-proyecto");
  const $cap = modal.querySelector("#nr-capability");
  const $newCap = modal.querySelector("#nr-new-cap");
  const $subj = modal.querySelector("#nr-subject");
  const $sub = modal.querySelector("#nr-submit");

  const fill = (sel, list, placeholder) => {
    sel.innerHTML = `<option value="">— elige —</option>` +
      list.map(p => `<option value="${p.id}">${escapeHtml(p.name || p.subject)}</option>`).join("");
    sel.disabled = list.length === 0;
    if (list.length === 0) sel.innerHTML = `<option value="">${placeholder}</option>`;
  };

  function checkSubmit() {
    const hasProj = $proy.value;
    const hasCap = $cap.value || $newCap.value.trim();
    const hasSubj = $subj.value.trim();
    $sub.disabled = !(hasProj && hasCap && hasSubj);
  }

  async function loadCaps() {
    if (!$proy.value) {
      fill($cap, [], "— elige proyecto —"); $newCap.disabled = true; $newCap.value = "";
      checkSubmit(); return;
    }
    fill($cap, [], "— cargando… —");
    try {
      const r = await api(`/api/projects/${$proy.value}/capabilities`);
      const caps = (r.items || []).map(c => ({ id: c.id, name: c.subject }));
      fill($cap, caps, "— sin capabilities; crea una nueva abajo —");
      $newCap.disabled = false;
      $newCap.placeholder = caps.length
        ? "…o escribe nombre para crear nueva capability"
        : "Escribe nombre para crear primera capability del proyecto";
    } catch (e) { toast("Error cargando capabilities: " + e.message, "error"); }
    checkSubmit();
  }
  $proy.onchange = loadCaps;
  // Si heredó proyecto del combo, carga sus capabilities de una vez.
  if ($proy.value) loadCaps();
  setTimeout(() => $subj.focus(), 50);
  $cap.onchange = () => {
    if ($cap.value) { $newCap.disabled = true; $newCap.value = ""; }
    else $newCap.disabled = false;
    checkSubmit();
  };
  $newCap.oninput = () => {
    if ($newCap.value.trim()) $cap.value = "";
    checkSubmit();
  };
  $subj.oninput = checkSubmit;

  modal.querySelector("#nr-files").onchange = (e) => {
    const files = Array.from(e.target.files || []);
    const $l = modal.querySelector("#nr-files-list");
    $l.innerHTML = files.length
      ? files.map(f => `<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
          <span style="flex:1">📎 ${escapeHtml(f.name)} <span style="color:#999">(${(f.size/1024).toFixed(1)} KB)</span></span>
          <span style="color:#666">Tipo:</span> ${tipoSelectHtml("nr-ftype")}</div>`).join("")
      : "";
  };

  $sub.onclick = async () => {
    const projectId = $proy.value;
    const capId = $cap.value;
    const newCapName = $newCap.value.trim();
    const subject = $subj.value.trim();
    const desc = modal.querySelector("#nr-desc").value.trim();
    const relatedTo = (modal.querySelector("#nr-related").value || "").trim().replace(/^#/, "");
    const files = Array.from(modal.querySelector("#nr-files").files || []);
    if (relatedTo && !/^\d+$/.test(relatedTo)) {
      toast("El requisito a relacionar debe ser un número (#)", "error");
      return;
    }
    if (!projectId || !subject || (!capId && !newCapName)) {
      toast("Faltan datos: proyecto, capability y nombre del requerimiento", "error");
      return;
    }
    $sub.disabled = true; $sub.textContent = "Subiendo…";
    const status = modal.querySelector("#nr-status");

    try {
      // 1) Subir cada archivo DIRECTO a MinIO con presigned PUT URL.
      //    No pasa por backend → sin límite de tamaño FastAPI.
      const batchId = (crypto.randomUUID && crypto.randomUUID()) ||
                       (Date.now() + "-" + Math.random().toString(16).slice(2));
      const ftypes = [...modal.querySelectorAll(".nr-ftype")];
      const usedNames = new Set();
      const uploaded = [];
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const tipo = (ftypes[i] && ftypes[i].value) || "otro";
        const newName = buildFileName(subject, tipo, f.name, usedNames);
        status.textContent = `Subiendo ${i+1}/${files.length}: ${newName} (${(f.size/1024/1024).toFixed(2)} MB)…`;
        // a) Pedir presigned URL (con el nombre renombrado)
        const presign = await api("/api/inbox/upload-url", {
          method: "POST",
          body: JSON.stringify({
            batch_id: batchId,
            filename: newName,
            content_type: f.type || "application/octet-stream",
          }),
        });
        // b) PUT directo a MinIO/S3
        const putResp = await fetch(presign.put_url, {
          method: "PUT",
          headers: { "Content-Type": presign.content_type },
          body: f,
        });
        if (!putResp.ok) {
          throw new Error(`Upload de ${newName} falló: HTTP ${putResp.status}`);
        }
        uploaded.push({
          filename: newName, key: presign.key, tipo: tipo,
          size: f.size, content_type: presign.content_type,
        });
      }

      // 2) Crear el Requisito en OP + manifest + arrancar workflow
      status.textContent = "Creando requisito…";
      const result = await api("/api/inbox/new-requirement", {
        method: "POST",
        body: JSON.stringify({
          project_id: Number(projectId),
          subject, description: desc,
          capability_id: capId ? Number(capId) : null,
          new_capability_name: capId ? "" : newCapName,
          related_to: relatedTo ? Number(relatedTo) : null,
          files: uploaded,
        }),
      });
      const capMsg = result.created_capability
        ? ` · capability nueva #${result.created_capability.id} '${result.created_capability.subject}'`
        : "";
      // D1: si el creador es funcional/usuario-clave, el backend NO arranca
      // el workflow — queda en pendiente validación de SwF Funcional/admin.
      const tailMsg = result.needs_validation
        ? `📨 Pendiente validación por SwF Funcional antes de arrancar el agente.`
        : `🚀 Workflow iniciado.`;
      toast(`Requisito #${result.wp_id} creado${capMsg}. ${result.files_count} adjunto(s) en MinIO. ${tailMsg}`, "ok");
      close();
      await loadInbox();
      if (result.wp_id) setTimeout(() => loadDetail(result.wp_id), 800);
    } catch (err) {
      $sub.disabled = false; $sub.textContent = "🚀 Crear requerimiento";
      status.innerHTML = `<span style="color:#c33">${escapeHtml(err.message)}</span>`;
    }
  };
}

// Restaurar ancho persistido de la sidebar de lista.
(function() {
  const savedW = parseInt(localStorage.getItem("listWidth") || "0", 10);
  if (savedW >= 200 && savedW <= 800) {
    document.documentElement.style.setProperty("--list-width", savedW + "px");
  }
  // Drag del divisor entre lista y detalle.
  const divider = document.getElementById("list-divider");
  if (!divider) return;
  divider.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = document.getElementById("list-col").getBoundingClientRect().width;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    // Para que iframes en el detalle no se traguen el mousemove
    document.querySelectorAll("iframe").forEach(f => f.style.pointerEvents = "none");
    function onMove(ev) {
      let w = startW + (ev.clientX - startX);
      if (w < 200) w = 200;
      if (w > 800) w = 800;
      document.documentElement.style.setProperty("--list-width", w + "px");
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.querySelectorAll("iframe").forEach(f => f.style.pointerEvents = "");
      const finalW = parseInt(
        getComputedStyle(document.documentElement).getPropertyValue("--list-width"), 10) || 360;
      localStorage.setItem("listWidth", String(finalW));
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
})();

loadCurrentUser();
// Poblar selectores (proyecto → capacidad) ANTES de cargar el inbox.
loadMyProjects().then(loadMyCapabilities).then(loadInbox);

// Deep-link desde "Estado del proyecto": /inbox#wp=59 abre ese requerimiento
// (en la vista inbox). Útil al abrir el link de un requisito en otra pestaña.
function openWpFromHash() {
  const m = (location.hash || "").match(/wp=(\d+)/);
  if (!m) return;
  showView("inbox");
  loadDetail(parseInt(m[1], 10));
}
openWpFromHash();
window.addEventListener("hashchange", openWpFromHash);

// Deep-link a "Estado del proyecto": /inbox#proyecto  ó  /inbox#proyecto=<id>.
// Lo usa el Panel para reutilizar ESTA vista (única; no se duplica el Gantt).
function openProyectoFromHash() {
  const h = location.hash || "";
  if (!/(^|[#&])proyecto\b/.test(h)) return;
  const m = h.match(/proyecto=(\d+)/);
  if (m) {
    currentProject = m[1];
    localStorage.setItem("inboxProject", currentProject);
    const sel = document.getElementById("project-select");
    if (sel) sel.value = currentProject;
  }
  showView("proyecto");
}
openProyectoFromHash();
window.addEventListener("hashchange", openProyectoFromHash);
</script>
</body>
</html>
"""
