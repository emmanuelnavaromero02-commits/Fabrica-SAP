from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from fabrica.db.models import Base, now


class StageSegment(Base):
    __tablename__ = "stage_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20))
    clock: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(default=now)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    working_hours: Mapped[float | None] = mapped_column(default=None)


class Holiday(Base):
    __tablename__ = "holidays"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class ContractReport(Base):
    __tablename__ = "contract_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    passed: Mapped[bool] = mapped_column(default=False)
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)


class QualityReport(Base):
    __tablename__ = "quality_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    object_name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(8))
    score: Mapped[int] = mapped_column(default=100)
    blocking: Mapped[bool] = mapped_column(default=False)
    findings: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=now)
