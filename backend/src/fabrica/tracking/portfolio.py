from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fabrica.db.models import Attempt, Decision, Estimate, Requirement, RunState
from fabrica.tracking.time import hours_by_requirement


def _days(start: datetime, end: datetime) -> float:
    start = start if start.tzinfo else start.replace(tzinfo=end.tzinfo)
    end = end if end.tzinfo else end.replace(tzinfo=start.tzinfo)
    return round((end - start).total_seconds() / 86400, 2)


def first_pass_rates(attempts: list[Attempt]) -> dict[str, float]:
    first: dict[tuple[int, str], bool] = {}
    for attempt in sorted(attempts, key=lambda a: a.id):
        first.setdefault((attempt.requirement_id, attempt.activity), attempt.passed)
    per_activity: dict[str, list[bool]] = defaultdict(list)
    for (_, activity), passed in first.items():
        per_activity[activity].append(passed)
    return {a: round(sum(v) / len(v), 3) for a, v in sorted(per_activity.items())}


async def portfolio(session: AsyncSession) -> dict[str, Any]:
    requirements = list((await session.scalars(select(Requirement))).all())
    attempts = list((await session.scalars(select(Attempt))).all())
    decisions = list((await session.scalars(select(Decision).order_by(Decision.id))).all())
    estimates = list((await session.scalars(select(Estimate).order_by(Estimate.id))).all())
    worked = await hours_by_requirement(session)

    latest_estimate = {e.requirement_id: e.hours_total for e in estimates}
    cost: dict[int, float] = defaultdict(float)
    for attempt in attempts:
        cost[attempt.requirement_id] += attempt.cost_usd

    closed_at = {
        d.requirement_id: d.created_at
        for d in decisions
        if d.stage == "uat" and d.outcome == "approve"
    }
    lead_times = [_days(r.created_at, closed_at[r.id]) for r in requirements if r.id in closed_at]

    return {
        "total": len(requirements),
        "by_stage": dict(Counter(r.stage for r in requirements)),
        "by_state": dict(Counter(str(r.state) for r in requirements)),
        "blocked": [
            {"id": r.id, "title": r.title, "stage": r.stage}
            for r in requirements
            if r.state == RunState.BLOCKED
        ],
        "waiting": [
            {"id": r.id, "title": r.title, "stage": r.stage}
            for r in requirements
            if r.state == RunState.WAITING_GATE
        ],
        "lead_time_days": round(sum(lead_times) / len(lead_times), 2) if lead_times else None,
        "first_pass_rate": first_pass_rates(attempts),
        "ai_cost_usd": round(sum(cost.values()), 4),
        "requirements": [
            {
                "id": r.id,
                "title": r.title,
                "stage": r.stage,
                "estimated_hours": latest_estimate.get(r.id),
                "worked_hours": round(worked.get(r.id, 0.0), 2),
                "ai_cost_usd": round(cost.get(r.id, 0.0), 4),
            }
            for r in sorted(requirements, key=lambda r: r.id)
        ],
    }
