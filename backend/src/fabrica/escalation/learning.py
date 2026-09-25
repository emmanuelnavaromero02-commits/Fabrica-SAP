from __future__ import annotations

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fabrica.catalog import ActivityPolicy, LearningPolicy
from fabrica.db.models import Attempt


async def pass_rates(session: AsyncSession, activity: str) -> dict[str, tuple[int, int]]:
    rows = await session.execute(
        select(Attempt.tier, func.count(), func.sum(cast(Attempt.passed, Integer)))
        .where(Attempt.activity == activity)
        .group_by(Attempt.tier)
    )
    return {tier: (int(total), int(passed or 0)) for tier, total, passed in rows.all()}


def learned_tiers(
    policy: ActivityPolicy, learning: LearningPolicy, rates: dict[str, tuple[int, int]]
) -> list[str]:
    tiers = policy.tiers()
    while len(tiers) > 1:
        total, passed = rates.get(tiers[0], (0, 0))
        if total < learning.min_samples or passed / total >= learning.min_pass_rate:
            break
        tiers = tiers[1:]
    return tiers
