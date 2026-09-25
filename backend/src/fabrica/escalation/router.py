from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from fabrica.blackboard.service import Board
from fabrica.catalog import ModelCatalog, ModelSpec, model_catalog
from fabrica.db.models import Attempt, MessageKind
from fabrica.escalation.handoff import AttemptRecord, Handoff
from fabrica.escalation.learning import learned_tiers, pass_rates
from fabrica.knowledge.lessons import record_lessons, relevant_lessons, render_lessons
from fabrica.llm.base import LLMError, LLMRequest
from fabrica.llm.gateway import ModelGateway
from fabrica.verifiers.base import Verification, Verifier


@dataclass
class RouterOutcome:
    passed: bool
    output: dict[str, Any] | None
    tier: str | None
    provider: str | None
    issues: list[str]
    reason: str = ""


class EscalationRouter:
    def __init__(
        self, gateway: ModelGateway, board: Board, catalog: ModelCatalog | None = None
    ) -> None:
        self.gateway = gateway
        self.board = board
        self.catalog = catalog or model_catalog()

    def _pick(self, tier: str, attempt: int, avoid: str | None) -> ModelSpec:
        models = self.catalog.tiers[tier].models
        if avoid:
            others = [m for m in models if m.provider != avoid]
            models = others or models
        return models[attempt % len(models)]

    async def _over_budget(self, req_id: int) -> bool:
        req = await self.board.requirement(req_id)
        return req.spent_usd >= self.catalog.budget_usd_per_requirement

    async def run(
        self,
        req_id: int,
        activity: str,
        request: LLMRequest,
        verifier: Verifier,
        *,
        agent: str,
        avoid_provider: str | None = None,
    ) -> RouterOutcome:
        policy = self.catalog.policy(activity)
        handoff = Handoff(activity)
        tiers = await self._tiers(req_id, activity)
        base_prompt = request.prompt + await self._lessons(activity, request.prompt)

        for tier in tiers:
            for n in range(policy.attempts):
                if await self._over_budget(req_id):
                    return await self._to_human(
                        req_id, activity, agent, handoff, "presupuesto agotado"
                    )

                spec = self._pick(tier, n, avoid_provider if policy.cross_vendor else None)
                attempt_req = replace(
                    request,
                    prompt=base_prompt + handoff.render(),
                    tags={**request.tags, "activity": activity},
                )
                verification, output, cost, usage = await self._attempt(
                    spec, tier, attempt_req, verifier
                )

                await self.board.record_attempt(
                    Attempt(
                        requirement_id=req_id,
                        activity=activity,
                        tier=tier,
                        provider=spec.provider,
                        model=spec.model,
                        tokens_in=usage[0],
                        tokens_out=usage[1],
                        cost_usd=cost,
                        passed=verification.passed,
                        issues=verification.issues,
                    )
                )
                if verification.passed:
                    await self._learn(req_id, activity, handoff)
                    await self.board.post(
                        req_id,
                        thread=activity,
                        sender=f"{agent}@{tier}",
                        kind=MessageKind.PROPUESTA,
                        body=f"{activity} aprobado por verificación ({spec.model})",
                        data={"tier": tier, "evidence": verification.evidence},
                    )
                    await self.board.checkpoint()
                    return RouterOutcome(True, output, tier, spec.provider, [])

                handoff.add(AttemptRecord(tier, spec.model, output, verification.issues))
                await self.board.post(
                    req_id,
                    thread=activity,
                    sender="verificador",
                    kind=MessageKind.OBJECION,
                    body=f"{spec.model} ({tier}) no pasó: {len(verification.issues)} problema(s)",
                    recipient=f"{agent}@{tier}",
                    data={"issues": verification.issues},
                )
                await self.board.checkpoint()

            if tier != tiers[-1]:
                await self.board.post(
                    req_id,
                    thread=activity,
                    sender="router",
                    kind=MessageKind.ESCALAMIENTO,
                    body=f"Escalando {activity} desde {tier} al siguiente nivel",
                )

        return await self._to_human(req_id, activity, agent, handoff, "niveles agotados")

    async def _tiers(self, req_id: int, activity: str) -> list[str]:
        policy = self.catalog.policy(activity)
        rates = await pass_rates(self.board.s, activity)
        tiers = learned_tiers(policy, self.catalog.learning, rates)
        if tiers[0] != policy.start:
            await self.board.post(
                req_id,
                thread=activity,
                sender="router",
                kind=MessageKind.INFO,
                body=f"{activity} inicia en {tiers[0]}: el historial de {policy.start} no alcanza "
                f"la tasa mínima de éxito",
                data={"historial": {t: list(v) for t, v in rates.items()}},
            )
        return tiers

    async def _lessons(self, activity: str, context: str) -> str:
        lessons = await relevant_lessons(self.board.s, activity, context)
        for lesson in lessons:
            lesson.uses += 1
        return render_lessons(lessons)

    async def _learn(self, req_id: int, activity: str, handoff: Handoff) -> None:
        issues = [issue for record in handoff.history for issue in record.issues]
        if not issues:
            return
        req = await self.board.requirement(req_id)
        await record_lessons(
            self.board.s,
            activity=activity,
            issues=[i for i in issues if not i.startswith("[proveedor]")],
            requirement_id=req_id,
            capability=req.capability,
        )

    async def _attempt(
        self, spec: ModelSpec, tier: str, request: LLMRequest, verifier: Verifier
    ) -> tuple[Verification, dict[str, Any] | None, float, tuple[int, int]]:
        try:
            res = await self.gateway.call(spec, request, tier=tier)
        except LLMError as exc:
            spent = (exc.tokens_in, exc.tokens_out)
            return Verification(False, [f"[proveedor] {exc}"]), None, spec.cost(*spent), spent
        usage = (res.result.tokens_in, res.result.tokens_out)
        if res.result.refused:
            return (
                Verification(False, ["[proveedor] el modelo rechazó la tarea"]),
                None,
                res.cost_usd,
                usage,
            )
        if res.result.data is None:
            return Verification(False, ["[formato] respuesta sin JSON"]), None, res.cost_usd, usage
        try:
            verification = await verifier.verify(res.result.data)
        except Exception as exc:
            verification = Verification(False, [f"[verificador] {type(exc).__name__}: {exc}"])
        return verification, res.result.data, res.cost_usd, usage

    async def _to_human(
        self, req_id: int, activity: str, agent: str, handoff: Handoff, reason: str
    ) -> RouterOutcome:
        last = handoff.last
        issues = last.issues if last else []
        await self.board.post(
            req_id,
            thread=activity,
            sender="router",
            kind=MessageKind.ESCALAMIENTO,
            recipient="persona",
            body=f"{activity}: requiere intervención humana ({reason})",
            data={"issues": issues, "intentos": len(handoff.history)},
        )
        return RouterOutcome(False, last.output if last else None, None, None, issues, reason)
