from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSON,
        list[Any]: JSON,
        datetime: DateTime(timezone=True),
    }


class RunState(StrEnum):
    RUNNING = "running"
    WAITING_GATE = "waiting_gate"
    BLOCKED = "blocked"
    DONE = "done"


class MessageKind(StrEnum):
    PREGUNTA = "pregunta"
    RESPUESTA = "respuesta"
    PROPUESTA = "propuesta"
    OBJECION = "objecion"
    EVIDENCIA = "evidencia"
    ESCALAMIENTO = "escalamiento"
    DECISION = "decision"
    INFO = "info"


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    project: Mapped[str] = mapped_column(String(80), default="demo")
    capability: Mapped[str | None] = mapped_column(String(40))
    ricefw: Mapped[str | None] = mapped_column(String(1))
    stage: Mapped[str] = mapped_column(String(40))
    state: Mapped[RunState] = mapped_column(String(20), default=RunState.RUNNING)
    created_by: Mapped[str] = mapped_column(String(80))
    spent_usd: Mapped[float] = mapped_column(default=0.0)
    repo_url: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)

    documents: Mapped[list[Document]] = relationship(back_populates="requirement")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text)

    requirement: Mapped[Requirement] = relationship(back_populates="documents")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    thread: Mapped[str] = mapped_column(String(60))
    sender: Mapped[str] = mapped_column(String(80))
    recipient: Mapped[str | None] = mapped_column(String(80))
    kind: Mapped[MessageKind] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    activity: Mapped[str] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(4))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    passed: Mapped[bool] = mapped_column(default=False)
    issues: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    commit: Mapped[str] = mapped_column(String(64))
    author: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(default=now)


class SapCall(Base):
    __tablename__ = "sap_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int | None] = mapped_column(index=True)
    system: Mapped[str] = mapped_column(String(40))
    tool: Mapped[str] = mapped_column(String(40))
    object_name: Mapped[str] = mapped_column(String(60), default="")
    actor: Mapped[str] = mapped_column(String(80))
    ok: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Transport(Base):
    __tablename__ = "transports"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    system: Mapped[str] = mapped_column(String(40))
    number: Mapped[str] = mapped_column(String(20))
    objects: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="modificable")
    created_at: Mapped[datetime] = mapped_column(default=now)
