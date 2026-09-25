from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from fabrica.blackboard.service import Board
from fabrica.db.models import Requirement, RunState, now
from fabrica.db.session import session_scope
from fabrica.domain.schemas import Identity, RequirementIn
from fabrica.pipeline import engine as engine_module
from fabrica.pipeline.commands import create_requirement
from fabrica.pipeline.engine import Engine
from fabrica.pipeline.steps import StepResult


async def _new_requirement() -> tuple[int, str]:
    async with session_scope() as s:
        data = RequirementIn(title="Reporte", description="Reporte de facturas por fecha.")
        req = await create_requirement(Board(s), data, Identity(user="ana", role="funcional"))
        return req.id, req.stage


async def _state(req_id: int) -> tuple[str, str, Any]:
    async with session_scope() as s:
        req = await Board(s).requirement(req_id)
        return req.stage, req.state, req.lease_until


async def test_result_is_discarded_when_requirement_changed_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req_id, stage = await _new_requirement()

    async def work_while_someone_blocks(self: Engine, rid: int) -> StepResult:
        async with session_scope() as s:
            await s.execute(
                update(Requirement).where(Requirement.id == rid).values(state=RunState.BLOCKED)
            )
        return StepResult("advance", "")

    monkeypatch.setattr(Engine, "_work", work_while_someone_blocks)
    assert await Engine().run_stage(req_id) is False
    assert await _state(req_id) == (stage, RunState.BLOCKED, None)
    async with session_scope() as s:
        bodies = [m.body for m in await Board(s).messages(req_id)]
    assert any("descartado" in b for b in bodies)


async def test_leased_requirement_is_not_run_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    req_id, stage = await _new_requirement()
    async with session_scope() as s:
        await s.execute(
            update(Requirement)
            .where(Requirement.id == req_id)
            .values(lease_until=now() + timedelta(minutes=5))
        )
    worked: list[int] = []

    async def work(self: Engine, rid: int) -> StepResult:
        worked.append(rid)
        return StepResult("advance", "")

    monkeypatch.setattr(Engine, "_work", work)
    assert await Engine().run_stage(req_id) is False
    assert worked == []

    async with session_scope() as s:
        await s.execute(
            update(Requirement)
            .where(Requirement.id == req_id)
            .values(lease_until=now() - engine_module.LEASE)
        )
    await Engine().run_stage(req_id)
    assert worked == [req_id]
    next_stage, _, lease = await _state(req_id)
    assert next_stage != stage and lease is None


async def test_step_exception_blocks_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    req_id, stage = await _new_requirement()

    async def explode(self: Any, req: Requirement) -> StepResult:
        raise RuntimeError("sin conexión")

    monkeypatch.setattr(engine_module.Steps, stage, explode)
    assert await Engine().run_stage(req_id) is False
    assert (await _state(req_id))[:2] == (stage, RunState.BLOCKED)
    async with session_scope() as s:
        bodies = [m.body for m in await Board(s).messages(req_id)]
    assert any("RuntimeError: sin conexión" in b for b in bodies)
