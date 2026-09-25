from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fabrica.blackboard.service import Board
from fabrica.db.models import RunState
from fabrica.db.session import init_db, session_scope
from fabrica.domain.schemas import DecisionIn, DocumentIn, Identity, RequirementIn
from fabrica.pipeline import commands
from fabrica.pipeline.engine import Engine
from fabrica.tracking.portfolio import first_pass_rates

PILOT = Identity(user="piloto", role="funcional", roles=["funcional"])
DEFAULT_ANSWER = "Sin información adicional; usar el criterio estándar del módulo."


class Case(BaseModel):
    title: str
    description: str
    project: str = "demo"
    documents: list[DocumentIn] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)


async def _answer_open_questions(req_id: int, answers: list[str]) -> bool:
    async with session_scope() as s:
        board = Board(s)
        pending = await board.open_questions(req_id)
        resumed = False
        for index, question in enumerate(pending):
            text = answers[index] if index < len(answers) else DEFAULT_ANSWER
            resumed = await commands.answer_question(board, req_id, question.id, text, PILOT)
        return resumed


async def _approve_design(req_id: int) -> bool:
    async with session_scope() as s:
        board = Board(s)
        req = await board.requirement(req_id)
        if req.stage != "aprobacion_cliente" or req.state != RunState.WAITING_GATE:
            return False
        return await commands.record_decision(
            board, req_id, DecisionIn(outcome="approve", comment="piloto"), PILOT
        )


async def run_case(engine: Engine, case: Case) -> dict[str, Any]:
    started = time.monotonic()
    async with session_scope() as s:
        data = RequirementIn(**case.model_dump(exclude={"answers"}))
        req = await commands.create_requirement(Board(s), data, PILOT)
    await engine.drive(req.id)
    if await _answer_open_questions(req.id, case.answers):
        await engine.drive(req.id)
    if await _approve_design(req.id):
        await engine.drive(req.id)

    async with session_scope() as s:
        board = Board(s)
        final = await board.requirement(req.id)
        attempts = await board.attempts(req.id)
    tiers: dict[str, list[str]] = defaultdict(list)
    for attempt in attempts:
        tiers[attempt.activity].append(f"{attempt.tier}{'✓' if attempt.passed else '✗'}")
    return {
        "id": final.id,
        "title": final.title,
        "stage": final.stage,
        "state": str(final.state),
        "reached_abap_review": final.stage == "revision_abap",
        "cost_usd": round(final.spent_usd, 4),
        "seconds": round(time.monotonic() - started, 2),
        "attempts": dict(tiers),
        "first_pass": first_pass_rates(attempts),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results) or 1
    first_pass: dict[str, list[float]] = defaultdict(list)
    for result in results:
        for activity, rate in result["first_pass"].items():
            first_pass[activity].append(rate)
    return {
        "cases": len(results),
        "reached_abap_review": round(sum(r["reached_abap_review"] for r in results) / total, 3),
        "avg_cost_usd": round(sum(r["cost_usd"] for r in results) / total, 4),
        "first_pass_rate": {a: round(sum(v) / len(v), 3) for a, v in sorted(first_pass.items())},
    }


async def replay(cases: list[Case], engine: Engine | None = None) -> dict[str, Any]:
    await init_db()
    engine = engine or Engine()
    results = [await run_case(engine, case) for case in cases]
    return {"summary": summarize(results), "cases": results}


def run() -> None:
    parser = argparse.ArgumentParser(prog="fabrica-replay")
    parser.add_argument("casos", type=Path)
    parser.add_argument("--salida", type=Path)
    args = parser.parse_args()
    cases = [Case.model_validate(c) for c in json.loads(args.casos.read_text(encoding="utf-8"))]
    report = asyncio.run(replay(cases))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.salida:
        args.salida.write_text(text, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
