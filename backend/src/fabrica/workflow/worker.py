from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from fabrica.config import get_settings
from fabrica.db.session import init_db
from fabrica.workflow.activities import drive_activity
from fabrica.workflow.workflow import RequirementWorkflow


async def main() -> None:
    settings = get_settings()
    await init_db()
    client = await Client.connect(settings.temporal_host)
    worker = Worker(
        client,
        task_queue=settings.temporal_queue,
        workflows=[RequirementWorkflow],
        activities=[drive_activity],
    )
    logging.info(
        "Worker escuchando la cola %s en %s", settings.temporal_queue, settings.temporal_host
    )
    await worker.run()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
