from datetime import datetime
from typing import Any

from fabrica.blackboard.service import Board
from fabrica.catalog import stage_machine
from fabrica.db.models import Decision, Document, Estimate, MessageKind, Requirement, RunState
from fabrica.domain.schemas import DecisionIn, Identity, RequirementIn
from fabrica.domain.stages import decide

STANDARD_SIZES: dict[str, dict[str, Any]] = {
    "tiny": {
        "complexity": "XS",
        "hours_total": 8.0,
        "breakdown": {"backend": 4.0, "frontend": 2.0, "pruebas": 1.0, "uat": 1.0},
    },
    "small": {
        "complexity": "S",
        "hours_total": 24.0,
        "breakdown": {"backend": 12.0, "frontend": 4.0, "pruebas": 4.0, "uat": 4.0},
    },
    "medium": {
        "complexity": "M",
        "hours_total": 40.0,
        "breakdown": {"backend": 20.0, "frontend": 8.0, "pruebas": 6.0, "uat": 6.0},
    },
    "large": {
        "complexity": "L",
        "hours_total": 80.0,
        "breakdown": {"backend": 40.0, "frontend": 16.0, "pruebas": 12.0, "uat": 12.0},
    },
    "very_large": {
        "complexity": "XL",
        "hours_total": 120.0,
        "breakdown": {"backend": 60.0, "frontend": 24.0, "pruebas": 18.0, "uat": 18.0},
    },
}


async def create_requirement(board: Board, data: RequirementIn, who: Identity) -> Requirement:
    first = stage_machine().first
    req = Requirement(
        title=data.title,
        description=data.description,
        project=data.project,
        capability=data.capability,
        ricefw=data.ricefw,
        priority=data.priority,
        due_date=data.due_date,
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


async def claim(board: Board, req_id: int, who: Identity) -> None:
    from fabrica.db.models import now

    req = await board.requirement(req_id)
    req.holder_role = who.role
    req.holder_user = who.user
    req.holder_since = now()
    await board.post(
        req.id,
        thread="asignacion",
        sender=who.user,
        kind=MessageKind.INFO,
        body=f"{who.user} tomó el requisito del pool ({who.role})",
    )


async def transfer(board: Board, req_id: int, target_user: str, who: Identity) -> None:
    from fabrica.db.models import now

    req = await board.requirement(req_id)
    req.holder_user = target_user
    req.holder_since = now()
    await board.post(
        req.id,
        thread="asignacion",
        sender=who.user,
        kind=MessageKind.INFO,
        body=f"{who.user} transfirió el requisito a {target_user}",
    )


async def release(board: Board, req_id: int, who: Identity) -> None:
    from fabrica.db.models import now

    req = await board.requirement(req_id)
    req.holder_user = None
    req.holder_since = now()
    await board.post(
        req.id,
        thread="asignacion",
        sender=who.user,
        kind=MessageKind.INFO,
        body=f"{who.user} devolvió el requisito al pool",
    )


async def update_priority(
    board: Board, req_id: int, priority: str, due_date: datetime | None, who: Identity
) -> None:
    req = await board.requirement(req_id)
    req.priority = priority
    req.due_date = due_date
    msg = f"Prioridad actualizada a {priority}"
    if due_date:
        msg += f", fecha límite: {due_date.strftime('%Y-%m-%d')}"
    await board.post(req.id, thread="flujo", sender=who.user, kind=MessageKind.INFO, body=msg)
    await board.s.flush()


async def update_size(board: Board, req_id: int, size: str, who: Identity) -> Estimate:
    cfg = STANDARD_SIZES.get(size.lower())
    if not cfg:
        raise ValueError(f"Talla no reconocida: {size}")
    req = await board.requirement(req_id)
    estimate = Estimate(
        requirement_id=req.id,
        complexity=cfg["complexity"],
        hours_base=cfg["hours_total"],
        hours_total=cfg["hours_total"],
        days=round(cfg["hours_total"] / 8.0, 1),
        breakdown=cfg["breakdown"],
        assumptions=[f"Ajuste de talla a {size.upper()} por {who.user}"],
        items=[{"object": req.title, "size": cfg["complexity"], "hours": cfg["hours_total"]}],
    )
    board.s.add(estimate)
    await board.post(
        req.id,
        thread="flujo",
        sender=who.user,
        kind=MessageKind.INFO,
        body=f"Talla ajustada a {size.upper()} ({cfg['hours_total']} h) por {who.user}",
    )
    await board.s.flush()
    return estimate
