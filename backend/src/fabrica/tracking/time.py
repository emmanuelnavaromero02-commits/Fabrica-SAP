from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fabrica.db.models import WorkSession, now

IDLE_LIMIT = timedelta(minutes=5)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=now().tzinfo)


async def heartbeat(
    session: AsyncSession,
    *,
    user: str,
    requirement_id: int | None,
    source: str,
    at: datetime | None = None,
) -> WorkSession:
    moment = at or now()
    query = (
        select(WorkSession)
        .where(
            WorkSession.user == user,
            WorkSession.source == source,
            WorkSession.requirement_id.is_(None)
            if requirement_id is None
            else WorkSession.requirement_id == requirement_id,
        )
        .order_by(WorkSession.last_seen_at.desc())
    )
    current = (await session.scalars(query)).first()
    if current is not None:
        gap = moment - _aware(current.last_seen_at)
        if timedelta(0) <= gap <= IDLE_LIMIT:
            current.seconds += int(gap.total_seconds())
            current.last_seen_at = moment
            await session.flush()
            return current
    fresh = WorkSession(
        user=user,
        requirement_id=requirement_id,
        source=source,
        started_at=moment,
        last_seen_at=moment,
        seconds=0,
    )
    session.add(fresh)
    await session.flush()
    return fresh


async def hours_by_user_and_requirement(
    session: AsyncSession,
    *,
    user: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    query = select(WorkSession)
    if user:
        query = query.where(WorkSession.user == user)
    if since:
        query = query.where(WorkSession.last_seen_at >= since)
    if until:
        query = query.where(WorkSession.started_at <= until)
    totals: dict[tuple[str, int | None], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for work in (await session.scalars(query)).all():
        totals[(work.user, work.requirement_id)][work.source] += work.seconds
    return [
        {
            "user": u,
            "requirement_id": req,
            "hours": round(sum(by_source.values()) / 3600, 2),
            "by_source": {k: round(v / 3600, 2) for k, v in by_source.items()},
        }
        for (u, req), by_source in sorted(totals.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))
    ]


async def hours_by_requirement(session: AsyncSession) -> dict[int, float]:
    result: dict[int, float] = defaultdict(float)
    for row in await hours_by_user_and_requirement(session):
        if row["requirement_id"] is not None:
            result[row["requirement_id"]] += row["hours"]
    return dict(result)
