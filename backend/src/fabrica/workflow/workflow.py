"""Flujo de Temporal por requisito: duerme hasta recibir `kick` y ejecuta el motor.

Las decisiones humanas se guardan en la base de datos (fuente de verdad) y
luego envían `kick`; el flujo nunca decide etapas por su cuenta.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from fabrica.workflow.activities import drive_activity

_IDLE_LIMIT = timedelta(days=30)


@workflow.defn
class RequirementWorkflow:
    def __init__(self) -> None:
        self._pending = 0

    @workflow.signal
    def kick(self) -> None:
        self._pending += 1

    @workflow.run
    async def run(self, req_id: int) -> str:
        while True:
            try:
                await workflow.wait_condition(lambda: self._pending > 0, timeout=_IDLE_LIMIT)
            except TimeoutError:
                return "inactivo"  # el próximo kick lo vuelve a iniciar
            self._pending = 0
            finished = await workflow.execute_activity(
                drive_activity,
                req_id,
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if finished:
                return "terminado"
