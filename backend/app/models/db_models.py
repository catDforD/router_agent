"""SQLAlchemy models for Router runtime persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import JSON as SAJSON
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


json_type = SAJSON().with_variant(postgresql.JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base."""


class TaskRow(Base):
    """Persisted Router task state plus query projections."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    difficulty_level: Mapped[str] = mapped_column(String(16), nullable=False)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_tasks_session_id", "session_id"),
        Index("ix_tasks_user_id", "user_id"),
        Index("ix_tasks_status_phase", "status", "phase"),
        Index("ix_tasks_task_type", "task_type"),
    )


class AgentSessionRow(Base):
    """Persisted conversation session spanning multiple task/run rows."""

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    latest_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latest_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    session_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_agent_sessions_status_updated_at", "status", "updated_at"),
        Index("ix_agent_sessions_user_id", "user_id"),
    )


class AgentSessionEventRow(Base):
    """Session-scoped event mirror with monotonically increasing session seq."""

    __tablename__ = "agent_session_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_agent_session_events_seq"),
        Index("ix_agent_session_events_session_seq", "session_id", "seq"),
        Index("ix_agent_session_events_task_id", "task_id"),
        Index("ix_agent_session_events_type", "type"),
    )


class AgentRunRow(Base):
    """One user prompt execution inside an AgentSession."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    user_message: Mapped[str] = mapped_column(String(4096), nullable=False)
    final_response: Mapped[str | None] = mapped_column(String(12000), nullable=True)
    run_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_agent_runs_session_created_at", "session_id", "created_at"),
        Index("ix_agent_runs_task_id", "task_id"),
        Index("ix_agent_runs_status", "status"),
    )


class ArtifactRow(Base):
    """Persisted artifact metadata plus query projections."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str] = mapped_column(String(4096), nullable=False)
    artifact_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_artifacts_task_id", "task_id"),
        Index("ix_artifacts_task_type_version", "task_id", "type", "version"),
        Index("ix_artifacts_status", "status"),
    )


class EventRow(Base):
    """Append-only Router event row."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_events_task_id_seq"),
        Index("ix_events_task_id_seq", "task_id", "seq"),
        Index("ix_events_type", "type"),
    )


class WorkerJobRow(Base):
    """Persisted worker job lifecycle row."""

    __tablename__ = "worker_jobs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(json_type, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "idempotency_key",
            name="uq_worker_jobs_task_id_idempotency_key",
        ),
        Index("ix_worker_jobs_task_id", "task_id"),
        Index("ix_worker_jobs_worker_type", "worker_type"),
        Index("ix_worker_jobs_status", "status"),
    )


class GateResultRow(Base):
    """Internal persisted quality gate result row."""

    __tablename__ = "gate_results"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    gate_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_artifact_ids: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_gate_results_task_id_created_at", "task_id", "created_at"),
        Index("ix_gate_results_gate_type", "gate_type"),
        Index("ix_gate_results_status", "status"),
    )
