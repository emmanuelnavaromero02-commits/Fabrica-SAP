from __future__ import annotations

import json

from fabrica.agents.build_roles import DESARROLLADOR, DOCUMENTADOR, REVISOR
from fabrica.db.models import Requirement
from fabrica.knowledge.standards import standards
from fabrica.pipeline.step_support import (
    SPEC_JSON,
    IssueCheck,
    StepResult,
    StepTools,
    request_for,
    requirement_context,
)
from fabrica.sap.bridge import Assertion
from fabrica.sap.factory import sap_for
from fabrica.sap.systems import SapNotConfigured
from fabrica.verifiers.abap import AbapVerifier
from fabrica.workspace.manager import WorkspaceManager


async def run_build(tools: StepTools, req: Requirement) -> StepResult:
    raw = await tools.repos.read_file(req.id, SPEC_JSON)
    if raw is None:
        return StepResult("blocked", "No hay spec aprobada en el repo")
    spec = json.loads(raw)
    main = spec["objects"][0]
    assertions = [Assertion(**a) for a in spec["assertions"]]
    try:
        sap = await sap_for(tools.board, req.id, DESARROLLADOR.name)
    except SapNotConfigured as exc:
        return StepResult("blocked", str(exc))
    verifier = AbapVerifier(sap, main_object=main, assertions=assertions)
    prompt = (
        f"{await requirement_context(tools.board, req)}\n\n## Spec\n{spec['spec_markdown']}\n\n"
        f"Objeto principal: {main['name']}\n"
        f"Aseveraciones: {json.dumps(spec['assertions'], ensure_ascii=False)}"
        f"{standards().render(req.capability)}"
    )

    workspace = await WorkspaceManager().prepare(
        req.id, tools.repos.clone_url(req.id), tools.repos.git_auth_env()
    )
    dev = await tools.router.run(
        req.id,
        "implementar",
        request_for(DESARROLLADOR, prompt, workdir=str(workspace)),
        verifier,
        agent=DESARROLLADOR.name,
    )
    if not dev.passed or dev.output is None:
        return StepResult("blocked", "La implementación requiere una persona")

    code = "\n\n".join(f"// {f['path']}\n{f['content']}" for f in dev.output["files"])
    review = await tools.router.run(
        req.id,
        "revisar",
        request_for(REVISOR, f"{prompt}\n\n## Código\n{code}"),
        IssueCheck(
            lambda o: [] if o.get("approved") else list(o.get("objections") or ["Rechazado"])
        ),
        agent=REVISOR.name,
        avoid_provider=dev.provider,
    )
    if not review.passed:
        return StepResult("blocked", "El revisor dejó objeciones sin resolver")

    docs = await tools.router.run(
        req.id,
        "documentar",
        request_for(DOCUMENTADOR, f"{prompt}\n\n## Código\n{code}"),
        IssueCheck(lambda o: [] if o.get("markdown") else ["Documento vacío"]),
        agent=DOCUMENTADOR.name,
    )
    files = {f["path"]: f["content"] for f in dev.output["files"]}
    files["evidencia/construccion.json"] = json.dumps(
        {"nivel": dev.tier, "proveedor": dev.provider, "revisor": review.provider},
        ensure_ascii=False,
        indent=2,
    )
    if docs.passed and docs.output:
        files["docs/manual-tecnico.md"] = docs.output["markdown"]
    await tools.save(
        req, files, f"Construcción: código verificado ({dev.tier})", DESARROLLADOR.name
    )
    return StepResult("advance")
