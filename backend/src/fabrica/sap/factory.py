from __future__ import annotations

from dataclasses import dataclass

from fabrica.blackboard.service import Board
from fabrica.config import get_settings
from fabrica.db.models import SapCall
from fabrica.sap.adt_bridge import AdtSap
from fabrica.sap.bridge import GuardedBridge, SapBridge, SapObject
from fabrica.sap.systems import SapSystem, sap_landscape

_ADT_SESSIONS: dict[str, SapBridge] = {}


def connect(system: SapSystem) -> SapBridge:
    if system.name not in _ADT_SESSIONS:
        if get_settings().sap_mode == "mock":
            from fabrica.sap.simulated import SimulatedSapSystem

            _ADT_SESSIONS[system.name] = SimulatedSapSystem(
                get_settings().data_dir / "sap_mock", system.name
            )
        else:
            _ADT_SESSIONS[system.name] = AdtSap(system)
    return _ADT_SESSIONS[system.name]


@dataclass
class RequirementSap:
    bridge: GuardedBridge
    board: Board
    req_id: int
    title: str

    async def transport_for(self, obj: SapObject) -> str:
        existing = await self.board.transport(self.req_id, self.bridge.system)
        if existing is not None and not await self.bridge.transport_is_open(existing.number):
            existing.status = "liberada"
            existing = None
        text = f"Fabrica #{self.req_id} {self.title}"
        number = await self.bridge.ensure_transport(
            obj, text, existing.number if existing else None
        )
        if existing is None:
            existing = await self.board.add_transport(self.req_id, self.bridge.system, number)
        if obj.name.upper() not in existing.objects:
            existing.objects = [*existing.objects, obj.name.upper()]
        return number

    async def write(self, obj: SapObject) -> str:
        number = await self.transport_for(obj)
        await self.bridge.write_object(obj, number)
        return number


async def sap_for(board: Board, req_id: int, actor: str) -> RequirementSap:
    req = await board.requirement(req_id)
    system = sap_landscape().for_project(req.project)
    inner = connect(system)

    async def audit(tool: str, obj: str, ok: bool, detail: str) -> None:
        await board.audit_sap(
            SapCall(
                requirement_id=req_id,
                system=system.name,
                tool=tool,
                object_name=obj,
                actor=actor,
                ok=ok,
                detail=detail[:2000],
            )
        )

    bridge = GuardedBridge(inner, get_settings().sap_allowed_packages, audit)
    return RequirementSap(bridge, board, req_id, req.title)
