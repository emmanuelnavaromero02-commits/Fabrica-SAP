from __future__ import annotations

from typing import Any

import httpx

from fabrica.pipeline.runner import InlineRunner


def who(user: str, role: str) -> dict[str, str]:
    return {"X-Fabrica-User": user, "X-Fabrica-Role": role}


async def detail(client: httpx.AsyncClient, req_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/requirements/{req_id}", headers=who("ana", "funcional"))
    assert resp.status_code == 200
    data: dict[str, Any] = resp.json()
    return data


async def build_until_abap_review(
    client: httpx.AsyncClient, runner: InlineRunner, title: str
) -> dict[str, Any]:
    body = {
        "title": title,
        "description": f"{title}: listar documentos por fecha.",
        "documents": [{"name": "spec", "content": "Campos: sociedad, documento, fecha."}],
    }
    resp = await client.post("/api/requirements", json=body, headers=who("ana", "funcional"))
    req_id = resp.json()["id"]
    await runner.wait_idle()
    await client.post(
        f"/api/requirements/{req_id}/decisions",
        json={"outcome": "approve"},
        headers=who("ana", "funcional"),
    )
    await runner.wait_idle()
    return await detail(client, req_id)
