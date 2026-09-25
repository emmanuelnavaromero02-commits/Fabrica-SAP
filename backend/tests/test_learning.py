from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from fabrica.api.app import create_app
from fabrica.catalog import ActivityPolicy, LearningPolicy
from fabrica.db.models import Lesson
from fabrica.db.session import session_scope
from fabrica.escalation.learning import learned_tiers
from fabrica.knowledge.lessons import keywords, similar_requirements
from fabrica.pipeline.runner import InlineRunner
from tests.helpers import build_until_abap_review

POLICY = ActivityPolicy(start="N1", max_tier="N4", attempts=1)
LEARNING = LearningPolicy(min_samples=4, min_pass_rate=0.5)


def test_learned_tiers_skip_levels_that_keep_failing() -> None:
    assert learned_tiers(POLICY, LEARNING, {}) == ["N1", "N2", "N3", "N4"]
    assert learned_tiers(POLICY, LEARNING, {"N1": (3, 0)}) == ["N1", "N2", "N3", "N4"]
    assert learned_tiers(POLICY, LEARNING, {"N1": (4, 1)}) == ["N2", "N3", "N4"]
    rates = {"N1": (10, 0), "N2": (8, 1), "N3": (8, 0)}
    assert learned_tiers(POLICY, LEARNING, rates) == ["N4"]
    assert learned_tiers(POLICY, LEARNING, {"N1": (10, 6)}) == ["N1", "N2", "N3", "N4"]


def test_keywords_ignore_accents_and_stopwords() -> None:
    assert keywords("Reporte de Facturación del período") == {"reporte", "facturacion", "periodo"}


@pytest.fixture
async def api() -> Any:
    app = create_app()
    runner = InlineRunner()
    app.state.runner = runner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner


async def test_factory_learns_lessons_and_starting_tier(api: Any) -> None:
    client, runner = api
    first = await build_until_abap_review(client, runner, "Reporte de facturas")
    assert [a["tier"] for a in first["attempts"] if a["activity"] == "implementar"] == [
        "N2",
        "N2",
        "N3",
    ]
    async with session_scope() as s:
        lessons = (await s.scalars(select(Lesson))).all()
    assert any("SELECT *" in lesson.text for lesson in lessons)

    for title in ("Reporte de pagos", "Reporte de cobranzas"):
        await build_until_abap_review(client, runner, title)
    fourth = await build_until_abap_review(client, runner, "Reporte de compras")
    tiers = [a["tier"] for a in fourth["attempts"] if a["activity"] == "implementar"]
    assert tiers == ["N3"]
    assert any("inicia en N3" in m["body"] for m in fourth["messages"])

    async with session_scope() as s:
        used = (await s.scalars(select(Lesson).where(Lesson.uses > 0))).all()
        similar = await similar_requirements(s, "reporte de facturas")
    assert used
    assert similar == []


async def test_knowledge_mcp_serves_standards_and_lessons() -> None:
    from fabrica.mcp_servers.conocimiento import server

    rules = (await server.call_tool("estandares", {"modulo": "fi"})).structured_content
    assert rules is not None
    assert any("BAPI_ACC_DOCUMENT_POST" in r for r in rules["result"])
    saved = await server.call_tool(
        "guardar_leccion",
        {"actividad": "implementar", "texto": "Usar moneda en importes", "requisito_id": 1},
    )
    assert saved.structured_content == {"creada": True}
    found = await server.call_tool(
        "buscar_lecciones", {"actividad": "implementar", "texto": "importes con moneda"}
    )
    assert found.structured_content is not None
    assert found.structured_content["result"][0]["texto"] == "Evitar: Usar moneda en importes"
