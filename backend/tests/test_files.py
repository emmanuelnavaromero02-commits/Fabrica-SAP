from __future__ import annotations

from typing import Any

import httpx
import pytest

from fabrica.api.app import create_app
from fabrica.pipeline.runner import InlineRunner
from fabrica.sap import factory
from fabrica.sap.systems import SapLandscape
from tests.helpers import build_until_abap_review, detail, who


@pytest.fixture
async def api() -> Any:
    app = create_app()
    runner = InlineRunner()
    app.state.runner = runner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner


async def test_file_viewer_reads_generated_artifacts_only_inside_repo(api: Any) -> None:
    client, runner = api
    built = await build_until_abap_review(client, runner, "Reporte de facturas")
    req_id = built["requirement"]["id"]
    code_path = next(a["path"] for a in built["artifacts"] if a["path"].endswith(".abap"))
    params = {"path": code_path}
    resp = await client.get(
        f"/api/requirements/{req_id}/file", params=params, headers=who("ana", "funcional")
    )
    assert resp.status_code == 200 and "REPORT" in resp.json()["content"]
    assert built["sap_calls"][0]["system"] == "PRUEBA-DEV"
    escape = await client.get(
        f"/api/requirements/{req_id}/file",
        params={"path": "../../test.db"},
        headers=who("ana", "funcional"),
    )
    assert escape.status_code == 404


async def test_construction_blocks_when_project_has_no_sap_system(
    api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, runner = api
    monkeypatch.setattr(factory, "sap_landscape", lambda: SapLandscape())
    built = await build_until_abap_review(client, runner, "Reporte de facturas")
    assert (built["requirement"]["stage"], built["requirement"]["state"]) == (
        "construccion",
        "blocked",
    )
    assert any("no tiene un sistema SAP DEV" in m["body"] for m in built["messages"])
    assert (await detail(client, built["requirement"]["id"]))["transports"] == []
