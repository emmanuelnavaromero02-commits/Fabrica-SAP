from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from fabrica.api.deps import RunnerDep, Who
from fabrica.blackboard.service import Board
from fabrica.db.models import Document, MessageKind, Requirement, RunState
from fabrica.db.session import session_scope
from fabrica.documents.extract import UnsupportedDocument, document_name, extract_text
from fabrica.domain.schemas import DocumentIn, DocumentOut, RequirementIn, RequirementOut
from fabrica.pipeline import commands

router = APIRouter(prefix="/api/requirements", tags=["documentos"])

Files = Annotated[list[UploadFile], File(description="PDF, DOCX o texto")]
Kinds = Annotated[list[str] | None, Form(description="Tipo de cada archivo, en el mismo orden")]


async def _read(files: list[UploadFile], kinds: list[str] | None) -> list[DocumentIn]:
    documents = []
    for index, upload in enumerate(files):
        name = upload.filename or f"documento-{index + 1}"
        try:
            text = extract_text(name, await upload.read())
        except UnsupportedDocument as exc:
            raise HTTPException(415, str(exc)) from exc
        kind = kinds[index] if kinds and index < len(kinds) else "especificacion"
        documents.append(DocumentIn(name=document_name(name), kind=kind, content=text))
    return documents


@router.post("/with-files", response_model=RequirementOut, status_code=201)
async def create_with_files(
    who: Who,
    runner: RunnerDep,
    title: Annotated[str, Form(min_length=3, max_length=200)],
    description: Annotated[str, Form(min_length=10)],
    files: Files,
    project: Annotated[str, Form()] = "demo",
    kinds: Kinds = None,
) -> Requirement:
    documents = await _read(files, kinds)
    data = RequirementIn(title=title, description=description, project=project, documents=documents)
    async with session_scope() as s:
        req = await commands.create_requirement(Board(s), data, who)
    await runner.kick(req.id)
    return req


@router.post("/{req_id}/documents", response_model=list[DocumentOut], status_code=201)
async def add_documents(
    req_id: int, who: Who, runner: RunnerDep, files: Files, kinds: Kinds = None
) -> list[DocumentOut]:
    documents = await _read(files, kinds)
    async with session_scope() as s:
        board = Board(s)
        try:
            req = await board.requirement(req_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        added = [
            Document(requirement_id=req_id, name=d.name, kind=d.kind, content=d.content)
            for d in documents
        ]
        s.add_all(added)
        await board.post(
            req_id,
            thread="analizar",
            sender=who.user,
            kind=MessageKind.EVIDENCIA,
            body=f"Documentos agregados: {', '.join(d.name for d in documents)}",
        )
        resume = req.state == RunState.BLOCKED and not await board.open_questions(req_id)
        if resume:
            req.state = RunState.RUNNING
        await s.flush()
        result = [DocumentOut.model_validate(d) for d in added]
    if resume:
        await runner.kick(req_id)
    return result


@router.get("/{req_id}/documents", response_model=list[DocumentOut])
async def list_documents(req_id: int, who: Who) -> list[DocumentOut]:
    async with session_scope() as s:
        try:
            req = await Board(s).requirement(req_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return [DocumentOut.model_validate(d) for d in req.documents]
