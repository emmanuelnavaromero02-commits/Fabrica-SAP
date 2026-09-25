from __future__ import annotations

import io
from typing import Any

import httpx
import openpyxl
import pytest

from fabrica.api.app import create_app
from fabrica.pipeline.runner import InlineRunner
from tests.helpers import who


@pytest.fixture
async def api() -> Any:
    runner = InlineRunner()
    app = create_app()
    app.state.runner = runner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner


async def test_clients_crud(api: Any) -> None:
    client, _ = api
    resp = await client.post(
        "/api/clients",
        json={"name": "Cliente Demo", "code": "CLIDEMO"},
        headers=who("admin", "admin"),
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["name"] == "Cliente Demo"
    assert created["code"] == "CLIDEMO"

    list_resp = await client.get("/api/clients", headers=who("admin", "admin"))
    assert list_resp.status_code == 200
    names = [c["name"] for c in list_resp.json()]
    assert "Cliente Demo" in names


async def test_projects_and_capabilities_crud(api: Any) -> None:
    client, _ = api
    c_resp = await client.post(
        "/api/clients",
        json={"name": "Retail Corp", "code": "RETAIL"},
        headers=who("admin", "admin"),
    )
    client_id = c_resp.json()["id"]

    p_resp = await client.post(
        "/api/projects",
        json={
            "client_id": client_id,
            "name": "RISE 2026",
            "code": "RETAIL-RISE",
            "sap_system": "RET-DEV",
        },
        headers=who("admin", "admin"),
    )
    assert p_resp.status_code == 201
    proj_id = p_resp.json()["id"]

    cap_resp = await client.post(
        f"/api/projects/{proj_id}/capabilities",
        json={"name": "Material Management", "code": "MM"},
        headers=who("admin", "admin"),
    )
    assert cap_resp.status_code == 201
    assert cap_resp.json()["code"] == "MM"

    caps_list = await client.get(
        f"/api/projects/{proj_id}/capabilities", headers=who("admin", "admin")
    )
    assert caps_list.status_code == 200
    assert len(caps_list.json()) == 1


async def test_requirement_claim_transfer_release(api: Any) -> None:
    client, runner = api
    create_resp = await client.post(
        "/api/requirements",
        json={"title": "Reporte Facturas", "description": "Reporte de prueba para asignacion."},
        headers=who("ana", "funcional"),
    )
    req_id = create_resp.json()["id"]

    claim_resp = await client.post(
        f"/api/requirements/{req_id}/claim", headers=who("carlos", "funcional")
    )
    assert claim_resp.status_code == 204

    detail_resp = await client.get(
        f"/api/requirements/{req_id}", headers=who("carlos", "funcional")
    )
    req_data = detail_resp.json()["requirement"]
    assert req_data["holder_user"] == "carlos"
    assert req_data["holder_role"] == "funcional"
    assert req_data["holder_since"] is not None

    transfer_resp = await client.post(
        f"/api/requirements/{req_id}/transfer",
        json={"user": "maria"},
        headers=who("carlos", "funcional"),
    )
    assert transfer_resp.status_code == 204

    detail_resp2 = await client.get(
        f"/api/requirements/{req_id}", headers=who("maria", "funcional")
    )
    assert detail_resp2.json()["requirement"]["holder_user"] == "maria"

    release_resp = await client.post(
        f"/api/requirements/{req_id}/release", headers=who("maria", "funcional")
    )
    assert release_resp.status_code == 204

    detail_resp3 = await client.get(
        f"/api/requirements/{req_id}", headers=who("maria", "funcional")
    )
    assert detail_resp3.json()["requirement"]["holder_user"] is None


async def test_billing_snapshot_and_excel_export(api: Any) -> None:
    client, runner = api
    project_code = "DEMO-PROJ"
    await client.post(
        "/api/requirements",
        json={
            "project": project_code,
            "title": "Interfaz Ventas",
            "description": "Interfaz de envio de facturas al SAT.",
        },
        headers=who("ana", "funcional"),
    )

    snap_resp = await client.post(
        f"/api/projects/{project_code}/snapshots",
        json={"project": project_code},
        headers=who("lider", "lider"),
    )
    assert snap_resp.status_code == 201
    snap = snap_resp.json()
    assert snap["project"] == project_code
    assert snap["created_by"] == "lider"

    list_snaps = await client.get(
        f"/api/projects/{project_code}/snapshots", headers=who("lider", "lider")
    )
    assert list_snaps.status_code == 200
    assert len(list_snaps.json()) == 1

    excel_resp = await client.get(
        f"/api/projects/{project_code}/export/billing", headers=who("lider", "lider")
    )
    assert excel_resp.status_code == 200
    assert "spreadsheetml.sheet" in excel_resp.headers["content-type"]

    wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
    assert set(wb.sheetnames) == {"Resumen", "Detalle Requisitos", "Delta Facturación"}


async def test_technical_dossier(api: Any) -> None:
    client, runner = api
    create_resp = await client.post(
        "/api/requirements",
        json={
            "project": "DEMO-DOSSIER",
            "title": "Reporte de Balance Comprobacion",
            "description": "Reporte financiero SAP con Clean Core.",
        },
        headers=who("ana", "funcional"),
    )
    req_id = create_resp.json()["id"]

    dossier_resp = await client.get(
        f"/api/requirements/{req_id}/dossier?user=ana&role=funcional"
    )
    assert dossier_resp.status_code == 200
    assert "text/html" in dossier_resp.headers["content-type"]
    html_text = dossier_resp.text
    assert "ACTA DE CONFORMIDAD Y ENTREGA TÉCNICA SAP" in html_text
    assert "Reporte de Balance Comprobacion" in html_text
    assert "Clean Core" in html_text
    assert "Sintaxis ABAP" in html_text
    assert "ABAP Test Cockpit (ATC)" in html_text
    assert "Pruebas Unitarias (AUnit)" in html_text
    assert "Consultor Funcional SAP" in html_text


async def test_requirement_priority_and_size(api: Any) -> None:
    client, runner = api
    create_resp = await client.post(
        "/api/requirements",
        json={
            "title": "Ajuste de Precios SAP",
            "description": "Calculo de precios para ventas.",
            "priority": "alta",
            "due_date": "2026-10-01T12:00:00Z",
        },
        headers=who("ana", "funcional"),
    )
    assert create_resp.status_code == 201
    req_id = create_resp.json()["id"]

    detail_resp = await client.get(
        f"/api/requirements/{req_id}", headers=who("ana", "funcional")
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["requirement"]["priority"] == "alta"

    prio_resp = await client.post(
        f"/api/requirements/{req_id}/priority",
        json={"priority": "urgente", "due_date": "2026-10-05T00:00:00Z"},
        headers=who("ana", "lider"),
    )
    assert prio_resp.status_code == 204

    detail_resp2 = await client.get(
        f"/api/requirements/{req_id}", headers=who("ana", "funcional")
    )
    assert detail_resp2.json()["requirement"]["priority"] == "urgente"

    size_resp = await client.post(
        f"/api/requirements/{req_id}/size",
        json={"size": "large"},
        headers=who("ana", "lider"),
    )
    assert size_resp.status_code == 200
    assert size_resp.json()["hours_total"] == 80.0

    detail_resp3 = await client.get(
        f"/api/requirements/{req_id}", headers=who("ana", "funcional")
    )
    assert detail_resp3.json()["estimate"]["hours_total"] == 80.0


async def test_inbox_endpoint(api: Any) -> None:
    client, runner = api
    create_resp1 = await client.post(
        "/api/requirements",
        json={"title": "Tarea Asignada", "description": "Tarea especifica asignada a un usuario."},
        headers=who("roberto", "funcional"),
    )
    req1_id = create_resp1.json()["id"]
    await client.post(f"/api/requirements/{req1_id}/claim", headers=who("roberto", "funcional"))

    create_resp2 = await client.post(
        "/api/requirements",
        json={"title": "Tarea Libre", "description": "Tarea en la bolsa general sin reclamar."},
        headers=who("laura", "funcional"),
    )
    assert create_resp2.status_code == 201

    inbox_resp = await client.get("/api/inbox", headers=who("roberto", "funcional"))
    assert inbox_resp.status_code == 200
    data = inbox_resp.json()
    assert isinstance(data["assigned"], list)
    assert isinstance(data["pool"], list)
    assert isinstance(data["waiting_gates"], list)
    assert isinstance(data["open_questions"], list)

    assigned_ids = [r["id"] for r in data["assigned"]]
    assert req1_id in assigned_ids

