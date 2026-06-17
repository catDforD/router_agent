"""Main Agent function tools backed by deterministic Router runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

try:  # Keep core service tests independent from the SDK import boundary.
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover - exercised only when SDK is absent locally.
    RunContextWrapper = Any  # type: ignore[assignment]

    def function_tool(func: Any | None = None, **_: Any) -> Any:
        if func is None:
            return lambda wrapped: wrapped
        return func

from app.core.errors import RepositoryNotFoundError
from app.core.ids import new_event_id
from app.core.time import utc_now
from app.mcp.adapter import McpAdapter
from app.mcp.mock_worker import DEFAULT_MOCK_SCENARIO
from app.models.router_schema import (
    Artifact,
    ArtifactRef,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    Failure,
    RouterEvent,
    TaskPhase,
    TaskState,
    TaskStatus,
    TraceContext,
    WorkerExecutionStatus,
    WorkerInput,
    WorkerJobRef,
    WorkerJobStatus,
    WorkerResult,
    WorkerType,
)
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import (
    ArtifactStore,
    ArtifactStoreContentError,
    ArtifactStoreInvalidStorageError,
    ArtifactStoreUnsupportedProviderError,
)
from app.services.event_service import EventService
from app.services.quality_gate import QualityGateService
from app.services.scheduler_guard import (
    ProposedWorkerJob,
    SchedulerGuardViolation,
    validate_finish_task,
    validate_parallel_jobs,
    validate_worker_call,
)
from app.workers.worker_input_builder import (
    WorkerInputBuildError,
    build_worker_input,
)
from app.workers.worker_result_handler import handle_worker_result


DEFAULT_READ_ARTIFACT_MAX_CHARS = 12_000
TERMINAL_EVENT_BY_STATUS = {
    TaskStatus.SUCCEEDED.value: EventType.TASK_SUCCEEDED,
    TaskStatus.PARTIAL_FAILED.value: EventType.TASK_PARTIAL_FAILED,
    TaskStatus.FAILED.value: EventType.TASK_FAILED,
    TaskStatus.CANCELLED.value: EventType.TASK_CANCELLED,
}


@dataclass(frozen=True)
class AgentToolContext:
    """Runtime resources passed to SDK tool calls through agent context."""

    session: Session
    artifact_root: Path
    mcp_mode: str = "mock"
    mock_scenario: str = DEFAULT_MOCK_SCENARIO
    read_artifact_max_chars: int = DEFAULT_READ_ARTIFACT_MAX_CHARS


class ToolStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    NOOP = "no-op"


class ToolBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ArtifactRefSummary(ToolBaseModel):
    artifact_id: str
    type: str
    version: int
    uri: str | None = None
    summary: str | None = None
    content_hash: str | None = None


class FailureSummary(ToolBaseModel):
    failure_id: str
    source: str
    severity: str
    status: str
    title: str
    evidence_artifact_ids: list[str]


class GateStateSummary(ToolBaseModel):
    test_required: bool
    formal_required: bool
    regression_required: bool
    formal_regression_required: bool
    latest_test_passed: bool | None
    latest_formal_passed: bool | None
    has_blocking_failure: bool
    can_finish_as_success: bool


class ToolViolation(ToolBaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolError(ToolBaseModel):
    error_code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactReadSummary(ToolBaseModel):
    artifact_id: str
    task_id: str
    type: str
    version: int
    name: str
    summary: str
    uri: str
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    content: str | None = None
    content_truncated: bool = False
    content_chars: int | None = None


class AgentToolResult(ToolBaseModel):
    """Compact tool output intended for Main Agent context."""

    tool: str
    task_id: str | None = None
    status: ToolStatus
    summary: str
    artifact_refs: list[ArtifactRefSummary] = Field(default_factory=list)
    failures: list[FailureSummary] = Field(default_factory=list)
    gate_state: GateStateSummary | None = None
    next_recommended_action: str | None = None
    worker_job_id: str | None = None
    worker_type: str | None = None
    execution_status: str | None = None
    outcome_status: str | None = None
    violation: ToolViolation | None = None
    error: ToolError | None = None
    artifact: ArtifactReadSummary | None = None
    results: list[AgentToolResult] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ParallelWorkerRequest:
    worker_type: str
    objective: str | None = None


class AgentToolService:
    """SDK-independent implementation behind Main Agent tools."""

    def __init__(self, context: AgentToolContext) -> None:
        self.context = context
        self.task_repository = TaskRepository(context.session)
        self.artifact_repository = ArtifactRepository(context.session)
        self.artifact_store = ArtifactStore(
            session=context.session,
            artifact_root=context.artifact_root,
        )
        self.event_service = EventService(context.session)

    def call_plc_dev(
        self,
        task_id: str,
        *,
        objective: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_dev",
            task_id=task_id,
            worker_type=WorkerType.PLC_DEV.value,
            objective=objective,
        )

    def call_plc_test(
        self,
        task_id: str,
        *,
        objective: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_test",
            task_id=task_id,
            worker_type=WorkerType.PLC_TEST.value,
            objective=objective,
        )

    def call_plc_formal(
        self,
        task_id: str,
        *,
        objective: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_formal",
            task_id=task_id,
            worker_type=WorkerType.PLC_FORMAL.value,
            objective=objective,
        )

    def call_plc_repair(
        self,
        task_id: str,
        *,
        objective: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_repair",
            task_id=task_id,
            worker_type=WorkerType.PLC_REPAIR.value,
            objective=objective,
        )

    def run_parallel_workers(
        self,
        task_id: str,
        requests: list[ParallelWorkerRequest],
    ) -> AgentToolResult:
        tool_name = "run_parallel_workers"
        if not requests:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="empty_parallel_batch",
                message="parallel worker batch must not be empty",
            )

        task = self._get_task(task_id)
        proposed_jobs: list[ProposedWorkerJob] = []
        proposed_artifacts: list[list[ArtifactRef]] = []
        for request in requests:
            artifacts = _proposed_worker_input_artifacts(task, request.worker_type)
            proposed_artifacts.append(artifacts)
            proposed_jobs.append(
                ProposedWorkerJob(
                    worker_type=request.worker_type,
                    input_artifacts=artifacts,
                )
            )

        try:
            validate_parallel_jobs(task, proposed_jobs)
        except SchedulerGuardViolation as exc:
            return self._guard_rejected_result(tool_name, task, exc)

        worker_inputs: list[WorkerInput] = []
        for request, artifacts in zip(requests, proposed_artifacts, strict=True):
            try:
                worker_inputs.append(
                    build_worker_input(
                        task,
                        request.worker_type,
                        objective=request.objective,
                        input_artifacts=artifacts,
                        trace_context=_trace_context_for_task(task),
                        metadata={"source": "main_agent_function_tools"},
                    )
                )
            except WorkerInputBuildError as exc:
                return self._rejected_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    task=task,
                    code="worker_input_build_error",
                    message=str(exc),
                    details={"worker_type": request.worker_type},
                )

        results = [
            self._dispatch_worker_input(tool_name=tool_name, worker_input=worker_input)
            for worker_input in worker_inputs
        ]
        latest = self._get_task(task_id)
        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=(
                ToolStatus.FAILED
                if any(result.status == ToolStatus.FAILED.value for result in results)
                else ToolStatus.APPLIED
            ),
            summary=f"Dispatched {len(results)} worker(s).",
            gate_state=_gate_state_summary(latest),
            failures=_failure_summaries(latest.failures),
            results=results,
        )

    def read_artifact(
        self,
        task_id: str,
        artifact_id: str,
        *,
        mode: str = "summary",
        max_chars: int | None = None,
    ) -> AgentToolResult:
        tool_name = "read_artifact"
        limit = max_chars or self.context.read_artifact_max_chars
        if limit < 1:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_max_chars",
                message="max_chars must be greater than zero",
            )

        try:
            artifact = self.artifact_repository.get_artifact(artifact_id)
        except RepositoryNotFoundError:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="artifact_not_found",
                message=f"artifact not found: {artifact_id}",
            )

        if artifact.task_id != task_id:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="foreign_artifact",
                message="artifact does not belong to requested task",
                details={"artifact_id": artifact_id, "artifact_task_id": artifact.task_id},
            )

        if mode not in {"summary", "full"}:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_read_mode",
                message="read_artifact mode must be 'summary' or 'full'",
                details={"mode": mode},
            )

        read_summary = _artifact_read_summary(artifact)
        if mode == "full":
            try:
                stored = self.artifact_store.read_artifact_content(artifact_id)
                decoded = stored.content.decode("utf-8")
            except UnicodeDecodeError:
                return self._failed_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    message=f"artifact content is not UTF-8 text: {artifact_id}",
                    error_code="artifact_not_utf8",
                )
            except (
                ArtifactStoreContentError,
                ArtifactStoreInvalidStorageError,
                ArtifactStoreUnsupportedProviderError,
            ) as exc:
                return self._failed_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    message=str(exc),
                    error_code=type(exc).__name__,
                )
            truncated = len(decoded) > limit
            read_summary = read_summary.model_copy(
                update={
                    "content": decoded[:limit],
                    "content_truncated": truncated,
                    "content_chars": min(len(decoded), limit),
                }
            )

        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=(
                "Artifact metadata read."
                if mode == "summary"
                else "Artifact content read with bounded output."
            ),
            artifact_refs=[_artifact_ref_summary_from_artifact(artifact)],
            artifact=read_summary,
        )

    def run_quality_gate(self, task_id: str) -> AgentToolResult:
        result = QualityGateService(
            session=self.context.session,
            artifact_root=self.context.artifact_root,
        ).run_quality_gate(task_id)
        failed_gates = [
            outcome.gate_type
            for outcome in result.assessment.outcomes
            if outcome.blocking
        ]
        return AgentToolResult(
            tool="run_quality_gate",
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=result.assessment.message,
            artifact_refs=[_artifact_ref_summary(result.gate_report)],
            failures=_failure_summaries(result.task.failures),
            gate_state=_gate_state_summary(result.task),
            details={
                "assessment_status": result.assessment.status,
                "blocking": result.assessment.blocking,
                "failed_gates": failed_gates,
            },
        )

    def finish_task(
        self,
        task_id: str,
        *,
        final_status: TaskStatus | str = TaskStatus.SUCCEEDED.value,
    ) -> AgentToolResult:
        tool_name = "finish_task"
        task = self._get_task(task_id)
        status_value = _value(final_status)
        if status_value not in TERMINAL_EVENT_BY_STATUS:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="unsupported_final_status",
                message=f"unsupported final status: {status_value!r}",
            )

        try:
            validate_finish_task(task, status_value)
        except SchedulerGuardViolation as exc:
            return self._guard_rejected_result(tool_name, task, exc)

        now = utc_now()
        updated = task.model_copy(
            deep=True,
            update={
                "status": status_value,
                "phase": TaskPhase.COMPLETED.value,
                "updated_at": now,
                "completed_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            _build_terminal_task_event(
                task_id=task_id,
                final_status=status_value,
                created_at=now,
            )
        )
        persisted = self._get_task(task_id)
        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Task marked {status_value}.",
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            details={"final_status": status_value},
        )

    def _call_worker_tool(
        self,
        *,
        tool_name: str,
        task_id: str,
        worker_type: str,
        objective: str | None,
    ) -> AgentToolResult:
        task = self._get_task(task_id)
        proposed_artifacts = _proposed_worker_input_artifacts(task, worker_type)
        try:
            validate_worker_call(task, worker_type, proposed_artifacts)
        except SchedulerGuardViolation as exc:
            return self._guard_rejected_result(tool_name, task, exc)

        try:
            worker_input = build_worker_input(
                task,
                worker_type,
                objective=objective,
                input_artifacts=proposed_artifacts,
                trace_context=_trace_context_for_task(task),
                metadata={"source": "main_agent_function_tools"},
            )
        except WorkerInputBuildError as exc:
            return self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="worker_input_build_error",
                message=str(exc),
                details={"worker_type": worker_type},
            )

        return self._dispatch_worker_input(tool_name=tool_name, worker_input=worker_input)

    def _dispatch_worker_input(
        self,
        *,
        tool_name: str,
        worker_input: WorkerInput,
    ) -> AgentToolResult:
        predispatch = self._record_active_worker(worker_input)
        try:
            result = McpAdapter(
                session=self.context.session,
                artifact_root=self.context.artifact_root,
                mcp_mode=self.context.mcp_mode,
                mock_scenario=self.context.mock_scenario,
            ).call_worker(worker_input)
            handled = handle_worker_result(result, session=self.context.session)
            final_task = self._decrement_active_worker_counter(worker_input.task_id)
            return _worker_result_to_tool_result(
                tool_name=tool_name,
                result=result,
                task=final_task,
                applied=handled.applied,
            )
        except Exception as exc:
            self._restore_active_worker(
                task_id=worker_input.task_id,
                worker_job_id=worker_input.worker_job_id,
                previous_task=predispatch,
            )
            return self._failed_result(
                tool_name=tool_name,
                task_id=worker_input.task_id,
                message=str(exc),
                error_code=type(exc).__name__,
            )

    def _record_active_worker(self, worker_input: WorkerInput) -> TaskState:
        task = self._get_task(worker_input.task_id)
        active_jobs = [
            job
            for job in task.active_worker_jobs
            if job.worker_job_id != worker_input.worker_job_id
        ]
        active_jobs.append(
            WorkerJobRef(
                worker_job_id=worker_input.worker_job_id,
                worker_type=worker_input.worker_type,
                status=WorkerJobStatus.RUNNING,
                objective=worker_input.objective,
                started_at=worker_input.created_at,
            )
        )
        updated = task.model_copy(
            deep=True,
            update={
                "active_worker_jobs": active_jobs,
                "runtime_limits": task.runtime_limits.model_copy(
                    update={
                        "active_parallel_workers": (
                            task.runtime_limits.active_parallel_workers + 1
                        ),
                        "worker_calls_used": task.runtime_limits.worker_calls_used + 1,
                    }
                ),
                "updated_at": worker_input.created_at,
            },
        )
        self.task_repository.update_task_state(updated)
        return task

    def _decrement_active_worker_counter(self, task_id: str) -> TaskState:
        task = self._get_task(task_id)
        active_workers = max(task.runtime_limits.active_parallel_workers - 1, 0)
        updated = task.model_copy(
            deep=True,
            update={
                "runtime_limits": task.runtime_limits.model_copy(
                    update={"active_parallel_workers": active_workers}
                )
            },
        )
        return self.task_repository.update_task_state(updated)

    def _restore_active_worker(
        self,
        *,
        task_id: str,
        worker_job_id: str,
        previous_task: TaskState,
    ) -> TaskState:
        current = self._get_task(task_id)
        restored_jobs = [
            job
            for job in previous_task.active_worker_jobs
            if job.worker_job_id != worker_job_id
        ]
        updated = current.model_copy(
            deep=True,
            update={
                "active_worker_jobs": restored_jobs,
                "runtime_limits": current.runtime_limits.model_copy(
                    update={
                        "active_parallel_workers": (
                            previous_task.runtime_limits.active_parallel_workers
                        ),
                        "worker_calls_used": previous_task.runtime_limits.worker_calls_used,
                    }
                ),
            },
        )
        return self.task_repository.update_task_state(updated)

    def _get_task(self, task_id: str) -> TaskState:
        return self.task_repository.get_task(task_id)

    def _guard_rejected_result(
        self,
        tool_name: str,
        task: TaskState,
        violation: SchedulerGuardViolation,
    ) -> AgentToolResult:
        return self._rejected_result(
            tool_name=tool_name,
            task_id=task.task_id,
            task=task,
            code=_value(violation.code),
            message=violation.message,
            details=violation.details,
        )

    def _rejected_result(
        self,
        *,
        tool_name: str,
        task_id: str | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        task: TaskState | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.REJECTED,
            summary=message,
            failures=_failure_summaries(task.failures) if task is not None else [],
            gate_state=_gate_state_summary(task) if task is not None else None,
            violation=ToolViolation(
                code=code,
                message=message,
                details=dict(details or {}),
            ),
        )

    def _failed_result(
        self,
        *,
        tool_name: str,
        task_id: str | None,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.FAILED,
            summary=message,
            error=ToolError(
                error_code=error_code,
                message=message,
                retryable=False,
                details=dict(details or {}),
            ),
        )


@function_tool
def call_plc_dev(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
) -> AgentToolResult:
    """Generate or update PLC implementation artifacts for a classified task."""

    return AgentToolService(ctx.context).call_plc_dev(
        task_id=task_id,
        objective=objective,
    )


@function_tool
def call_plc_test(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
) -> AgentToolResult:
    """Run PLC test worker for the task's current code and requirements."""

    return AgentToolService(ctx.context).call_plc_test(
        task_id=task_id,
        objective=objective,
    )


@function_tool
def call_plc_formal(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
) -> AgentToolResult:
    """Run formal verification worker for the current PLC code."""

    return AgentToolService(ctx.context).call_plc_formal(
        task_id=task_id,
        objective=objective,
    )


@function_tool
def call_plc_repair(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
) -> AgentToolResult:
    """Run PLC repair worker using current code and latest failure evidence."""

    return AgentToolService(ctx.context).call_plc_repair(
        task_id=task_id,
        objective=objective,
    )


@function_tool
def run_parallel_workers(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    workers: list[str],
    objectives: list[str] | None = None,
) -> AgentToolResult:
    """Dispatch a guarded parallel batch of non-repair PLC workers."""

    requests = [
        ParallelWorkerRequest(
            worker_type=worker,
            objective=objectives[index] if objectives and index < len(objectives) else None,
        )
        for index, worker in enumerate(workers)
    ]
    return AgentToolService(ctx.context).run_parallel_workers(
        task_id=task_id,
        requests=requests,
    )


@function_tool
def read_artifact(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    artifact_id: str,
    mode: str = "summary",
    max_chars: int | None = None,
) -> AgentToolResult:
    """Read artifact metadata or bounded UTF-8 content for one task artifact."""

    return AgentToolService(ctx.context).read_artifact(
        task_id=task_id,
        artifact_id=artifact_id,
        mode=mode,
        max_chars=max_chars,
    )


@function_tool
def run_quality_gate(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
) -> AgentToolResult:
    """Run and persist Quality Gate assessment for a task."""

    return AgentToolService(ctx.context).run_quality_gate(task_id=task_id)


@function_tool
def finish_task(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    final_status: str = TaskStatus.SUCCEEDED.value,
) -> AgentToolResult:
    """Finish a task through guarded terminal state transition."""

    return AgentToolService(ctx.context).finish_task(
        task_id=task_id,
        final_status=final_status,
    )


def get_main_agent_tools() -> list[Any]:
    """Return SDK function tools for Main Agent registration."""

    return [
        call_plc_dev,
        call_plc_test,
        call_plc_formal,
        call_plc_repair,
        run_parallel_workers,
        read_artifact,
        run_quality_gate,
        finish_task,
    ]


def _proposed_worker_input_artifacts(
    task: TaskState,
    worker_type: WorkerType | str,
) -> list[ArtifactRef]:
    worker = _value(worker_type)
    artifacts = task.current_artifacts
    if worker == WorkerType.PLC_DEV.value:
        return [
            artifact
            for artifact in (artifacts.raw_user_request, artifacts.requirements_ir)
            if artifact is not None
        ][:1]
    if worker in {WorkerType.PLC_TEST.value, WorkerType.PLC_FORMAL.value}:
        return [
            artifact
            for artifact in (artifacts.requirements_ir, artifacts.current_code)
            if artifact is not None
        ]
    if worker == WorkerType.PLC_REPAIR.value:
        return [
            artifact
            for artifact in (
                artifacts.current_code,
                artifacts.latest_test_report,
                artifacts.latest_failing_trace,
                artifacts.latest_formal_report,
                artifacts.latest_counterexample,
            )
            if artifact is not None
        ]
    return []


def _worker_result_to_tool_result(
    *,
    tool_name: str,
    result: WorkerResult,
    task: TaskState,
    applied: bool,
) -> AgentToolResult:
    status = ToolStatus.APPLIED if applied else ToolStatus.NOOP
    return AgentToolResult(
        tool=tool_name,
        task_id=result.task_id,
        status=status,
        summary=result.summary,
        artifact_refs=_artifact_ref_summaries(result.produced_artifacts),
        failures=_failure_summaries(task.failures),
        gate_state=_gate_state_summary(task),
        next_recommended_action=_value(result.next_recommended_action),
        worker_job_id=result.worker_job_id,
        worker_type=_value(result.worker_type),
        execution_status=_value(result.execution_status),
        outcome_status=_value(result.outcome.status),
        error=(
            ToolError(
                error_code=result.error.error_code,
                message=result.error.message,
                retryable=result.error.retryable,
                details=dict(result.error.details or {}),
            )
            if result.error is not None
            else None
        ),
    )


def _artifact_ref_summaries(
    artifacts: list[ArtifactRef],
) -> list[ArtifactRefSummary]:
    return [_artifact_ref_summary(artifact) for artifact in artifacts]


def _artifact_ref_summary(artifact: ArtifactRef) -> ArtifactRefSummary:
    return ArtifactRefSummary(
        artifact_id=artifact.artifact_id,
        type=_value(artifact.type),
        version=artifact.version,
        uri=artifact.uri,
        summary=artifact.summary,
        content_hash=artifact.content_hash,
    )


def _artifact_ref_summary_from_artifact(artifact: Artifact) -> ArtifactRefSummary:
    return ArtifactRefSummary(
        artifact_id=artifact.artifact_id,
        type=_value(artifact.type),
        version=artifact.version,
        uri=artifact.storage.uri,
        summary=artifact.summary,
        content_hash=artifact.storage.content_hash,
    )


def _artifact_read_summary(artifact: Artifact) -> ArtifactReadSummary:
    return ArtifactReadSummary(
        artifact_id=artifact.artifact_id,
        task_id=artifact.task_id,
        type=_value(artifact.type),
        version=artifact.version,
        name=artifact.name,
        summary=artifact.summary,
        uri=artifact.storage.uri,
        mime_type=artifact.storage.mime_type,
        size_bytes=artifact.storage.size_bytes,
        content_hash=artifact.storage.content_hash,
    )


def _failure_summaries(failures: list[Failure]) -> list[FailureSummary]:
    return [
        FailureSummary(
            failure_id=failure.failure_id,
            source=_value(failure.source),
            severity=_value(failure.severity),
            status=_value(failure.status),
            title=failure.title,
            evidence_artifact_ids=list(failure.evidence_artifact_ids),
        )
        for failure in failures
    ]


def _gate_state_summary(task: TaskState) -> GateStateSummary:
    gates = task.gates
    return GateStateSummary(
        test_required=gates.test_required,
        formal_required=gates.formal_required,
        regression_required=gates.regression_required,
        formal_regression_required=gates.formal_regression_required,
        latest_test_passed=gates.latest_test_passed,
        latest_formal_passed=gates.latest_formal_passed,
        has_blocking_failure=gates.has_blocking_failure,
        can_finish_as_success=gates.can_finish_as_success,
    )


def _trace_context_for_task(task: TaskState) -> TraceContext:
    return TraceContext(
        openai_trace_id=task.trace.openai_trace_id,
        main_agent_run_id=task.trace.latest_main_agent_run_id,
    )


def _build_terminal_task_event(
    *,
    task_id: str,
    final_status: str,
    created_at: Any,
) -> RouterEvent:
    event_type = TERMINAL_EVENT_BY_STATUS[final_status]
    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.RUNTIME),
        severity=(
            EventSeverity.INFO
            if final_status == TaskStatus.SUCCEEDED.value
            else EventSeverity.ERROR
        ),
        visibility=EventVisibility.USER,
        title=f"Task {final_status}",
        message=f"The task was marked {final_status}.",
        correlation=EventCorrelation(),
        payload={"task_id": task_id, "status": final_status},
        created_at=created_at,
    )


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
