"""Read-only Router task trace summary projection."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.models.router_schema import (
    EventCorrelation,
    RouterEvent,
    WorkerResult,
)
from app.repositories.event_repo import EventRepository
from app.repositories.gate_repo import GateResultRecord, GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRecord, WorkerJobRepository


class TraceSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class TraceEventSummary(TraceSummaryModel):
    event_id: str
    seq: int
    type: str
    source_type: str
    source_id: str | None = None
    source_worker_type: str | None = None
    severity: str
    visibility: str
    title: str
    message: str | None = None
    correlation: EventCorrelation
    payload_keys: list[str]
    created_at: datetime


class TraceFileSummary(TraceSummaryModel):
    path: str
    exists: bool | None = None
    size_bytes: int | None = None
    mime_type: str | None = None


class TraceWorkerJobSummary(TraceSummaryModel):
    worker_job_id: str
    worker_type: str
    status: str
    mcp_tool: str
    openai_trace_id: str | None = None
    main_agent_run_id: str | None = None
    mcp_request_id: str | None = None
    input_paths: list[str]
    read_paths: list[str]
    written_paths: list[str]
    report_paths: list[str]
    failure_ids: list[str]
    execution_status: str | None = None
    outcome_status: str | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TraceGateResultSummary(TraceSummaryModel):
    gate_result_id: str
    gate_type: str
    status: str
    blocking: bool
    evidence_paths: list[str]
    created_at: datetime


class TraceMainAgentRunSummary(TraceSummaryModel):
    main_agent_run_id: str
    openai_trace_id: str | None = None
    started_event_id: str | None = None
    started_seq: int | None = None
    started_at: datetime | None = None
    completed_event_id: str | None = None
    completed_seq: int | None = None
    completed_at: datetime | None = None
    error_event_ids: list[str]
    final_report_path: str | None = None
    replay_log_path: str | None = None


class TaskTraceSummary(TraceSummaryModel):
    task_id: str
    openai_trace_id: str | None = None
    main_agent_run_ids: list[str]
    latest_main_agent_run_id: str | None = None
    terminal_event_id: str | None = None
    terminal_event_type: str | None = None
    main_agent_runs: list[TraceMainAgentRunSummary]
    worker_jobs: list[TraceWorkerJobSummary]
    files: list[TraceFileSummary]
    gate_results: list[TraceGateResultSummary]
    events: list[TraceEventSummary]


class TraceSummaryService:
    """Build compact trace projections from persisted Router rows."""

    def __init__(self, session: Session) -> None:
        self.task_repository = TaskRepository(session)
        self.event_repository = EventRepository(session)
        self.worker_job_repository = WorkerJobRepository(session)
        self.gate_result_repository = GateResultRepository(session)

    def get_task_trace_summary(self, task_id: str) -> TaskTraceSummary:
        task = self.task_repository.get_task(task_id)
        events = self.event_repository.list_events(task_id)
        worker_jobs = self.worker_job_repository.list_task_jobs(task_id)
        gate_results = self.gate_result_repository.list_results(task_id)

        event_summaries = [_event_summary(event) for event in events]
        worker_summaries = [_worker_job_summary(job) for job in worker_jobs]
        gate_summaries = [_gate_result_summary(result) for result in gate_results]
        terminal_event = _terminal_event(events)

        return TaskTraceSummary(
            task_id=task.task_id,
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_ids=list(task.trace.main_agent_run_ids),
            latest_main_agent_run_id=task.trace.latest_main_agent_run_id,
            terminal_event_id=terminal_event.event_id if terminal_event else None,
            terminal_event_type=_value(terminal_event.type) if terminal_event else None,
            main_agent_runs=_main_agent_run_summaries(
                run_ids=task.trace.main_agent_run_ids,
                openai_trace_id=task.trace.openai_trace_id,
                events=events,
            ),
            worker_jobs=worker_summaries,
            files=_file_summaries(task),
            gate_results=gate_summaries,
            events=event_summaries,
        )


def _event_summary(event: RouterEvent) -> TraceEventSummary:
    return TraceEventSummary(
        event_id=event.event_id,
        seq=event.seq,
        type=_value(event.type),
        source_type=_value(event.source.type),
        source_id=event.source.id,
        source_worker_type=_optional_value(event.source.worker_type),
        severity=_value(event.severity),
        visibility=_value(event.visibility),
        title=event.title,
        message=event.message,
        correlation=event.correlation,
        payload_keys=sorted(str(key) for key in event.payload.keys()),
        created_at=event.created_at,
    )


def _worker_job_summary(job: WorkerJobRecord) -> TraceWorkerJobSummary:
    result = job.result
    trace_context = (
        result.trace_context if result is not None else job.input.trace_context
    )
    return TraceWorkerJobSummary(
        worker_job_id=job.id,
        worker_type=job.worker_type,
        status=job.status,
        mcp_tool=_value(job.input.mcp_tool),
        openai_trace_id=trace_context.openai_trace_id,
        main_agent_run_id=trace_context.main_agent_run_id,
        mcp_request_id=trace_context.mcp_request_id,
        input_paths=list(job.input.input_paths),
        read_paths=list(result.read_paths) if result is not None else [],
        written_paths=list(result.written_paths) if result is not None else [],
        report_paths=list(result.report_paths) if result is not None else [],
        failure_ids=_failure_ids(result),
        execution_status=(
            _value(result.execution_status) if result is not None else None
        ),
        outcome_status=_value(result.outcome.status) if result is not None else None,
        error_code=result.error.error_code if result is not None and result.error else None,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _gate_result_summary(result: GateResultRecord) -> TraceGateResultSummary:
    result_payload = result.result if isinstance(result.result, dict) else {}
    return TraceGateResultSummary(
        gate_result_id=result.id,
        gate_type=result.gate_type,
        status=result.status,
        blocking=result.blocking,
        evidence_paths=list(result_payload.get("evidence_paths") or []),
        created_at=result.created_at,
    )


def _main_agent_run_summaries(
    *,
    run_ids: list[str],
    openai_trace_id: str | None,
    events: list[RouterEvent],
) -> list[TraceMainAgentRunSummary]:
    summaries: list[TraceMainAgentRunSummary] = []
    for run_id in run_ids:
        run_events = [
            event
            for event in events
            if event.correlation.main_agent_run_id == run_id
            or event.source.id == run_id
            or event.payload.get("main_agent_run_id") == run_id
        ]
        started = _first_event(run_events, "agent.started") or _first_event(
            run_events,
            "main_agent.started",
        )
        completed = _first_event(run_events, "agent.completed") or _first_event(
            run_events,
            "main_agent.completed",
        )
        error_events = [
            event.event_id
            for event in run_events
            if _value(event.severity) == "error"
            or event.payload.get("error_code") is not None
        ]
        if completed is not None:
            final_report_path = completed.payload.get("final_report_path")
            replay_log_path = completed.payload.get("main_agent_log_path")
        else:
            final_report_path = None
            replay_log_path = None
        summaries.append(
            TraceMainAgentRunSummary(
                main_agent_run_id=run_id,
                openai_trace_id=openai_trace_id,
                started_event_id=started.event_id if started else None,
                started_seq=started.seq if started else None,
                started_at=started.created_at if started else None,
                completed_event_id=completed.event_id if completed else None,
                completed_seq=completed.seq if completed else None,
                completed_at=completed.created_at if completed else None,
                error_event_ids=error_events,
                final_report_path=str(final_report_path)
                if final_report_path is not None
                else None,
                replay_log_path=str(replay_log_path)
                if replay_log_path is not None
                else None,
            )
        )
    return summaries


def _first_event(events: list[RouterEvent], event_type: str) -> RouterEvent | None:
    for event in events:
        if _value(event.type) == event_type:
            return event
    return None


def _terminal_event(events: list[RouterEvent]) -> RouterEvent | None:
    terminal_types = {
        "task.succeeded",
        "task.partial_failed",
        "task.failed",
        "task.cancelled",
    }
    for event in reversed(events):
        if _value(event.type) in terminal_types:
            return event
    return None


def _failure_ids(result: WorkerResult | None) -> list[str]:
    if result is None:
        return []
    return [failure.failure_id for failure in result.failures]


def _file_summaries(task: Any) -> list[TraceFileSummary]:
    root = _workspace_root(task)
    summaries: list[TraceFileSummary] = []
    for path in task.current_files.all_paths:
        summaries.append(_file_summary(path, root))
    return summaries


def _file_summary(path: str, root: Path | None) -> TraceFileSummary:
    if root is None:
        return TraceFileSummary(path=path)

    try:
        target = (root / path).resolve()
        target.relative_to(root)
    except ValueError:
        return TraceFileSummary(path=path, exists=False)

    if not target.exists():
        return TraceFileSummary(path=path, exists=False)
    if not target.is_file():
        return TraceFileSummary(path=path, exists=True)
    return TraceFileSummary(
        path=path,
        exists=True,
        size_bytes=target.stat().st_size,
        mime_type=_mime_type_for_path(path),
    )


def _workspace_root(task: Any) -> Path | None:
    if task.workspace is not None and task.workspace.root:
        return Path(task.workspace.root).expanduser().resolve()
    if task.project_context.workspace_root:
        return Path(task.project_context.workspace_root).expanduser().resolve()
    return None


def _mime_type_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix in {".st", ".scl", ".txt", ".diff", ".patch"}:
        return "text/plain"
    return None


def _optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return _value(value)


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
