from pathlib import Path

import pytest

from fabrica.sap.bridge import GuardedBridge, PolicyViolation, SapObject
from fabrica.sap.simulated import SimulatedSap

calls: list[tuple[str, str, bool]] = []


async def _audit(tool: str, obj: str, ok: bool, detail: str) -> None:
    calls.append((tool, obj, ok))


def _bridge(tmp_path: Path, system: str = "SIM-DEV") -> GuardedBridge:
    return GuardedBridge(SimulatedSap(tmp_path, system), ("Z", "Y"), _audit)


async def test_writes_only_customer_packages(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    with pytest.raises(PolicyViolation):
        await bridge.write_object(SapObject("RFBILA00", "PROG", "FBAS", "REPORT x."), "T1")
    assert calls[-1] == ("write_object", "RFBILA00", False)


async def test_never_writes_outside_dev(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, system="SIM-QAS")
    with pytest.raises(PolicyViolation):
        await bridge.write_object(SapObject("ZTEST", "PROG", "ZFAB", "REPORT ztest."), "T1")


async def test_atc_flags_select_star(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    source = "REPORT zx.\nSELECT * FROM bkpf INTO TABLE @DATA(lt).\n"
    await bridge.write_object(SapObject("ZX", "PROG", "ZFAB", source), "T1")
    assert (await bridge.syntax_check("ZX")).ok
    atc = await bridge.run_atc("ZX")
    assert not atc.ok
    assert "SELECT *" in atc.findings[0].message


async def test_mcp_blocks_and_audits_forbidden_write() -> None:
    from sqlalchemy import select

    from fabrica.blackboard.service import Board
    from fabrica.db.models import SapCall
    from fabrica.db.session import session_scope
    from fabrica.domain.schemas import Identity, RequirementIn
    from fabrica.mcp_servers.sap import server
    from fabrica.pipeline.commands import create_requirement

    async with session_scope() as s:
        data = RequirementIn(title="Reporte", description="Reporte de prueba de política.")
        await create_requirement(Board(s), data, Identity(user="ana", role="funcional"))
    args = {
        "requisito_id": 1,
        "nombre": "RFBILA00",
        "tipo": "PROG",
        "paquete": "SAP",
        "fuente": "REPORT rfbila00.",
    }
    result = await server.call_tool("escribir_objeto", args)
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    async with session_scope() as s:
        audit = (await s.scalars(select(SapCall))).all()
    assert [(a.tool, a.ok) for a in audit] == [("create_transport", False)]
