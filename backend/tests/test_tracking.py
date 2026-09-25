from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest

from fabrica.api.app import create_app
from fabrica.db.models import now
from fabrica.db.session import session_scope
from fabrica.notify.notifier import WebhookNotifier, announce
from fabrica.pilot.replay import Case, replay
from fabrica.pipeline.runner import InlineRunner
from fabrica.tracking.time import heartbeat, hours_by_user_and_requirement
from tests.helpers import build_until_abap_review, who


async def test_heartbeats_accumulate_active_time_and_split_on_idle() -> None:
    start = now()
    async with session_scope() as s:
        for minutes in (0, 3, 6):
            await heartbeat(
                s, user="ana", requirement_id=1, source="web", at=start + timedelta(minutes=minutes)
            )
        await heartbeat(
            s, user="ana", requirement_id=1, source="web", at=start + timedelta(minutes=30)
        )
        await heartbeat(
            s, user="ana", requirement_id=1, source="web", at=start + timedelta(minutes=32)
        )
        await heartbeat(s, user="ana", requirement_id=1, source="mcp", at=start)
        await heartbeat(
            s, user="ana", requirement_id=1, source="mcp", at=start + timedelta(minutes=4)
        )
        rows = await hours_by_user_and_requirement(s)
    assert rows == [
        {"user": "ana", "requirement_id": 1, "hours": 0.2, "by_source": {"web": 0.13, "mcp": 0.07}}
    ]


@pytest.fixture
async def api() -> Any:
    app = create_app()
    runner = InlineRunner()
    app.state.runner = runner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner


async def test_time_and_portfolio_permissions(api: Any) -> None:
    client, runner = api
    first = await build_until_abap_review(client, runner, "Reporte de facturas")
    req_id = first["requirement"]["id"]
    resp = await client.post(
        "/api/time/heartbeat", json={"requirement_id": req_id}, headers=who("ana", "funcional")
    )
    assert resp.status_code == 204
    own = await client.get("/api/time", headers=who("ana", "funcional"))
    assert [r["requirement_id"] for r in own.json()] == [req_id]
    assert (
        await client.get("/api/time?user=luis", headers=who("ana", "funcional"))
    ).status_code == 403
    assert (await client.get("/api/portfolio", headers=who("ana", "funcional"))).status_code == 403

    report = (await client.get("/api/portfolio", headers=who("lucia", "lider"))).json()
    assert report["total"] == 1 and report["by_stage"] == {"revision_abap": 1}
    assert report["waiting"][0]["id"] == req_id
    assert report["first_pass_rate"]["implementar"] == 0.0
    assert report["first_pass_rate"]["disenar_spec"] == 1.0
    [row] = report["requirements"]
    assert row["estimated_hours"] == first["estimate"]["hours_total"]
    assert row["ai_cost_usd"] > 0


async def test_announce_posts_gate_to_webhook(api: Any) -> None:
    client, runner = api
    first = await build_until_abap_review(client, runner, "Reporte de facturas")
    sent: list[dict[str, Any]] = []

    def hook(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    notifier = WebhookNotifier(
        "https://hooks.test/x", httpx.AsyncClient(transport=httpx.MockTransport(hook))
    )
    assert await announce(first["requirement"]["id"], notifier)
    assert "espera decisión de abap en Revisión ABAP" in sent[0]["text"]


async def test_replay_reports_pilot_metrics() -> None:
    cases = [
        Case(title="Reporte de facturas", description="Listar facturas por fecha."),
        Case(
            title="Reporte de pagos",
            description="Listar pagos por proveedor.",
            documents=[{"name": "spec", "content": "Campos: proveedor, importe, fecha."}],
        ),
    ]
    report = await replay(cases)
    assert report["summary"]["cases"] == 2
    assert report["summary"]["reached_abap_review"] == 1.0
    assert report["cases"][0]["attempts"]["implementar"] == ["N2✗", "N2✗", "N3✓"]
    assert report["summary"]["first_pass_rate"]["implementar"] == 0.0
