"""Workflow Temporal de la fábrica — máquina de fases DIRIGIDA POR EL BLOG.

`run()` recorre las etapas de negocio (recepcion → diseno → desarrollo →
pruebas_funcionales → pruebas_usuario → cerrado). Cada fase ESPERA una acción de
un driver en el blog (aprobar = avanza, devolver = fase anterior, rechazar =
cierra); delegar/responder solo mueven la pelota. El diseño/desarrollo reales
ocurren en Claude Code contra el repo del requerimiento vía MCP — el workflow
solo orquesta a quién le toca y proyecta el estado al read-model (proyecto-db).
Ver [[workflow-dirigido-por-blog]]. OpenProject quedó fuera.

Efectos por fase: al entrar a Recepción carga insumos (fetch_inputs +
load_architecture + snapshot_inputs); al entrar a Diseño/Desarrollo provisiona
el repo (provisionar_repo) y comenta el blog (blog_comment). Cada transición
llama save_timeline + proyectar_transicion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities import (
        agent_stage,
        blog_comment,
        fetch_inputs,
        load_architecture,
        provisionar_repo,
        proyectar_requisito,
        proyectar_transicion,
        save_timeline,
        snapshot_inputs,
    )

_API_TIMEOUT = timedelta(seconds=60)
_LOAD_TIMEOUT = timedelta(minutes=2)
_AGENT_TIMEOUT = timedelta(minutes=20)   # generación LLM del borrador (opcional)
_BLOG_MAX = 60000                         # tope del texto del borrador en el blog

# Modelo de flujo DIRIGIDO POR BLOG (hub-and-spoke). Fases en orden; cada una se
# resuelve por una acción de DRIVER en el blog (aprobar→avanza / devolver→atrás /
# rechazar→cerrado). El espejo data-driven vive en la tabla `fase` (lo usa el
# inbox para permisos/pelota). Ver [[workflow-dirigido-por-blog]].
_AVANZA = {
    "recepcion": "diseno",
    "diseno": "desarrollo",
    "desarrollo": "pruebas_funcionales",
    "pruebas_funcionales": "pruebas_usuario",
    "pruebas_usuario": "cerrado",
}
_DEVUELVE = {
    "diseno": "recepcion",
    "desarrollo": "diseno",
    "pruebas_funcionales": "desarrollo",
    "pruebas_usuario": "pruebas_funcionales",
}


@dataclass
class RequisitoInput:
    work_package_id: int
    project_id: int
    subject: str
    description: str
    gitea_owner: str
    gitea_repo: str


@dataclass
class HumanDecision:
    decision: str          # "approved" | "rejected"
    note: str = ""
    actor: str = ""
    # Skill elegido por el humano al liberar desarrollo (clave del catalogo).
    # Solo lo usa la compuerta de liberacion de desarrollo; las demas lo dejan
    # vacio. Define que skill recibe el agente de desarrollo.
    skill_key: str = ""
    # Liberar desarrollo: VARIOS skills a alimentar (tecnologia + estandares de
    # codificacion + estructura de repo/abapGit) y el nombre del paquete SAP.
    skill_keys: list = field(default_factory=list)
    sap_package: str = ""


@dataclass
class SpecReviewFeedback:
    """Feedback del revisor humano para el loop de revision de la spec."""
    action: str = "iterate"               # "approve" | "iterate" | "reject"
    model: str = ""                        # override de modelo para la sig iter
    instructions: str = ""                 # comentario libre del revisor
    focus_points: list[str] = field(default_factory=list)
    additional_context: str = ""           # info extra (respuestas a preguntas, etc.)
    actor: str = ""                        # quien reviso
    skill_key: str = ""                    # iterate del bloque desarrollo: cambiar skill




@workflow.defn
class FactoryWorkflow:
    def __init__(self) -> None:
        self._decision: HumanDecision | None = None
        self._clarified: bool = False
        # Modelo opcional que el humano elige al re-enviar tras una aclaración
        # (p.ej. subir de haiku a sonnet). "" = mantener el actual.
        self._clarified_model: str = ""
        self._spec_review: SpecReviewFeedback | None = None
        self._stage: str = "iniciando"
        # True mientras una compuerta humana espera decisión. Permite varias
        # compuertas secuenciales (VoBo funcional → VoBo usuario-clave) sin
        # acoplar el signal a un stage concreto.
        self._gate_waiting: bool = False
        # Borrador OPCIONAL del agente en la compuerta de diseño (rediseño Fase 4):
        # el humano puede pedir un borrador one-shot del paquete (spec+assertions)
        # como punto de partida; luego lo refina con Claude Code + MCP. No hay
        # loop automático. Ver [[rediseno-fase-diseno-mcp]].
        self._draft_requested: bool = False
        self._draft_model: str = ""
        # Historial de ETAPAS de negocio con timestamp (para el Panel: cuánto
        # tiempo lleva/llevó cada etapa). Una entrada por cambio de etapa.
        self._stage_history: list[dict] = []
        # Última acción del blog que DIRIGE el flujo (modelo nuevo hub-and-spoke).
        # El inbox/MCP la emite cuando un DRIVER aprueba/rechaza/devuelve/delega.
        # {accion, destino_rol, destino_usuario, actor}. Ver
        # [[workflow-dirigido-por-blog]].
        self._blog_action: dict | None = None

    @workflow.signal
    def blog_action(self, payload: dict) -> None:
        """Acción del blog que dirige el flujo (aprobar/rechazar/devolver/
        delegar). El loop de fase la consume. 'delegar' solo mueve la pelota
        (ya la movió el inbox en la DB) → el workflow la ignora y sigue."""
        self._blog_action = payload or {}

    @workflow.signal
    def human_decision(self, decision: HumanDecision) -> None:
        # Solo aceptar el signal cuando ALGUNA compuerta está esperando.
        # Entre compuertas (p.ej. tras el VoBo funcional y antes de armar el
        # VoBo usuario-clave) descartamos para no "preautorizar" la siguiente.
        # Como las compuertas son secuenciales, el signal se aplica a la que
        # esté activa ahora. Si llegan varios mientras esperamos, gana el último.
        if not self._gate_waiting:
            return
        self._decision = decision

    @workflow.signal
    def clarification_received(self, model: str = "") -> None:
        self._clarified = True
        # El re-envío puede traer un modelo mayor (sugerencia: sonnet). Los
        # emisores viejos (sin payload) dejan model="" → se mantiene el actual.
        if model:
            self._clarified_model = model

    @workflow.signal
    def spec_review(self, feedback: SpecReviewFeedback) -> None:
        """Feedback del revisor humano del loop de espec (approve/iterate/reject)."""
        self._spec_review = feedback

    @workflow.signal
    def generar_borrador(self, model: str = "") -> None:
        """Pide un borrador one-shot del paquete de diseño (ayudante OPCIONAL).
        Solo surte efecto si la compuerta de diseño está esperando (allow_draft).
        Tras generarlo, el workflow VUELVE a la compuerta — no hay loop. El
        humano refina con Claude Code + MCP. Ver [[rediseno-fase-diseno-mcp]]."""
        if not self._gate_waiting:
            return
        self._draft_requested = True
        if model:
            self._draft_model = model

    @workflow.query
    def current_stage(self) -> str:
        return self._stage

    @workflow.query
    def stage_history(self) -> list:
        """Historial de etapas con timestamp (para el Panel)."""
        return self._stage_history

    # --- handlers de bloques --------------------------------------------
    async def _do_load_inputs(self, state: dict, block: dict) -> None:
        wp_id = state["ctx"]["work_package_id"]
        inputs = await workflow.execute_activity(
            fetch_inputs, args=[wp_id], start_to_close_timeout=_LOAD_TIMEOUT)
        architecture = await workflow.execute_activity(
            load_architecture, args=[], start_to_close_timeout=_LOAD_TIMEOUT)
        state["inputs"] = inputs
        state["architecture"] = architecture
        state["ctx"]["description"] = inputs.get("description", "")
        state["ctx"]["subject"] = inputs.get("subject") or state["ctx"]["subject"]
        state["ctx"]["parent_subject"] = state["ctx"]["subject"]
        await workflow.execute_activity(
            snapshot_inputs, args=[{
                "work_package_id": wp_id,
                "owner": state["inp"].gitea_owner,
                "repo": state["inp"].gitea_repo,
                "branch": state["branch"],
                "subject": state["ctx"]["subject"],
                "description": state["ctx"]["description"],
                "images": inputs.get("images", []),
                "pdfs": inputs.get("pdfs", []),
                "documents": inputs.get("documents", []),
                "architecture_markdown": architecture.get("markdown", ""),
                "architecture_sha": architecture.get("sha"),
            }],
            start_to_close_timeout=_API_TIMEOUT)

    async def _do_provisionar_repo(self, state: dict, block: dict) -> None:
        """Fase desarrollo (rediseño): crea el repo del requerimiento bajo la
        org del cliente y lo siembra (diseño + adjuntos + abapGit + CLAUDE.md).
        Guarda las coordenadas en state y comenta el WP con la URL de clone."""
        wp_id = state["ctx"]["work_package_id"]
        pa = {
            "work_package_id": wp_id,
            "project_id": state["inp"].project_id,
            "subject": state["ctx"]["subject"],
        }
        # phase del bloque (diseno = temprano para Claude Code; dev = al
        # desarrollo). patched(): workflows en vuelo (sin phase en su historia)
        # mantienen los args viejos → replay determinista.
        diseno_phase = False
        if workflow.patched("provisionar-repo-phase-v1"):
            phase = block.get("phase", "dev")
            pa["phase"] = phase
            diseno_phase = (phase == "diseno")
        res = await workflow.execute_activity(
            provisionar_repo, args=[pa],
            start_to_close_timeout=_LOAD_TIMEOUT)
        state["dev_repo"] = res
        stack = "ABAP/SAP (abapGit)" if res.get("is_abap") else "git"
        if diseno_phase:
            msg = (f"🧩 Workspace de **diseño** listo (**{stack}**):\n\n"
                   f"`git clone {res.get('clone_url')}`\n\n"
                   f"Org (cliente): **{res.get('cliente') or res.get('org')}** · "
                   f"insumos + `CLAUDE.md` de diseño + MCP sembrados. Genera el "
                   f"paquete (spec/prototipo/casos/soporte) con Claude Code en "
                   f"`diseno/` y publícalo por **git** (`git add diseno/ && git "
                   f"commit && git push`) + `enviar_a_revision(tu_nombre)`.")
        else:
            msg = (f"🧰 Repo del requerimiento listo para desarrollo local "
                   f"(**{stack}**):\n\n`git clone {res.get('clone_url')}`\n\n"
                   f"Org (cliente): **{res.get('cliente') or res.get('org')}** · "
                   f"diseño + adjuntos sembrados. Clónalo y trabájalo con "
                   f"Claude Code/Codex; al terminar, libéralo a pruebas.")
        await workflow.execute_activity(
            blog_comment, args=[{"wp_id": wp_id, "texto": msg}],
            start_to_close_timeout=_API_TIMEOUT)

    # --- modelo dirigido por blog (hub-and-spoke) -----------------------
    async def _transition_fase(self, state: dict, etapa: str) -> None:
        """Entra a una fase: proyecta la etapa al read-model (registrar_transicion
        cierra el segmento previo, abre el nuevo y pone la PELOTA en el hub de la
        fase) y agrega la entrada al timeline. Idempotente si ya estamos ahí."""
        if etapa == self._stage:
            return
        self._stage = etapa
        ts = workflow.now().isoformat()
        if (not self._stage_history
                or self._stage_history[-1].get("etapa_key") != etapa):
            self._stage_history.append(
                {"stage": etapa, "etapa": etapa, "etapa_key": etapa, "ts": ts})
            await workflow.execute_activity(
                save_timeline, args=[{
                    "work_package_id": state["ctx"]["work_package_id"],
                    "owner": state["inp"].gitea_owner,
                    "repo": state["inp"].gitea_repo,
                    "branch": state["branch"],
                    "timeline": self._stage_history,
                }],
                start_to_close_timeout=_API_TIMEOUT)
        await workflow.execute_activity(
            proyectar_transicion, args=[{
                "wp_id": state["ctx"]["work_package_id"],
                "etapa": etapa, "ts": ts, "es_espera": False,
            }],
            start_to_close_timeout=_API_TIMEOUT)

    async def _on_enter_phase(self, state: dict, etapa: str) -> None:
        """Efectos al ENTRAR a una fase: cargar insumos en Recepción, provisionar
        el repo de diseño al entrar a Diseño y el de desarrollo al entrar a
        Desarrollo. Todo idempotente (re-entrar por 'devolver' no duplica)."""
        if etapa == "recepcion":
            if state.get("inputs") is None:
                await self._do_load_inputs(state, {})
        elif etapa == "diseno":
            await self._do_provisionar_repo(state, {"phase": "diseno"})
        elif etapa == "desarrollo":
            await self._do_provisionar_repo(state, {"phase": "dev"})

    async def _run_phase(self, state: dict, current: str = "") -> str:
        """Espera acciones del blog hasta que un DRIVER resuelva la fase. Devuelve
        'aprobar' | 'rechazar' | 'devolver'. 'delegar'/'responder' solo mueven la
        pelota (ya lo hizo el inbox en la DB) → el workflow sigue esperando.

        En la fase de diseño, también atiende el ayudante OPCIONAL 'generar
        borrador con IA' (señal generar_borrador): genera un borrador de spec y
        lo POSTEA AL BLOG (no toca el repo — el dev lo copia a su clon), luego
        vuelve a esperar. No avanza la fase."""
        draft_on = workflow.patched("draft-to-blog-v1")
        while True:
            await workflow.wait_condition(
                lambda: self._blog_action is not None
                or (draft_on and self._draft_requested))
            if draft_on and self._draft_requested:
                self._draft_requested = False
                if current == "diseno":
                    await self._do_generate_draft_to_blog(state)
                continue
            act = self._blog_action or {}
            self._blog_action = None
            accion = act.get("accion")
            if accion in ("aprobar", "rechazar", "devolver"):
                return accion
            # delegar / responder → la pelota ya cambió en la DB; seguir.

    async def _do_generate_draft_to_blog(self, state: dict) -> None:
        """Ayudante OPCIONAL: corre el agente de diseño y postea el borrador de
        spec al BLOG como punto de partida. NO escribe el repo (evita
        desincronizar el clon del dev) y NO avanza la fase."""
        wp_id = state["ctx"]["work_package_id"]
        if state.get("inputs") is None:
            await self._do_load_inputs(state, {})
        res = await workflow.execute_activity(
            agent_stage,
            args=["diseno", state["ctx"], state.get("inputs"),
                  state.get("architecture"), self._draft_model or None],
            start_to_close_timeout=_AGENT_TIMEOUT)
        named = (res or {}).get("named_artifacts") or {}
        spec = named.get("spec_md") or (res or {}).get("markdown") or ""
        cuerpo = spec[:_BLOG_MAX] if spec else "_(el agente no devolvió spec)_"
        texto = (
            "🤖 **Borrador de spec generado por IA** (ayudante opcional). Es un "
            "punto de partida: cópialo a `diseno/spec.md` en tu clon, refínalo "
            "con Claude Code y publícalo por `git push`. El flujo **no** avanzó.\n\n"
            "---\n\n" + cuerpo)
        await workflow.execute_activity(
            blog_comment, args=[{"wp_id": wp_id, "texto": texto}],
            start_to_close_timeout=_API_TIMEOUT)

    # --- entrypoint -----------------------------------------------------
    @workflow.run
    async def run(self, inp: RequisitoInput) -> dict:
        branch = f"req-{inp.work_package_id}"
        state = {
            "inp": inp,
            "branch": branch,
            "refs": f"refs #{inp.work_package_id}",
            "inputs": None,
            "architecture": None,
            "ctx": {
                "work_package_id": inp.work_package_id,
                "subject": inp.subject,
                "description": "",
                "parent_subject": inp.subject,
            },
        }

        # Registrar el requisito en el read-model (DB de proyecto).
        await workflow.execute_activity(
            proyectar_requisito, args=[{
                "wp_id": inp.work_package_id,
                "project_id": inp.project_id,
                "subject": inp.subject,
                "repo_org": inp.gitea_owner,
                "repo_name": inp.gitea_repo,
                "repo_branch": branch,
            }],
            start_to_close_timeout=_API_TIMEOUT)

        # Máquina de fases dirigida por el BLOG. Cada fase espera que un driver
        # apruebe (avanza), devuelva (fase anterior) o rechace (cierra). El
        # diseño/desarrollo reales ocurren en Claude Code contra el repo vía MCP;
        # el blog solo conduce a quién le toca. Ver [[workflow-dirigido-por-blog]].
        current = "recepcion"
        while True:
            await self._transition_fase(state, current)
            await self._on_enter_phase(state, current)
            accion = await self._run_phase(state, current)
            if accion == "rechazar":
                await self._transition_fase(state, "cerrado")
                return {"result": "rejected", "work_package_id": inp.work_package_id,
                        "branch": branch, "rejected_at": current}
            if accion == "devolver":
                current = _DEVUELVE.get(current, current)
                continue
            # aprobar
            nxt = _AVANZA.get(current)
            if not nxt or nxt == "cerrado":
                await self._transition_fase(state, "cerrado")
                return {"result": "completed",
                        "work_package_id": inp.work_package_id, "branch": branch}
            current = nxt
