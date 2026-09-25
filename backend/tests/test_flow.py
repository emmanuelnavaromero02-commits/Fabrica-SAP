from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from fabrica.api.app import create_app
from fabrica.pipeline.runner import InlineRunner


def who(user: str, role: str) -> dict[str, str]:
    return {"X-Fabrica-User": user, "X-Fabrica-Role": role}


@pytest.fixture
async def api() -> Any:
    app = create_app()
    runner = InlineRunner()
    app.state.runner = runner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner


async def _detail(client: httpx.AsyncClient, req_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/requirements/{req_id}", headers=who("ana", "funcional"))
    assert resp.status_code == 200
    data: dict[str, Any] = resp.json()
    return data


async def test_full_factory_flow(api: Any, isolated: Path) -> None:
    client, runner = api
    body = {
        "title": "Reporte de facturas contabilizadas",
        "description": "Listar documentos contables por fecha de contabilización.",
    }
    resp = await client.post("/api/requirements", json=body, headers=who("ana", "funcional"))
    assert resp.status_code == 201
    req_id = resp.json()["id"]
    await runner.wait_idle()

    d = await _detail(client, req_id)
    assert (d["requirement"]["stage"], d["requirement"]["state"]) == ("recepcion", "blocked")
    question = next(m for m in d["messages"] if m["kind"] == "pregunta")

    await client.post(
        f"/api/requirements/{req_id}/answers",
        json={
            "message_id": question["id"],
            "body": "Sociedad, documento y fecha; filtro por fecha.",
        },
        headers=who("ana", "funcional"),
    )
    await runner.wait_idle()
    d = await _detail(client, req_id)
    assert d["requirement"]["stage"] == "aprobacion_cliente"
    assert d["requirement"]["capability"] == "FI"
    estimate = d["estimate"]
    assert estimate["items"][0]["size"] == "M" and estimate["complexity"] == "M"
    assert estimate["hours_total"] == round((40 + 24) * 1.15, 2)

    resp = await client.post(
        f"/api/requirements/{req_id}/decisions",
        json={"outcome": "approve"},
        headers=who("ana", "funcional"),
    )
    assert resp.status_code == 204
    await runner.wait_idle()
    d = await _detail(client, req_id)
    assert d["requirement"]["stage"] == "revision_abap"
    impl = [a for a in d["attempts"] if a["activity"] == "implementar"]
    assert [a["tier"] for a in impl] == ["N2", "N2", "N3"]
    assert [a["passed"] for a in impl] == [False, False, True]
    assert any("SELECT *" in issue for issue in impl[0]["issues"])
    review = next(a for a in d["attempts"] if a["activity"] == "revisar")
    assert review["provider"] != impl[-1]["provider"]
    assert any(m["kind"] == "escalamiento" for m in d["messages"])

    paths = {a["path"] for a in d["artifacts"]}
    assert {"diseno/spec.md", "evidencia/construccion.json"} <= paths
    assert any(p.endswith(".prog.abap") for p in paths)
    assert (isolated / "repos" / f"req-{req_id}" / ".git").exists()
    [transport] = d["transports"]
    assert transport["system"] == "SIM-DEV" and transport["number"].startswith("SIMK9")
    assert transport["objects"] == ["Z_REPORTE_DE_FACTURAS"]

    resp = await client.post(
        f"/api/requirements/{req_id}/decisions",
        json={"outcome": "approve"},
        headers=who("ana", "funcional"),
    )
    assert resp.status_code == 409
    for user, role in (("luis", "abap"), ("carla", "usuario_clave")):
        resp = await client.post(
            f"/api/requirements/{req_id}/decisions",
            json={"outcome": "approve"},
            headers=who(user, role),
        )
        assert resp.status_code == 204
    d = await _detail(client, req_id)
    assert (d["requirement"]["stage"], d["requirement"]["state"]) == ("cerrado", "done")
    assert [x["actor"] for x in d["decisions"]] == ["ana", "luis", "carla"]
    assert d["requirement"]["spent_usd"] >= 0


async def test_requires_identity(api: Any) -> None:
    client, _ = api
    resp = await client.get("/api/requirements")
    assert resp.status_code == 401


async def test_usage_report(api: Any) -> None:
    client, runner = api
    body = {
        "title": "Reporte de ventas",
        "description": "Ventas por cliente y material.",
        "documents": [{"name": "spec", "content": "Campos: cliente, material, importe."}],
    }
    await client.post("/api/requirements", json=body, headers=who("ana", "funcional"))
    await runner.wait_idle()
    rows = (await client.get("/api/usage", headers=who("ana", "admin"))).json()
    assert {r["activity"] for r in rows} >= {"clasificar", "analizar", "disenar_spec"}
