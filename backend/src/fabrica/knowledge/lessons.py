from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fabrica.db.models import Lesson, Requirement, RunState

_STOPWORDS = frozenset(
    [
        "de",
        "la",
        "el",
        "los",
        "las",
        "un",
        "una",
        "y",
        "o",
        "en",
        "con",
        "por",
        "para",
        "del",
        "al",
        "se",
        "que",
        "no",
        "es",
        "su",
        "sus",
        "lo",
        "como",
        "mas",
    ]
)


def keywords(text: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9_*]{3,}", plain) if w not in _STOPWORDS}


def _score(query: set[str], text: str) -> int:
    return len(query & keywords(text))


async def record_lessons(
    session: AsyncSession,
    *,
    activity: str,
    issues: list[str],
    requirement_id: int,
    capability: str | None,
    source: str = "escalamiento",
) -> list[Lesson]:
    existing = set(
        (await session.scalars(select(Lesson.text).where(Lesson.activity == activity))).all()
    )
    created = []
    for issue in dict.fromkeys(issues):
        text = f"Evitar: {issue}"
        if text in existing:
            continue
        lesson = Lesson(
            activity=activity,
            capability=capability,
            text=text,
            source=source,
            requirement_id=requirement_id,
        )
        session.add(lesson)
        created.append(lesson)
    await session.flush()
    return created


async def relevant_lessons(
    session: AsyncSession, activity: str, context: str, *, limit: int = 5
) -> list[Lesson]:
    candidates = (await session.scalars(select(Lesson).where(Lesson.activity == activity))).all()
    query = keywords(context)
    ranked = sorted(
        candidates,
        key=lambda lesson: (_score(query, lesson.text), lesson.uses, lesson.id),
        reverse=True,
    )
    return ranked[:limit]


def render_lessons(lessons: list[Lesson]) -> str:
    if not lessons:
        return ""
    items = "\n".join(f"- {lesson.text}" for lesson in lessons)
    return f"\n\n## LECCIONES APRENDIDAS EN REQUISITOS ANTERIORES\n{items}\n"


async def similar_requirements(
    session: AsyncSession, text: str, *, exclude: int | None = None, limit: int = 5
) -> list[Requirement]:
    closed = (
        await session.scalars(select(Requirement).where(Requirement.state == RunState.DONE))
    ).all()
    query = keywords(text)
    scored = [
        (score, r)
        for r in closed
        if r.id != exclude and (score := _score(query, f"{r.title} {r.description}")) > 0
    ]
    return [r for _, r in sorted(scored, key=lambda x: (x[0], x[1].id), reverse=True)[:limit]]
