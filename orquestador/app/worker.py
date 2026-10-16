"""Worker de Temporal: registra el FactoryWorkflow y sus activities y los
ejecuta. Reintenta la conexion porque Temporal puede tardar en arrancar.
"""
from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

import config
from activities import (
    agent_stage,
    blog_comment,
    fetch_inputs,
    gitea_commit,
    load_architecture,
    load_pipeline,
    load_skill,
    provisionar_repo,
    proyectar_requisito,
    proyectar_transicion,
    save_draft,
    save_iteration,       # deprecated, mantenido por compat con workflows en curso
    save_open_questions,
    save_timeline,
    snapshot_inputs,
    snapshot_version,
)
from workflow import FactoryWorkflow

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orquestador.worker")


async def connect_with_retry(attempts: int = 40, delay: float = 3.0) -> Client:
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await Client.connect(config.TEMPORAL_ADDRESS,
                                        namespace=config.TEMPORAL_NAMESPACE)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.info("Temporal aun no responde (%s); reintento %d/%d",
                     config.TEMPORAL_ADDRESS, i, attempts)
            await asyncio.sleep(delay)
    raise RuntimeError(f"No se pudo conectar a Temporal: {last}")


async def main() -> None:
    client = await connect_with_retry()
    log.info("Conectado a Temporal. Task queue: %s", config.TASK_QUEUE)
    worker = Worker(
        client,
        task_queue=config.TASK_QUEUE,
        workflows=[FactoryWorkflow],
        activities=[
            agent_stage,
            gitea_commit, fetch_inputs, load_architecture, load_pipeline,
            load_skill, provisionar_repo,
            snapshot_inputs, save_open_questions,
            save_draft, snapshot_version, save_timeline,
            proyectar_requisito, proyectar_transicion, blog_comment,
            save_iteration,   # deprecated, no se invoca desde el workflow nuevo
        ],
    )
    log.info("Worker iniciado; esperando tareas.")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
