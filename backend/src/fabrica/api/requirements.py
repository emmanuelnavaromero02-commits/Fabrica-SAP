"""Rutas de requisitos: crear, listar, detalle, preguntas, decisiones y reanudar.

Regla: se hace commit de la transacción ANTES de disparar el motor (`kick`),
para que el motor siempre lea el estado ya guardado.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from fabrica.api.deps import RunnerDep, Who
from fabrica.blackboard.service import Board
from fabrica.db.models import Requirement
from fabrica.db.session import session_scope
from fabrica.domain.schemas import (
    AnswerIn,
    ArtifactOut,
    AttemptOut,
    DecisionIn,
    DecisionOut,
    MessageOut,
    RequirementDetail,
    RequirementIn,
    RequirementOut,
)
from fabrica.domain.stages import TransitionError
from fabrica.pipeline import commands

router = APIRouter(prefix="/api/requirements", tags=["requisitos"])


@router.get("", response_model=list[RequirementOut])
async def list_requirements(who: Who) -> list[Requirement]:
    async with session_scope() as s:
        rows = await s.scalars(select(Requirement).order_by(Requirement.id.desc()))
        return list(rows.all())


@router.post("", response_model=RequirementOut, status_code=201)
async def create(data: RequirementIn, who: Who, runner: RunnerDep) -> Requirement:
    async with session_scope() as s:
        req = await commands.create_requirement(Board(s), data, who)
    await runner.kick(req.id)
    return req


@router.get("/{req_id}", response_model=RequirementDetail)
async def detail(req_id: int, who: Who) -> RequirementDetail:
    async with session_scope() as s:
        board = Board(s)
        try:
            req = await board.requirement(req_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return RequirementDetail(
            requirement=RequirementOut.model_validate(req),
            messages=[MessageOut.model_validate(m) for m in await board.messages(req_id)],
            attempts=[AttemptOut.model_validate(a) for a in await board.attempts(req_id)],
            decisions=[DecisionOut.model_validate(d) for d in await board.decisions(req_id)],
            artifacts=[ArtifactOut.model_validate(a) for a in await board.artifacts(req_id)],
        )


@router.post("/{req_id}/answers", status_code=204)
async def answer(req_id: int, data: AnswerIn, who: Who, runner: RunnerDep) -> None:
    try:
        async with session_scope() as s:
            unblocked = await commands.answer_question(
                Board(s), req_id, data.message_id, data.body, who
            )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if unblocked:
        await runner.kick(req_id)


@router.post("/{req_id}/decisions", status_code=204)
async def decision(req_id: int, data: DecisionIn, who: Who, runner: RunnerDep) -> None:
    try:
        async with session_scope() as s:
            has_work = await commands.record_decision(Board(s), req_id, data, who)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except TransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    if has_work:
        await runner.kick(req_id)


@router.post("/{req_id}/resume", status_code=204)
async def resume(req_id: int, who: Who, runner: RunnerDep) -> None:
    try:
        async with session_scope() as s:
            resumed = await commands.resume(Board(s), req_id, who)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if resumed:
        await runner.kick(req_id)
