from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from fabrica.api.deps import Who
from fabrica.db.models import Message, MessageKind, Requirement, RunState
from fabrica.db.session import session_scope
from fabrica.domain.schemas import MessageOut, RequirementOut

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


class InboxOut(BaseModel):
    assigned: list[RequirementOut]
    pool: list[RequirementOut]
    waiting_gates: list[RequirementOut]
    open_questions: list[MessageOut]


@router.get("", response_model=InboxOut)
async def get_inbox(who: Who) -> InboxOut:
    async with session_scope() as s:
        req_rows = await s.scalars(select(Requirement).order_by(Requirement.id.desc()))
        all_reqs = list(req_rows.all())

        assigned = [
            RequirementOut.model_validate(r) for r in all_reqs if r.holder_user == who.user
        ]
        pool = [
            RequirementOut.model_validate(r)
            for r in all_reqs
            if not r.holder_user and r.state != RunState.DONE
        ]
        waiting_gates = [
            RequirementOut.model_validate(r)
            for r in all_reqs
            if r.state == RunState.WAITING_GATE
        ]

        q_rows = await s.scalars(
            select(Message)
            .where(Message.kind == MessageKind.PREGUNTA, Message.resolved.is_(False))
            .order_by(Message.id.desc())
        )
        open_questions = [MessageOut.model_validate(m) for m in q_rows.all()]

        return InboxOut(
            assigned=assigned,
            pool=pool,
            waiting_gates=waiting_gates,
            open_questions=open_questions,
        )
