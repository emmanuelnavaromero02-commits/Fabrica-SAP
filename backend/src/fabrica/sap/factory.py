from __future__ import annotations

from fabrica.blackboard.service import Board
from fabrica.config import get_settings
from fabrica.db.models import SapCall
from fabrica.sap.bridge import GuardedBridge
from fabrica.sap.simulated import SimulatedSap


def bridge_for(board: Board, req_id: int, actor: str) -> GuardedBridge:
    settings = get_settings()
    inner = SimulatedSap(settings.data_dir / "sap")

    async def audit(tool: str, obj: str, ok: bool, detail: str) -> None:
        await board.audit_sap(
            SapCall(
                requirement_id=req_id,
                system=inner.system,
                tool=tool,
                object_name=obj,
                actor=actor,
                ok=ok,
                detail=detail[:2000],
            )
        )

    return GuardedBridge(inner, settings.sap_allowed_packages, audit)
