from __future__ import annotations

import json
from typing import Any

from fabrica.agents import roles
from fabrica.blackboard.service import Board
from fabrica.db.models import Estimate, MessageKind, Requirement
from fabrica.escalation.router import EscalationRouter
from fabrica.estimation.calculator import compute, estimation_table
from fabrica.llm.base import LLMRequest
from fabrica.verifiers.base import Verification


class EstimateVerifier:
    def __init__(self, objects: list[str]) -> None:
        self.objects = {o.upper() for o in objects}

    async def verify(self, output: dict[str, Any]) -> Verification:
        sizes = set(estimation_table().sizes)
        items = output.get("items") or []
        covered = {str(i.get("object", "")).upper() for i in items}
        issues = [f"Falta talla para {o}" for o in sorted(self.objects - covered)]
        issues += [f"Talla inválida {i.get('size')}" for i in items if i.get("size") not in sizes]
        return Verification.from_issues(issues)


async def estimate_requirement(
    router: EscalationRouter, board: Board, req: Requirement, spec: dict[str, Any]
) -> Estimate | None:
    objects = [str(o["name"]) for o in spec.get("objects", [])]
    prompt = (
        f"Título: {req.title}\nMódulo: {req.capability or '?'} · RICEFW {req.ricefw or '?'}\n\n"
        f"## Spec\n{spec.get('spec_markdown', '')}\n\n"
        f"## Objetos\n{json.dumps(spec.get('objects', []), ensure_ascii=False)}"
    )
    out = await router.run(
        req.id,
        "estimar",
        LLMRequest(system=roles.ESTIMADOR.system, prompt=prompt, schema=roles.ESTIMADOR.schema),
        EstimateVerifier(objects),
        agent=roles.ESTIMADOR.name,
    )
    if not out.passed or out.output is None:
        return None
    result = compute(out.output["items"])
    estimate = Estimate(
        requirement_id=req.id,
        items=result.items,
        breakdown=result.breakdown,
        assumptions=out.output.get("assumptions", []),
        hours_base=result.hours_base,
        hours_total=result.hours_total,
        days=result.days,
        complexity=result.complexity,
    )
    board.s.add(estimate)
    await board.post(
        req.id,
        thread="estimar",
        sender=roles.ESTIMADOR.name,
        kind=MessageKind.PROPUESTA,
        body=f"Estimación: {result.hours_total} h ({result.days} días, complejidad "
        f"{result.complexity})",
        data={"desglose": result.breakdown},
    )
    await board.s.flush()
    return estimate


def estimate_file(estimate: Estimate) -> str:
    return json.dumps(
        {
            "items": estimate.items,
            "desglose": estimate.breakdown,
            "asunciones": estimate.assumptions,
            "horas_base": estimate.hours_base,
            "horas_total": estimate.hours_total,
            "dias": estimate.days,
            "complejidad": estimate.complexity,
        },
        ensure_ascii=False,
        indent=2,
    )
