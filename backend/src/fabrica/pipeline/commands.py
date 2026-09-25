from __future__ import annotations

from fabrica.blackboard.service import Board
from fabrica.catalog import stage_machine
from fabrica.db.models import Decision, Document, MessageKind, Requirement, RunState
from fabrica.domain.schemas import DecisionIn, Identity, RequirementIn
from fabrica.domain.stages import decide


async def create_requirement(board: Board, data: RequirementIn, who: Identity) -> Requirement:
    first = stage_machine().first
    req = Requirement(
        title=data.title,
        description=data.description,
        project=data.project,
        stage=first.key,
        state=RunState.RUNNING,
        created_by=who.user,
    )
    board.s.add(req)
    await board.s.flush()
    for doc in data.documents:
        board.s.add(
            Document(requirement_id=req.id, name=doc.name, kind=doc.kind, content=doc.content)
        )
    await board.post(
        req.id,
        thread="flujo",
        sender=who.user,
        kind=MessageKind.INFO,
        body=f"Requisito creado por {who.user} ({who.role})",
    )
    await board.s.flush()
    return req


async def answer_question(
    board: Board, req_id: int, message_id: int, body: str, who: Identity
) -> bool:
    await board.answer(req_id, message_id, who.user, body)
    req = await board.requirement(req_id)
    if req.state == RunState.BLOCKED and not await board.open_questions(req_id):
        req.state = RunState.RUNNING
        return True
    return False


async def record_decision(board: Board, req_id: int, data: DecisionIn, who: Identity) -> bool:
    req = await board.requirement(req_id)
    transition = decide(req.stage, data.outcome, who.role)
    await board.record_decision(
        Decision(
            requirement_id=req_id,
            stage=req.stage,
            outcome=data.outcome,
            actor=who.user,
            role=who.role,
            comment=data.comment,
        )
    )
    await board.post(
        req_id,
        thread="flujo",
        sender=who.user,
        kind=MessageKind.DECISION,
        body=f"{data.outcome} en {stage_machine().get(req.stage).label}: {data.comment}".strip(
            ": "
        ),
    )
    req.stage, req.state = transition.stage, transition.state
    return transition.state == RunState.RUNNING


async def resume(board: Board, req_id: int, who: Identity) -> bool:
    req = await board.requirement(req_id)
    if req.state != RunState.BLOCKED:
        return False
    if await board.open_questions(req_id):
        raise ValueError("Aún hay preguntas sin responder")
    req.state = RunState.RUNNING
    await board.post(
        req_id,
        thread="flujo",
        sender=who.user,
        kind=MessageKind.INFO,
        body="Etapa reanudada manualmente",
    )
    return True
