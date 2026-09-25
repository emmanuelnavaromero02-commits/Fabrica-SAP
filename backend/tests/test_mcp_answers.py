from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fabrica.blackboard.service import Board
from fabrica.db.models import MessageKind, RunState
from fabrica.db.session import session_scope
from fabrica.domain.schemas import Identity, RequirementIn
from fabrica.mcp_servers import fabrica as fabrica_mcp
from fabrica.pipeline.commands import create_requirement


class _Kicks:
    def __init__(self) -> None:
        self.kicked: list[int] = []

    async def kick(self, req_id: int) -> None:
        self.kicked.append(req_id)


async def _blocked_with_question() -> tuple[int, int, int]:
    async with session_scope() as s:
        board = Board(s)
        data = RequirementIn(title="Reporte", description="Reporte de facturas por fecha.")
        req = await create_requirement(board, data, Identity(user="ana", role="funcional"))
        req.state = RunState.BLOCKED
        question = await board.post(
            req.id, thread="recepcion", sender="analista", kind=MessageKind.PREGUNTA, body="¿?"
        )
        info = await board.post(
            req.id, thread="flujo", sender="motor", kind=MessageKind.INFO, body="nota"
        )
        return req.id, question.id, info.id


async def _answer(req_id: int, message_id: int) -> dict[str, Any]:
    args = {"requisito_id": req_id, "pregunta_id": message_id, "texto": "Por fecha."}
    result = await fabrica_mcp.server.call_tool("responder_pregunta", args)
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content
    return content


async def test_answering_last_question_by_mcp_resumes_the_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _Kicks()
    monkeypatch.setattr(fabrica_mcp, "build_runner", lambda: runner)
    req_id, question_id, _ = await _blocked_with_question()
    assert (await _answer(req_id, question_id))["reanudado"] is True
    assert runner.kicked == [req_id]
    async with session_scope() as s:
        assert (await Board(s).requirement(req_id)).state == RunState.RUNNING


async def test_only_open_questions_can_be_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fabrica_mcp, "build_runner", _Kicks)
    req_id, question_id, info_id = await _blocked_with_question()
    await _answer(req_id, question_id)
    for message_id in (question_id, info_id):
        with pytest.raises(ToolError, match="(?i)pregunta"):
            await fabrica_mcp.server.call_tool(
                "responder_pregunta",
                {"requisito_id": req_id, "pregunta_id": message_id, "texto": "otra"},
            )
