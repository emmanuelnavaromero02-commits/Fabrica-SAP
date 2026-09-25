from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from fabrica.blackboard.service import Board
from fabrica.catalog import ModelSpec
from fabrica.db.models import Attempt
from fabrica.db.session import session_scope
from fabrica.domain.schemas import Identity, RequirementIn
from fabrica.escalation.router import EscalationRouter
from fabrica.git.repo import GiteaRepoStore, safe_repo_path
from fabrica.llm.base import LLMError, LLMRequest, LLMResult
from fabrica.llm.gateway import ModelGateway
from fabrica.pipeline.commands import create_requirement
from fabrica.sap import factory
from fabrica.sap.bridge import SapObject
from fabrica.verifiers.abap import AbapVerifier
from fabrica.verifiers.base import Verification


async def _requirement(board: Board) -> int:
    data = RequirementIn(title="Reporte", description="Reporte de facturas por fecha.")
    req = await create_requirement(board, data, Identity(user="ana", role="funcional"))
    return req.id


async def test_released_transport_is_replaced_by_a_new_one() -> None:
    async with session_scope() as s:
        board = Board(s)
        req_id = await _requirement(board)
        sap = await factory.sap_for(board, req_id, "dev")
        obj = SapObject("ZREP", "PROG", "ZFAB", "REPORT zrep.")
        first = await sap.write(obj)
        assert await sap.write(obj) == first
        fake: Any = factory.connect(None)  # type: ignore[arg-type]
        fake.released.add(first)
        second = await sap.write(obj)
        assert second != first
        statuses = {t.number: t.status for t in await board.transports(req_id)}
    assert statuses == {first: "liberada", second: "modificable"}


async def _verifier(main: dict[str, str]) -> AbapVerifier:
    async with session_scope() as s:
        board = Board(s)
        sap = await factory.sap_for(board, await _requirement(board), "dev")
    return AbapVerifier(sap, main_object=main, assertions=[])


async def test_verifier_rejects_empty_source_without_touching_sap() -> None:
    verifier = await _verifier({"name": "ZREP", "type": "PROG", "package": "ZFAB"})
    result = await verifier.verify({"files": [{"path": "zrep.abap", "content": "  "}]})
    assert not result.passed
    assert await factory.connect(None).read_object("ZREP") is None  # type: ignore[arg-type]


async def test_verifier_rejects_unsupported_object_type() -> None:
    verifier = await _verifier({"name": "ZTAB", "type": "TABL", "package": "ZFAB"})
    result = await verifier.verify({"files": [{"path": "ztab", "content": "x"}]})
    assert not result.passed and "TABL" in result.issues[0]


class _BrokenJson:
    name = "roto"

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult:
        raise LLMError("JSON inválido", tokens_in=1_000_000, tokens_out=0)


class _NeverCalled:
    async def verify(self, output: dict[str, Any]) -> Verification:
        raise AssertionError("no debe verificarse")


async def test_failed_provider_call_is_still_charged() -> None:
    spec = ModelSpec(provider="anthropic", model="m", price_in=3.0)
    async with session_scope() as s:
        board = Board(s)
        req_id = await _requirement(board)
        router = EscalationRouter(ModelGateway({"anthropic": _BrokenJson()}), board)
        request = LLMRequest(system="s", prompt="p", schema={"type": "object"})
        verification, output, cost, usage = await router._attempt(
            spec, "N1", request, _NeverCalled()
        )
        assert not verification.passed and output is None
        assert (cost, usage) == (3.0, (1_000_000, 0))
        await board.record_attempt(
            Attempt(
                requirement_id=req_id,
                activity="x",
                tier="N1",
                provider="anthropic",
                model="m",
                tokens_in=usage[0],
                tokens_out=usage[1],
                cost_usd=cost,
                passed=False,
            )
        )
        assert (await board.requirement(req_id)).spent_usd == 3.0


@pytest.mark.parametrize(
    "path", ["../otro/x", "/etc/passwd", "a/../../b", "a?ref=x", "a#b", "a\\b", "", "a//b"]
)
def test_repo_paths_that_escape_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        safe_repo_path(path)


def test_repo_paths_are_url_quoted() -> None:
    assert safe_repo_path("src/z rep%.abap") == "src/z%20rep%25.abap"


async def test_gitea_keeps_token_out_of_urls(tmp_path: Path) -> None:
    seen: list[str] = []

    def gitea(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    store = GiteaRepoStore("https://git.test", "secreto", "fabrica")
    store.http = httpx.AsyncClient(
        base_url="https://git.test/api/v1", transport=httpx.MockTransport(gitea)
    )
    assert "secreto" not in store.clone_url(3)
    assert "secreto" in "".join(store.git_auth_env().values())
    assert await store.read_file(3, "../../admin") is None
    assert await store.read_file(3, "src/a b.abap") is None
    assert seen == ["https://git.test/api/v1/repos/fabrica/req-3/raw/src/a%20b.abap"]
