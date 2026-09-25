from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fabrica.db.models import (
    Artifact,
    Attempt,
    Decision,
    Estimate,
    Message,
    MessageKind,
    Requirement,
    SapCall,
    Transport,
)


class Board:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def requirement(self, req_id: int) -> Requirement:
        req = await self.s.get(Requirement, req_id, options=[selectinload(Requirement.documents)])
        if req is None:
            raise LookupError(f"Requisito {req_id} no existe")
        return req

    async def post(
        self,
        req_id: int,
        *,
        thread: str,
        sender: str,
        kind: MessageKind,
        body: str,
        recipient: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Message:
        msg = Message(
            requirement_id=req_id,
            thread=thread,
            sender=sender,
            recipient=recipient,
            kind=kind,
            body=body,
            data=data or {},
        )
        self.s.add(msg)
        await self.s.flush()
        return msg

    async def messages(self, req_id: int, thread: str | None = None) -> list[Message]:
        q = select(Message).where(Message.requirement_id == req_id)
        if thread:
            q = q.where(Message.thread == thread)
        return list((await self.s.scalars(q.order_by(Message.id))).all())

    async def open_questions(self, req_id: int) -> list[Message]:
        q = select(Message).where(
            Message.requirement_id == req_id,
            Message.kind == MessageKind.PREGUNTA,
            Message.resolved.is_(False),
        )
        return list((await self.s.scalars(q)).all())

    async def answer(self, req_id: int, message_id: int, author: str, body: str) -> Message:
        question = await self.s.get(Message, message_id)
        if question is None or question.requirement_id != req_id:
            raise LookupError("Pregunta no encontrada")
        question.resolved = True
        return await self.post(
            req_id,
            thread=question.thread,
            sender=author,
            recipient=question.sender,
            kind=MessageKind.RESPUESTA,
            body=body,
            data={"reply_to": message_id},
        )

    async def answers(self, req_id: int) -> list[str]:
        q = select(Message.body).where(
            Message.requirement_id == req_id, Message.kind == MessageKind.RESPUESTA
        )
        return list((await self.s.scalars(q)).all())

    async def record_attempt(self, attempt: Attempt) -> None:
        self.s.add(attempt)
        req = await self.requirement(attempt.requirement_id)
        req.spent_usd = round(req.spent_usd + attempt.cost_usd, 6)
        await self.s.flush()

    async def attempts(self, req_id: int) -> list[Attempt]:
        q = select(Attempt).where(Attempt.requirement_id == req_id).order_by(Attempt.id)
        return list((await self.s.scalars(q)).all())

    async def record_decision(self, decision: Decision) -> None:
        self.s.add(decision)
        await self.s.flush()

    async def decisions(self, req_id: int) -> list[Decision]:
        q = select(Decision).where(Decision.requirement_id == req_id).order_by(Decision.id)
        return list((await self.s.scalars(q)).all())

    async def record_artifact(self, req_id: int, path: str, commit: str, author: str) -> None:
        self.s.add(Artifact(requirement_id=req_id, path=path, commit=commit, author=author))
        await self.s.flush()

    async def artifacts(self, req_id: int) -> list[Artifact]:
        q = select(Artifact).where(Artifact.requirement_id == req_id).order_by(Artifact.id)
        return list((await self.s.scalars(q)).all())

    async def audit_sap(self, call: SapCall) -> None:
        self.s.add(call)
        await self.s.flush()

    async def transport(self, req_id: int, system: str) -> Transport | None:
        q = select(Transport).where(Transport.requirement_id == req_id, Transport.system == system)
        return (await self.s.scalars(q)).first()

    async def add_transport(self, req_id: int, system: str, number: str) -> Transport:
        transport = Transport(requirement_id=req_id, system=system, number=number, objects=[])
        self.s.add(transport)
        await self.s.flush()
        return transport

    async def transports(self, req_id: int) -> list[Transport]:
        q = select(Transport).where(Transport.requirement_id == req_id).order_by(Transport.id)
        return list((await self.s.scalars(q)).all())

    async def latest_estimate(self, req_id: int) -> Estimate | None:
        q = select(Estimate).where(Estimate.requirement_id == req_id).order_by(Estimate.id.desc())
        return (await self.s.scalars(q)).first()

    async def sap_calls(self, req_id: int, limit: int = 200) -> list[SapCall]:
        q = (
            select(SapCall)
            .where(SapCall.requirement_id == req_id)
            .order_by(SapCall.id.desc())
            .limit(limit)
        )
        return list(reversed((await self.s.scalars(q)).all()))
