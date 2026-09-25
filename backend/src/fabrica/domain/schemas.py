"""Contratos del API (entrada y salida). El frontend replica estos tipos en TypeScript."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentIn(BaseModel):
    name: str
    kind: str = "especificacion"
    content: str


class RequirementIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10)
    project: str = "demo"
    documents: list[DocumentIn] = Field(default_factory=list)


class RequirementOut(ORM):
    id: int
    title: str
    description: str
    project: str
    capability: str | None
    ricefw: str | None
    stage: str
    state: str
    created_by: str
    spent_usd: float
    repo_url: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(ORM):
    id: int
    thread: str
    sender: str
    recipient: str | None
    kind: str
    body: str
    data: dict[str, Any]
    resolved: bool
    created_at: datetime


class AttemptOut(ORM):
    id: int
    activity: str
    tier: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    passed: bool
    issues: list[Any]
    created_at: datetime


class DecisionIn(BaseModel):
    outcome: Literal["approve", "reject", "discard"]
    comment: str = ""


class DecisionOut(ORM):
    id: int
    stage: str
    outcome: str
    actor: str
    role: str
    comment: str
    created_at: datetime


class AnswerIn(BaseModel):
    message_id: int
    body: str = Field(min_length=1)


class ArtifactOut(ORM):
    id: int
    path: str
    commit: str
    author: str
    created_at: datetime


class RequirementDetail(BaseModel):
    requirement: RequirementOut
    messages: list[MessageOut]
    attempts: list[AttemptOut]
    decisions: list[DecisionOut]
    artifacts: list[ArtifactOut]


class StageOut(BaseModel):
    key: str
    label: str
    kind: str
    roles: list[str]


class Identity(BaseModel):
    user: str
    role: str
