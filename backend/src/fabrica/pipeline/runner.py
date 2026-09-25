from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy

from fabrica.config import get_settings
from fabrica.pipeline.engine import Engine

log = logging.getLogger(__name__)


class Runner(Protocol):
    async def kick(self, req_id: int) -> None: ...


class InlineRunner:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or Engine()
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def _run(self, req_id: int) -> None:
        lock = self._locks.setdefault(req_id, asyncio.Lock())
        async with lock:
            try:
                await self.engine.drive(req_id)
            except Exception:
                log.exception("Motor falló para el requisito %s", req_id)

    async def kick(self, req_id: int) -> None:
        task = asyncio.create_task(self._run(req_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks))


class TemporalRunner:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client

    async def _connect(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(get_settings().temporal_host)
        return self._client

    async def kick(self, req_id: int) -> None:
        from fabrica.workflow.workflow import RequirementWorkflow

        client = await self._connect()
        await client.start_workflow(
            RequirementWorkflow.run,
            req_id,
            id=f"requisito-{req_id}",
            task_queue=get_settings().temporal_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            start_signal="kick",
        )


def build_runner() -> Runner:
    return TemporalRunner() if get_settings().runner == "temporal" else InlineRunner()
