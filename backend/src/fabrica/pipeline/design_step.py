from __future__ import annotations

import json

from fabrica.agents.design_roles import ARQUITECTO
from fabrica.config import get_settings
from fabrica.db.models import Requirement
from fabrica.estimation.step import estimate_file, estimate_requirement
from fabrica.knowledge.standards import standards
from fabrica.pipeline.step_support import (
    SPEC_JSON,
    StepResult,
    StepTools,
    request_for,
    requirement_context,
)
from fabrica.verifiers.spec import SpecVerifier


async def run_design(tools: StepTools, req: Requirement) -> StepResult:
    allowed = get_settings().sap_allowed_packages
    out = await tools.router.run(
        req.id,
        "disenar_spec",
        request_for(
            ARQUITECTO,
            await requirement_context(tools.board, req) + standards().render(req.capability),
        ),
        SpecVerifier(allowed),
        agent=ARQUITECTO.name,
    )
    if not out.passed or out.output is None:
        return StepResult("blocked", "El diseño requiere una persona")
    estimate = await estimate_requirement(tools.router, tools.board, req, out.output)
    if estimate is None:
        return StepResult("blocked", "La estimación requiere una persona")
    save_files = {
        "diseno/spec.md": out.output["spec_markdown"],
        SPEC_JSON: json.dumps(out.output, ensure_ascii=False, indent=2),
        "diseno/estimacion.json": estimate_file(estimate),
    }
    if out.output.get("prototype_html"):
        save_files["diseno/prototipo.html"] = out.output["prototype_html"]
    if out.output.get("test_plan_markdown"):
        save_files["diseno/plan_de_pruebas.md"] = out.output["test_plan_markdown"]
    await tools.save(
        req,
        save_files,
        "Diseño: especificación, prototipo, plan de pruebas y estimación",
        ARQUITECTO.name,
    )
    return StepResult("advance")
