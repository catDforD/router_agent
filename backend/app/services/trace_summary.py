"""Read-only Router task trace summary projection."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.models.router_schema import (
    Artifact,
    EventCorrelation,
    RouterEvent,
    WorkerResult,
)
from app.repositories.artifact_repo import ArtifactRepository
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


class TraceArtifactSummary(TraceSummaryModel):
    artifact_id: str
    type: str
    version: int
    status: str
    visibility: str
    uri: str
    content_hash: str | None = None
    size_bytes: int | None = None
    summary: str
    parent_artifact_ids: list[str]
    derived_from_worker_job_id: str | None = None
    derived_from_artifact_ids: list[str] | None = None
    created_by_type: str
    created_by_id: str | None = None
    created_by_worker_job_id: str | None = None
    created_by_main_agent_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TraceWorkerJobSummary(TraceSummaryModel):
    worker_job_id: str
    worker_type: str
    status: str
    mcp_tool: str
    openai_trace_id: str | None = None
    main_agent_run_id: str | None = None
    mcp_request_id: str | None = None
    input_artifact_ids: list[str]
    produced_artifact_ids: list[str]
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
    evidence_artifact_ids: list[str]
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
    final_report_artifact_id: str | None = None
    replay_log_artifact_id: str | None = None


class TaskTraceSummary(TraceSummaryModel):
    task_id: str
    openai_trace_id: str | None = None
    main_agent_run_ids: list[str]
    latest_main_agent_run_id: str | None = None
    terminal_event_id: str | None = None
    terminal_event_type: str | None = None
    main_agent_runs: list[TraceMainAgentRunSummary]
    worker_jobs: list[TraceWorkerJobSummary]
    artifacts: list[TraceArtifactSummary]
    gate_results: list[TraceGateResultSummary]
    events: list[TraceEventSummary]


class TraceSummaryService:
    """Build compact trace projections from persisted Router rows."""

    def __init__(self, session: Session) -> None:
        self.task_repository = TaskRepository(session)
        self.event_repository = EventRepository(session)
        self.worker_job_repository = WorkerJobRepository(session)
        self.artifact_repository = ArtifactRepository(session)
        self.gate_result_repository = GateResultRepository(session)

    def get_task_trace_summary(self, task_id: str) -> TaskTraceSummary:
        task = self.task_repository.get_task(task_id)
        events = self.event_repository.list_events(task_id)
        worker_jobs = self.worker_job_repository.list_task_jobs(task_id)
        artifacts = self.artifact_repository.list_task_artifacts(task_id)
        gate_results = self.gate_result_repository.list_results(task_id)

        event_summaries = [_event_summary(event) for event in events]
        artifact_summaries = [_artifact_summary(artifact) for artifact in artifacts]
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
                artifacts=artifacts,
            ),
            worker_jobs=worker_summaries,
            artifacts=artifact_summaries,
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


def _artifact_summary(artifact: Artifact) -> TraceArtifactSummary:
    return TraceArtifactSummary(
        artifact_id=artifact.artifact_id,
        type=_value(artifact.type),
        version=artifact.version,
        status=_value(artifact.status),
        visibility=_value(artifact.visibility),
        uri=artifact.storage.uri,
        content_hash=artifact.storage.content_hash,
        size_bytes=artifact.storage.size_bytes,
        summary=artifact.summary,
        parent_artifact_ids=list(artifact.parent_artifact_ids),
        derived_from_worker_job_id=artifact.derived_from_worker_job_id,
        derived_from_artifact_ids=(
            list(artifact.derived_from_artifact_ids)
            if artifact.derived_from_artifact_ids is not None
            else None
        ),
        created_by_type=_value(artifact.created_by.type),
        created_by_id=artifact.created_by.id,
        created_by_worker_job_id=artifact.created_by.worker_job_id,
        created_by_main_agent_run_id=artifact.created_by.main_agent_run_id,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
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
        input_artifact_ids=[
            artifact.artifact_id for artifact in job.input.input_artifacts
        ],
        produced_artifact_ids=_produced_artifact_ids(result),
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
    return TraceGateResultSummary(
        gate_result_id=result.id,
        gate_type=result.gate_type,
        status=result.status,
        blocking=result.blocking,
        evidence_artifact_ids=list(result.evidence_artifact_ids),
        created_at=result.created_at,
    )


def _main_agent_run_summaries(
    *,
    run_ids: list[str],
    openai_trace_id: str | None,
    events: list[RouterEvent],
    artifacts: list[Artifact],
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
        started = _first_event(run_events, "main_agent.started")
        completed = _first_event(run_events, "main_agent.completed")
        error_events = [
            event.event_id
            for event in run_events
            if _value(event.severity) == "error"
            or event.payload.get("error_code") is not None
        ]
        run_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.created_by.main_agent_run_id == run_id
            or artifact.created_by.id == run_id
        ]
        final_report = _first_artifact(run_artifacts, "final_report")
        replay_log = _first_artifact(run_artifacts, "main_agent_log")
        if completed is not None:
            final_report_id = completed.payload.get("final_report_artifact_id")
            replay_log_id = completed.payload.get("main_agent_log_artifact_id")
        else:
            final_report_id = None
            replay_log_id = None
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
                final_report_artifact_id=(
                    str(final_report_id)
                    if final_report_id is not None
                    else final_report.artifact_id if final_report else None
                ),
                replay_log_artifact_id=(
                    str(replay_log_id)
                    if replay_log_id is not None
                    else replay_log.artifact_id if replay_log else None
                ),
            )
        )
    return summaries


def _first_event(events: list[RouterEvent], event_type: str) -> RouterEvent | None:
    for event in events:
        if _value(event.type) == event_type:
            return event
    return None


def _first_artifact(artifacts: list[Artifact], artifact_type: str) -> Artifact | None:
    for artifact in artifacts:
        if _value(artifact.type) == artifact_type:
            return artifact
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


def _produced_artifact_ids(result: WorkerResult | None) -> list[str]:
    if result is None:
        return []
    return [artifact.artifact_id for artifact in result.produced_artifacts]


def _failure_ids(result: WorkerResult | None) -> list[str]:
    if result is None:
        return []
    return [failure.failure_id for failure in result.failures]


def _optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return _value(value)


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
