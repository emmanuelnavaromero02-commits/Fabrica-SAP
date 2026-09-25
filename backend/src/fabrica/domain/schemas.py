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


class DocumentOut(ORM):
    id: int
    name: str
    kind: str
    content: str


class RequirementIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10)
    project: str = "demo"
    capability: str | None = None
    ricefw: str | None = None
    priority: str = "media"
    due_date: datetime | None = None
    documents: list[DocumentIn] = Field(default_factory=list)


class PriorityUpdateIn(BaseModel):
    priority: str = Field(pattern="^(urgente|alta|media|baja)$")
    due_date: datetime | None = None


class SizeUpdateIn(BaseModel):
    size: str = Field(pattern="^(tiny|small|medium|large|very_large)$")


class RequirementOut(ORM):
    id: int
    title: str
    description: str
    project: str
    capability: str | None
    ricefw: str | None
    priority: str = "media"
    due_date: datetime | None = None
    stage: str
    state: str
    created_by: str
    spent_usd: float
    repo_url: str | None
    holder_role: str | None = None
    holder_user: str | None = None
    holder_since: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MessageOut(ORM):
    id: int
    requirement_id: int
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


class TransportOut(ORM):
    id: int
    system: str
    number: str
    objects: list[str]
    status: str
    created_at: datetime


class EstimateOut(ORM):
    id: int
    items: list[dict[str, Any]]
    breakdown: dict[str, float]
    assumptions: list[str]
    hours_base: float
    hours_total: float
    days: float
    complexity: str
    created_at: datetime


class SapCallOut(ORM):
    id: int
    system: str
    tool: str
    object_name: str
    actor: str
    ok: bool
    detail: str
    created_at: datetime


class FileOut(BaseModel):
    path: str
    content: str


class RequirementDetail(BaseModel):
    requirement: RequirementOut
    messages: list[MessageOut]
    attempts: list[AttemptOut]
    decisions: list[DecisionOut]
    artifacts: list[ArtifactOut]
    transports: list[TransportOut] = Field(default_factory=list)
    estimate: EstimateOut | None = None
    sap_calls: list[SapCallOut] = Field(default_factory=list)


class StageOut(BaseModel):
    key: str
    label: str
    kind: str
    roles: list[str]
    next: str | None = None
    on_reject: str | None = None


class Identity(BaseModel):
    user: str
    role: str
    roles: list[str] = Field(default_factory=list)


class AuthConfigOut(BaseModel):
    mode: str
    issuer: str
    client_id: str


class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=2, max_length=40)


class ClientOut(ORM):
    id: int
    name: str
    code: str
    created_at: datetime


class ProjectIn(BaseModel):
    client_id: int | None = None
    name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=2, max_length=40)
    sap_system: str | None = None


class ProjectOut(ORM):
    id: int
    client_id: int | None
    name: str
    code: str
    sap_system: str | None
    created_at: datetime


class CapabilityIn(BaseModel):
    project_id: int | None = None
    name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=2, max_length=40)


class CapabilityOut(ORM):
    id: int
    project_id: int | None
    name: str
    code: str
    created_at: datetime


class TransferIn(BaseModel):
    user: str = Field(min_length=1, max_length=80)


class BillingSnapshotIn(BaseModel):
    project: str = Field(min_length=1, max_length=80)


class BillingSnapshotOut(ORM):
    id: int
    project: str
    created_by: str
    hours_total: float
    hours_billed: float
    items: list[dict[str, Any]]
    created_at: datetime
