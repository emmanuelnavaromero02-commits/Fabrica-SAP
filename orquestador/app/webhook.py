"""Receptor de webhooks de OpenProject + API de control del orquestador.

Endpoints:
  GET  /health                      - sonda de salud.
  POST /webhooks/openproject        - webhook de OpenProject: valida la firma,
                                      arranca o senaliza workflows segun el
                                      cambio de estado del work package.
  POST /factory/start               - arranca un workflow a mano (operacion
                                      y pruebas sin depender del webhook).
  POST /factory/{wp_id}/decision    - envia la senal de compuerta humana.
  POST /factory/{wp_id}/clarified   - senal de que el consultor contesto las
                                      preguntas del modo blocked.
  POST /factory/{wp_id}/review      - feedback del revisor humano para el
                                      loop de revision de la spec.
  GET  /factory/{wp_id}             - estado del workflow (etapa actual).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.service import RPCError

import config
import auth
import inbox
from workflow import (
    FactoryWorkflow,
    HumanDecision,
    RequisitoInput,
    SpecReviewFeedback,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orquestador.webhook")

_client: Client | None = None


async def _connect_with_retry(attempts: int = 40, delay: float = 3.0) -> Client:
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await Client.connect(config.TEMPORAL_ADDRESS,
                                        namespace=config.TEMPORAL_NAMESPACE)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.info("Temporal aun no responde; reintento %d/%d", i, attempts)
            await asyncio.sleep(delay)
    raise RuntimeError(f"No se pudo conectar a Temporal: {last}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _client
    _client = await _connect_with_retry()
    log.info("Conectado a Temporal (%s)", config.TEMPORAL_ADDRESS)
    # Comparte el client con el modulo inbox (evita reconectar).
    inbox.set_temporal_client(_client)
    yield


app = FastAPI(title="Orquestador de la fabrica", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(inbox.router)


# Middleware: el inbox y sus APIs requieren sesión. Los endpoints que
# consume el webhook de OpenProject o el operador por CLI (factory/start,
# /webhooks/*) NO requieren auth (siguen siendo internos del cluster).
@app.middleware("http")
async def require_auth_for_inbox(request: Request, call_next):
    path = request.url.path
    needs_auth = (
        path == "/inbox" or
        path == "/reporte-avance" or
        path.startswith("/api/")
    )
    if needs_auth and config.OIDC_ISSUER:
        user = auth.get_current_user(request)
        if not user:
            # Browser → redirect a login; APIs → 401 JSON.
            accept = request.headers.get("accept", "")
            if "text/html" in accept and request.method == "GET":
                return _redirect(f"/auth/login?next={path}")
            return _json_401()
        # Líder de proyecto de CLIENTE: SOLO el reporte de avance y sus datos;
        # todo lo demás del inbox → 403 (defensa en servidor, no solo el front).
        if inbox.es_lider_cliente_solo(user.get("roles")) \
                and not _cliente_reporte_ok(path, request.method):
            accept = request.headers.get("accept", "")
            if "text/html" in accept and request.method == "GET":
                return _redirect("/reporte-avance")
            return JSONResponse(
                {"detail": "restringido al reporte de avance"}, status_code=403)
    return await call_next(request)


def _cliente_reporte_ok(path: str, method: str) -> bool:
    """Whitelist de rutas del inbox que el líder de proyecto de CLIENTE sí puede
    tocar (el reporte de avance + sus fuentes de datos)."""
    if path in ("/reporte-avance", "/api/mis-proyectos", "/health"):
        return True
    if path == "/api/export/xlsx" and method == "POST":
        return True
    if path.startswith("/api/proyecto/") and (
            path.endswith("/resumen") or path.endswith("/comentarios-export")):
        return True
    return False


from fastapi.responses import RedirectResponse, JSONResponse  # noqa: E402

def _redirect(url: str):
    return RedirectResponse(url, status_code=302)

def _json_401():
    return JSONResponse({"detail": "not authenticated"}, status_code=401)


# --------------------------------------------------------------------------
def _workflow_id(work_package_id: int) -> str:
    return f"requisito-{work_package_id}"


async def _start_workflow(inp: RequisitoInput) -> str:
    assert _client is not None
    wid = _workflow_id(inp.work_package_id)
    await _client.start_workflow(
        FactoryWorkflow.run, inp, id=wid, task_queue=config.TASK_QUEUE,
    )
    log.info("Workflow arrancado: %s", wid)
    return wid


async def _signal_decision(work_package_id: int, decision: HumanDecision) -> str:
    assert _client is not None
    wid = _workflow_id(work_package_id)
    handle = _client.get_workflow_handle(wid)
    await handle.signal(FactoryWorkflow.human_decision, decision)
    log.info("Senal de decision enviada a %s: %s", wid, decision.decision)
    return wid


# --------------------------------------------------------------------------
class StartRequest(BaseModel):
    work_package_id: int
    project_id: int
    subject: str
    description: str = ""
    gitea_owner: str | None = None
    gitea_repo: str | None = None


class DecisionRequest(BaseModel):
    decision: str = "approved"   # "approved" | "rejected"
    note: str = ""
    actor: str = "manual"
    # Skill elegido al liberar desarrollo (clave del catalogo). Vacio en las
    # demas compuertas.
    skill_key: str = ""
    # Liberar desarrollo: varios skills + paquete SAP.
    skill_keys: list[str] = []
    sap_package: str = ""


class ReviewRequest(BaseModel):
    action: str = "iterate"      # "approve" | "iterate" | "reject"
    model: str = ""              # override del modelo para la siguiente iter
    instructions: str = ""       # comentario libre del revisor
    focus_points: list[str] = []
    additional_context: str = ""
    actor: str = "manual"
    skill_key: str = ""          # iterate del bloque desarrollo: cambiar skill


class BlogActionRequest(BaseModel):
    # Acción del blog originada en el MCP (Claude Code). Ver
    # [[workflow-dirigido-por-blog]].
    accion: str = "responder"    # responder|delegar|aprobar|rechazar|devolver
    destino_rol: str = ""        # delegar → a qué rol pasa la pelota
    destino_usuario: str = ""
    actor: str = "claude-code"
    texto: str = ""


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/factory/start")
async def factory_start(req: StartRequest) -> dict:
    """Arranca un workflow de fabrica a mano (operacion / pruebas)."""
    inp = RequisitoInput(
        work_package_id=req.work_package_id,
        project_id=req.project_id,
        subject=req.subject,
        description=req.description,
        gitea_owner=req.gitea_owner or config.FACTORY_GITEA_OWNER,
        gitea_repo=req.gitea_repo or config.FACTORY_GITEA_REPO,
    )
    try:
        wid = await _start_workflow(inp)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"started": wid}


@app.post("/factory/{work_package_id}/decision")
async def factory_decision(work_package_id: int, req: DecisionRequest) -> dict:
    """Envia la senal de compuerta humana a un workflow en espera."""
    decision = HumanDecision(decision=req.decision, note=req.note,
                             actor=req.actor, skill_key=req.skill_key,
                             skill_keys=list(req.skill_keys or []),
                             sap_package=req.sap_package)
    try:
        wid = await _signal_decision(work_package_id, decision)
    except RPCError as exc:
        raise HTTPException(status_code=404,
                            detail=f"workflow no encontrado: {exc}") from exc
    return {"signaled": wid, "decision": req.decision}


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


@app.api_route("/factory/mcp-validate", methods=["GET", "POST"])
async def factory_mcp_validate(request: Request) -> dict:
    """Valida el token personal de MCP del header Authorization y devuelve
    {usuario, roles}. Lo usan (a) el middleware del MCP `fabrica` y (b) el
    `forward_auth` de Caddy para los MCP `abap`/`sapcfg`/`cpimig` — Caddy hace
    un GET reenviando el Authorization, así el MISMO token personal gatea TODOS
    los MCP y revocarlo en `mcp_token` corta el acceso a todos. Ver
    [[unificacion-auth-mcp-por-token-personal]]."""
    import proyeccion
    user = await proyeccion.validar_mcp_token(_bearer(request))
    if not user:
        raise HTTPException(status_code=401, detail="token MCP inválido")
    return user


@app.post("/factory/{work_package_id}/blog-action")
async def factory_blog_action(work_package_id: int, req: BlogActionRequest,
                              request: Request) -> dict:
    """Acción del blog originada en el MCP (Claude Code): registra el comentario
    en proyecto-db y, si dirige el flujo (delegar/aprobar/rechazar/devolver),
    señala al workflow. El ACTOR sale del token personal de MCP (header
    Authorization) → identidad real + roles para permisos. Ver
    [[workflow-dirigido-por-blog]]."""
    import proyeccion
    accion = req.accion
    if accion not in ("responder", "delegar", "aprobar", "rechazar",
                      "devolver", "tomar"):
        raise HTTPException(status_code=400, detail="acción inválida")
    estado = await proyeccion.get_estado(work_package_id)
    etapa = (estado or {}).get("etapa_actual")
    pelota_rol = (estado or {}).get("pelota_rol")

    # Identidad por token (actor automático + roles). Fallback: body.actor.
    tok_user = await proyeccion.validar_mcp_token(_bearer(request))
    actor = (tok_user or {}).get("usuario") or req.actor or "claude-code"
    roles = set((tok_user or {}).get("roles") or [])

    # Permisos por fase (si hay roles del token). Sin token, se confía (compat).
    fase = await proyeccion.get_fase(etapa) if etapa else None
    drivers = set((fase or {}).get("drivers") or [])
    radios = set((fase or {}).get("radios") or [])
    soy_driver = bool(roles & drivers)
    soy_radio = bool(roles & radios) and not soy_driver
    if roles and accion in ("delegar", "aprobar", "rechazar", "devolver") and not soy_driver:
        raise HTTPException(
            status_code=403,
            detail=f"'{actor}' no es driver de la fase '{etapa}' para {accion}")
    if roles and accion == "tomar" and pelota_rol and pelota_rol not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"'{actor}' no tiene el rol '{pelota_rol}' para tomar")

    cuerpo = req.texto or {
        "aprobar": "✅ Aprobado (desde Claude Code)",
        "rechazar": "⛔ Rechazado (desde Claude Code)",
        "devolver": "↩️ Devuelto a la fase anterior (desde Claude Code)",
        "tomar": f"✋ {actor} tomó el requerimiento (desde Claude Code)",
        "delegar": f"🔎 Pasa la pelota a {req.destino_usuario or req.destino_rol}",
    }.get(accion, "(sin texto)")
    cid = await proyeccion.agregar_comentario(
        work_package_id, actor, actor, cuerpo, None, accion=accion,
        destino_rol=(req.destino_rol or None),
        destino_usuario=(req.destino_usuario or None), etapa=etapa,
        autor_es_radio=soy_radio)
    if cid is None:
        raise HTTPException(status_code=503, detail="blog no disponible (proyecto-db)")
    if accion in ("delegar", "aprobar", "rechazar", "devolver") and _client is not None:
        try:
            await _client.get_workflow_handle(
                _workflow_id(work_package_id)).signal(
                "blog_action", {"accion": accion,
                                "destino_rol": req.destino_rol or None,
                                "destino_usuario": req.destino_usuario or None,
                                "actor": actor})
        except Exception as exc:  # noqa: BLE001
            log.warning("signal blog_action wp=%s falló (no fatal): %s",
                        work_package_id, exc)
    return {"ok": True, "id": cid, "accion": accion, "actor": actor}


@app.post("/factory/{work_package_id}/review")
async def factory_review(work_package_id: int, req: ReviewRequest) -> dict:
    """Feedback del revisor humano al loop de revision de la spec.

    action='iterate' -> el agente re-corre con el feedback acumulado
                        (opcional cambiar 'model').
    action='approve' -> promueve la iteracion actual al artefacto vigente
                        y el workflow avanza al siguiente bloque.
    action='reject'  -> aborta el bloque (rara vez deseado; usa iterate)."""
    assert _client is not None
    wid = _workflow_id(work_package_id)
    feedback = SpecReviewFeedback(
        action=req.action, model=req.model, instructions=req.instructions,
        focus_points=list(req.focus_points or []),
        additional_context=req.additional_context, actor=req.actor,
        skill_key=req.skill_key,
    )
    try:
        await _client.get_workflow_handle(wid).signal(
            FactoryWorkflow.spec_review, feedback)
    except RPCError as exc:
        raise HTTPException(status_code=404,
                            detail=f"workflow no encontrado: {exc}") from exc
    return {"signaled": wid, "kind": "spec_review", "action": req.action}


@app.post("/factory/{work_package_id}/clarified")
async def factory_clarified(work_package_id: int,
                            body: dict | None = None) -> dict:
    """Reanuda el workflow despues de que el consultor contesto las preguntas
    del modo blocked. Lo dispara el webhook de OpenProject (cuando el WP sale
    de 'Esperando clarificacion') o se llama a mano para probar.

    Body opcional: {model: "claude-sonnet-4-6"} para re-correr el agente con un
    modelo mayor (sugerido cuando haiku bloquea por ambigüedad)."""
    assert _client is not None
    wid = _workflow_id(work_package_id)
    model = (body or {}).get("model") or ""
    try:
        await _client.get_workflow_handle(wid).signal(
            FactoryWorkflow.clarification_received, model
        )
    except RPCError as exc:
        raise HTTPException(status_code=404,
                            detail=f"workflow no encontrado: {exc}") from exc
    return {"signaled": wid, "kind": "clarification", "model": model}


@app.get("/factory/{work_package_id}")
async def factory_status(work_package_id: int) -> dict:
    """Etapa actual del workflow (query de Temporal, no lo interrumpe)."""
    assert _client is not None
    handle = _client.get_workflow_handle(_workflow_id(work_package_id))
    try:
        stage = await handle.query(FactoryWorkflow.current_stage)
    except RPCError as exc:
        raise HTTPException(status_code=404,
                            detail=f"workflow no encontrado: {exc}") from exc
    return {"work_package_id": work_package_id, "stage": stage}


