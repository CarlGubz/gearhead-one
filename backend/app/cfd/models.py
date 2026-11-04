"""CFD ORM models (tables prefixed ``cfd_`` in the shared Gearhead database).

A CFD *case* (called "project" in the original CFDFlow API, hence the `Project` alias below) always belongs to a Gearhead
project (`project_id` -> `projects.id`), so it is owned by that project's user, travels in the project file and is
deleted with it. One Gearhead project can hold several CFD cases, next to its FEA model stack and its CAD documents.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .. import models as _core  # noqa: F401  (registers `projects` for the foreign key, also in the worker process)
from .db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CfdCase(Base):
    __tablename__ = "cfd_cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    runs: Mapped[list["CfdRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class CfdRun(Base):
    __tablename__ = "cfd_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("cfd_cases.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | running | completed | failed | cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    sim_time: Mapped[float] = mapped_column(Float, default=0.0)
    converged: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONType)  # immutable snapshot of the case
    summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[CfdCase] = relationship(back_populates="runs")
    samples: Mapped[list["CfdMonitorSample"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    logs: Mapped[list["CfdRunLog"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class CfdMonitorSample(Base):
    __tablename__ = "cfd_monitor_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("cfd_runs.id", ondelete="CASCADE"))
    iteration: Mapped[int] = mapped_column(Integer)
    sim_time: Mapped[float] = mapped_column(Float)
    residuals: Mapped[dict] = mapped_column(JSONType)
    values: Mapped[dict] = mapped_column(JSONType)

    __table_args__ = (Index("ix_cfd_monitor_run_iter", "run_id", "iteration"),)


class CfdRunLog(Base):
    __tablename__ = "cfd_run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("cfd_runs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    level: Mapped[str] = mapped_column(String(8), default="info")
    message: Mapped[str] = mapped_column(Text)


# Names used by the original CFDFlow code (api/, services/, worker.py)
Project = CfdCase
Run = CfdRun
MonitorSample = CfdMonitorSample
RunLog = CfdRunLog
