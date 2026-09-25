"""Flujo real en Temporal. Se omite si no se puede iniciar el servidor de desarrollo."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from fabrica.blackboard.service import Board
from fabrica.config import get_settings
from fabrica.db.session import session_scope
from fabrica.domain.schemas import DocumentIn, Identity, RequirementIn
from fabrica.pipeline import commands
from fabrica.pipeline.runner import TemporalRunner
from fabrica.workflow.activities import drive_activity
from fabrica.workflow.workflow import RequirementWorkflow


async def test_workflow_runs_until_client_gate() -> None:
    try:
        env = await WorkflowEnvironment.start_local()
    except RuntimeError as exc:
        pytest.skip(f"Temporal dev server no disponible: {exc}")

    queue = f"test-{uuid.uuid4()}"
    get_settings().temporal_queue = queue
    async with (
        env,
        Worker(
            env.client,
            task_queue=queue,
            workflows=[RequirementWorkflow],
            activities=[drive_activity],
        ),
    ):
        data = RequirementIn(
            title="Reporte de ventas",
            description="Ventas por cliente y material.",
            documents=[DocumentIn(name="spec", content="Campos: cliente, material, importe.")],
        )
        async with session_scope() as s:
            req = await commands.create_requirement(
                Board(s), data, Identity(user="ana", role="funcional")
            )
        await TemporalRunner(env.client).kick(req.id)

        for _ in range(100):
            async with session_scope() as s:
                current = await Board(s).requirement(req.id)
            if current.stage == "aprobacion_cliente":
                break
            await asyncio.sleep(0.1)
        assert current.stage == "aprobacion_cliente"
