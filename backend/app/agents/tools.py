"""Main Agent function tools backed by deterministic Router runtime services."""

from __future__ import annotations

from collections.abc import Callable
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
from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.output_schema import (
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
    MainAgentPlanStep,
)
from app.core.ids import new_event_id, prefixed_id
from app.core.time import utc_now
from app.mcp.adapter import McpAdapter
from app.mcp.mock_worker import DEFAULT_MOCK_SCENARIO
from app.models.router_schema import (
    Artifact,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    ClarificationQuestion,
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
    TaskType,
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
CheckpointCallback = Callable[[], None]
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
    report_first_finalization: bool = False
    checkpoint: CheckpointCallback | None = None
    observability_recorder: Any | None = None


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

    def update_plan(
        self,
        task_id: str,
        *,
        summary: str,
        plan: list[dict[str, Any]] | None = None,
        normalized_goal: str | None = None,
        task_type: str | None = None,
        requires_test: bool | None = None,
        requires_formal: bool | None = None,
    ) -> AgentToolResult:
        tool_name = "update_plan"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=summary,
            arguments={
                "task_id": task_id,
                "summary": summary,
                "plan": plan or [],
                "normalized_goal": normalized_goal,
                "task_type": task_type,
                "requires_test": requires_test,
                "requires_formal": requires_formal,
            },
        )
        task = self._get_task(task_id)
        if _value(task.status) in TERMINAL_EVENT_BY_STATUS:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="terminal_task",
                message=f"cannot update plan for terminal task: {task_id}",
            )
            self._record_tool_result(tool_name, result)
            return result

        now = utc_now()
        selected_task_type = _normalized_task_type_from_tool(
            task_type,
            current_task_type=_value(task.task_type),
        )
        difficulty = task.difficulty.model_copy(
            update={
                "requires_test": (
                    requires_test
                    if requires_test is not None
                    else task.difficulty.requires_test
                ),
                "requires_formal": (
                    requires_formal
                    if requires_formal is not None
                    else task.difficulty.requires_formal
                ),
                "need_clarification": False,
            }
        )
        gates = task.gates.model_copy(
            update={
                "test_required": (
                    requires_test
                    if requires_test is not None
                    else task.gates.test_required
                ),
                "formal_required": (
                    requires_formal
                    if requires_formal is not None
                    else task.gates.formal_required
                ),
                "can_finish_as_success": False,
            }
        )
        updated = task.model_copy(
            deep=True,
            update={
                "normalized_goal": normalized_goal or task.normalized_goal or task.raw_user_request,
                "task_type": selected_task_type,
                "difficulty": difficulty,
                "gates": gates,
                "status": TaskStatus.RUNNING.value,
                "phase": TaskPhase.PLANNING.value,
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            _build_main_agent_event(
                task=updated,
                event_type=EventType.MAIN_AGENT_PLAN_UPDATED,
                title="Main Agent plan updated",
                message=summary,
                payload={
                    "task_id": task_id,
                    "summary": summary,
                    "plan": plan or [],
                },
                created_at=now,
            )
        )
        self._checkpoint()
        persisted = self._get_task(task_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=summary,
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            details={"plan": plan or []},
        )
        self._record_tool_result(tool_name, result)
        return result

    def request_clarification(
        self,
        task_id: str,
        *,
        questions: list[dict[str, Any]] | list[str],
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        tool_name = "request_clarification"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={"task_id": task_id, "questions": questions},
        )
        task = self._get_task(task_id)
        if not questions:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="missing_clarification_questions",
                message="request_clarification requires at least one question",
            )
            self._record_tool_result(tool_name, result)
            return result
        now = utc_now()
        clarification_questions = [
            _clarification_question_from_tool(item, now=now)
            for item in questions
        ]
        updated = task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.WAITING_USER.value,
                "phase": TaskPhase.CLARIFYING.value,
                "difficulty": task.difficulty.model_copy(
                    update={"need_clarification": True}
                ),
                "unresolved_questions": [
                    *task.unresolved_questions,
                    *clarification_questions,
                ],
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        question_ids = [question.question_id for question in clarification_questions]
        self.event_service.append_event(
            _build_main_agent_event(
                task=updated,
                event_type=EventType.MAIN_AGENT_CLARIFICATION_REQUESTED,
                title="Main Agent requested clarification",
                message=rationale_summary or "Main Agent paused for user clarification.",
                payload={"task_id": task_id, "question_ids": question_ids},
                created_at=now,
            )
        )
        self.event_service.append_event(
            _build_task_event(
                task=updated,
                event_type=EventType.TASK_WAITING_USER,
                title="Task waiting for user",
                message="The task needs user clarification before workers can run.",
                payload={
                    "task_id": task_id,
                    "status": TaskStatus.WAITING_USER.value,
                    "phase": TaskPhase.CLARIFYING.value,
                    "question_ids": question_ids,
                },
                created_at=now,
            )
        )
        self._checkpoint()
        persisted = self._get_task(task_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary="Task paused for user clarification.",
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            next_recommended_action="ask_user",
            details={"question_ids": question_ids},
        )
        self._record_tool_result(tool_name, result)
        return result

    def write_final_report(
        self,
        task_id: str,
        *,
        final_status: str,
        summary: str,
        rationale_summary: str | None = None,
        decisions: list[dict[str, Any]] | None = None,
        plan: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        tool_name = "write_final_report"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary or summary,
            arguments={
                "task_id": task_id,
                "final_status": final_status,
                "summary": summary,
                "decisions": decisions or [],
                "plan": plan or [],
                "metadata": metadata or {},
            },
        )
        task = self._get_task(task_id)
        output = MainAgentEpisodeOutput(
            task_id=task_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            final_task_status=final_status,
            phase=_value(task.phase),
            decisions=_main_agent_decisions_from_tool(decisions),
            plan=_main_agent_plan_from_tool(plan),
            artifact_refs=_main_agent_artifact_refs(task),
            gate_summary=MainAgentGateSummary.model_validate(
                task.gates.model_dump(mode="json")
            ),
            open_clarification_question_ids=[
                question.question_id
                for question in task.unresolved_questions
                if _value(question.status) == "open"
            ],
            summary=summary,
            metadata=metadata or {},
        )
        recorder = self.context.observability_recorder or MainAgentObservabilityRecorder(
            session=self.context.session,
            artifact_root=self.context.artifact_root,
            task_id=task_id,
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
            checkpoint=self.context.checkpoint,
        )
        final_report = recorder.write_final_report(output)
        replay_log = recorder.write_replay_log(final_output=output)
        recorder.record_completed(
            output=output,
            final_report=final_report,
            replay_log=replay_log,
        )
        self._checkpoint()
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary="Final report written.",
            artifact_refs=[
                _artifact_ref_summary(final_report),
                _artifact_ref_summary(replay_log),
            ],
            failures=_failure_summaries(task.failures),
            gate_state=_gate_state_summary(task),
            details={
                "final_status": final_status,
                "final_report_artifact_id": final_report.artifact_id,
                "main_agent_log_artifact_id": replay_log.artifact_id,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def call_plc_dev(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_dev",
            task_id=task_id,
            worker_type=WorkerType.PLC_DEV.value,
            objective=objective,
            rationale_summary=rationale_summary,
        )

    def call_plc_test(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_test",
            task_id=task_id,
            worker_type=WorkerType.PLC_TEST.value,
            objective=objective,
            rationale_summary=rationale_summary,
        )

    def call_plc_formal(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_formal",
            task_id=task_id,
            worker_type=WorkerType.PLC_FORMAL.value,
            objective=objective,
            rationale_summary=rationale_summary,
        )

    def call_plc_repair(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_repair",
            task_id=task_id,
            worker_type=WorkerType.PLC_REPAIR.value,
            objective=objective,
            rationale_summary=rationale_summary,
        )

    def run_parallel_workers(
        self,
        task_id: str,
        requests: list[ParallelWorkerRequest],
        *,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        tool_name = "run_parallel_workers"
        if not requests:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="empty_parallel_batch",
                message="parallel worker batch must not be empty",
            )
            self._record_tool_result(tool_name, result)
            return result

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
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={
                "task_id": task_id,
                "workers": [request.worker_type for request in requests],
                "objectives": [request.objective for request in requests],
            },
            input_artifacts=[
                artifact
                for artifacts in proposed_artifacts
                for artifact in artifacts
            ],
        )

        try:
            validate_parallel_jobs(task, proposed_jobs)
        except SchedulerGuardViolation as exc:
            result = self._guard_rejected_result(tool_name, task, exc)
            self._record_tool_result(tool_name, result)
            return result

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
                result = self._rejected_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    task=task,
                    code="worker_input_build_error",
                    message=str(exc),
                    details={"worker_type": request.worker_type},
                )
                self._record_tool_result(tool_name, result)
                return result

        results = [
            self._dispatch_worker_input(tool_name=tool_name, worker_input=worker_input)
            for worker_input in worker_inputs
        ]
        latest = self._get_task(task_id)
        result = AgentToolResult(
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
        self._record_tool_result(tool_name, result)
        return result

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

    def run_quality_gate(
        self,
        task_id: str,
        *,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        self._record_tool_call(
            tool_name="run_quality_gate",
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={"task_id": task_id},
        )
        result = QualityGateService(
            session=self.context.session,
            artifact_root=self.context.artifact_root,
        ).run_quality_gate(task_id)
        self._checkpoint()
        failed_gates = [
            outcome.gate_type
            for outcome in result.assessment.outcomes
            if outcome.blocking
        ]
        tool_result = AgentToolResult(
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
        self._record_tool_result("run_quality_gate", tool_result)
        return tool_result

    def finish_task(
        self,
        task_id: str,
        *,
        final_status: TaskStatus | str = TaskStatus.SUCCEEDED.value,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        tool_name = "finish_task"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={"task_id": task_id, "final_status": _value(final_status)},
        )
        task = self._get_task(task_id)
        status_value = _value(final_status)
        if _value(task.status) in TERMINAL_EVENT_BY_STATUS:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="terminal_task",
                message=f"cannot finish terminal task: {task_id}",
                details={"status": _value(task.status)},
            )
            self._record_tool_result(tool_name, result)
            return result
        if status_value not in TERMINAL_EVENT_BY_STATUS:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="unsupported_final_status",
                message=f"unsupported final status: {status_value!r}",
            )
            self._record_tool_result(tool_name, result)
            return result

        try:
            validate_finish_task(task, status_value)
        except SchedulerGuardViolation as exc:
            result = self._guard_rejected_result(tool_name, task, exc)
            self._record_tool_result(tool_name, result)
            return result

        if not self._has_final_report_artifact(task_id):
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="final_report_required",
                message="finish_task requires a durable final_report artifact",
                details={"final_status": status_value},
            )
            self._record_tool_result(tool_name, result)
            return result

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
                openai_trace_id=updated.trace.openai_trace_id,
                main_agent_run_id=updated.trace.latest_main_agent_run_id,
                created_at=now,
            )
        )
        self._checkpoint()
        persisted = self._get_task(task_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Task marked {status_value}.",
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            details={"final_status": status_value},
        )
        self._record_tool_result(tool_name, result)
        return result

    def _has_final_report_artifact(self, task_id: str) -> bool:
        return any(
            _value(artifact.type) == ArtifactType.FINAL_REPORT.value
            for artifact in self.artifact_repository.list_task_artifacts(task_id)
        )

    def _call_worker_tool(
        self,
        *,
        tool_name: str,
        task_id: str,
        worker_type: str,
        objective: str | None,
        rationale_summary: str | None,
    ) -> AgentToolResult:
        task = self._get_task(task_id)
        proposed_artifacts = _proposed_worker_input_artifacts(task, worker_type)
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={
                "task_id": task_id,
                "worker_type": worker_type,
                "objective": objective,
            },
            input_artifacts=proposed_artifacts,
        )
        try:
            validate_worker_call(task, worker_type, proposed_artifacts)
        except SchedulerGuardViolation as exc:
            result = self._guard_rejected_result(tool_name, task, exc)
            self._record_tool_result(tool_name, result)
            return result

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
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="worker_input_build_error",
                message=str(exc),
                details={"worker_type": worker_type},
            )
            self._record_tool_result(tool_name, result)
            return result

        result = self._dispatch_worker_input(
            tool_name=tool_name,
            worker_input=worker_input,
        )
        self._record_tool_result(tool_name, result)
        return result

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
                checkpoint=self.context.checkpoint,
            ).call_worker(worker_input)
            handled = handle_worker_result(result, session=self.context.session)
            final_task = self._decrement_active_worker_counter(worker_input.task_id)
            self._checkpoint()
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
            self._checkpoint()
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
        if self.context.checkpoint is not None:
            self.context.session.expire_all()
        return self.task_repository.get_task(task_id)

    def _checkpoint(self) -> None:
        if self.context.checkpoint is not None:
            self.context.checkpoint()

    def _record_tool_call(
        self,
        *,
        tool_name: str,
        task_id: str,
        rationale_summary: str | None,
        arguments: dict[str, Any],
        input_artifacts: list[ArtifactRef] | None = None,
    ) -> None:
        recorder = self.context.observability_recorder
        if recorder is None:
            return
        recorder.record_tool_call(
            tool_name=tool_name,
            rationale_summary=rationale_summary,
            arguments=arguments,
            input_artifact_ids=[
                artifact.artifact_id for artifact in input_artifacts or []
            ],
        )

    def _record_tool_result(self, tool_name: str, result: AgentToolResult) -> None:
        recorder = self.context.observability_recorder
        if recorder is None:
            return
        recorder.record_tool_result(tool_name=tool_name, result=result)

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


@function_tool(strict_mode=False)
def update_plan(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    summary: str,
    plan: list[dict[str, Any]] | None = None,
    normalized_goal: str | None = None,
    task_type: str | None = None,
    requires_test: bool | None = None,
    requires_formal: bool | None = None,
) -> AgentToolResult:
    """Persist a public Main Agent plan and move the task into planning."""

    return AgentToolService(ctx.context).update_plan(
        task_id=task_id,
        summary=summary,
        plan=plan,
        normalized_goal=normalized_goal,
        task_type=task_type,
        requires_test=requires_test,
        requires_formal=requires_formal,
    )


@function_tool(strict_mode=False)
def request_clarification(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    questions: list[dict[str, Any]] | list[str],
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Pause a task and persist user clarification questions."""

    return AgentToolService(ctx.context).request_clarification(
        task_id=task_id,
        questions=questions,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def write_final_report(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    final_status: str,
    summary: str,
    rationale_summary: str | None = None,
    decisions: list[dict[str, Any]] | None = None,
    plan: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentToolResult:
    """Write final report and replay artifacts before terminal status."""

    return AgentToolService(ctx.context).write_final_report(
        task_id=task_id,
        final_status=final_status,
        summary=summary,
        rationale_summary=rationale_summary,
        decisions=decisions,
        plan=plan,
        metadata=metadata,
    )


@function_tool(strict_mode=False)
def call_plc_dev(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Generate or update PLC implementation artifacts for a classified task."""

    return AgentToolService(ctx.context).call_plc_dev(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def call_plc_test(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Run PLC test worker for the task's current code and requirements."""

    return AgentToolService(ctx.context).call_plc_test(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def call_plc_formal(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Run formal verification worker for the current PLC code."""

    return AgentToolService(ctx.context).call_plc_formal(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def call_plc_repair(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Run PLC repair worker using current code and latest failure evidence."""

    return AgentToolService(ctx.context).call_plc_repair(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def run_parallel_workers(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    workers: list[str],
    objectives: list[str] | None = None,
    rationale_summary: str | None = None,
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
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
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


@function_tool(strict_mode=False)
def run_quality_gate(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Run and persist Quality Gate assessment for a task."""

    return AgentToolService(ctx.context).run_quality_gate(
        task_id=task_id,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def finish_task(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    final_status: str = TaskStatus.SUCCEEDED.value,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Finish a task through guarded terminal state transition."""

    return AgentToolService(ctx.context).finish_task(
        task_id=task_id,
        final_status=final_status,
        rationale_summary=rationale_summary,
    )


def get_main_agent_tools() -> list[Any]:
    """Return SDK function tools for Main Agent registration."""

    return [
        update_plan,
        request_clarification,
        call_plc_dev,
        call_plc_test,
        call_plc_formal,
        call_plc_repair,
        run_parallel_workers,
        read_artifact,
        run_quality_gate,
        write_final_report,
        finish_task,
    ]


def get_main_agent_tool_specs() -> list[dict[str, Any]]:
    """Return OpenAI-compatible Chat Completions tool definitions."""

    return [
        _tool_spec(
            "update_plan",
            "Persist a public execution plan and move the task into planning.",
            {
                "task_id": {"type": "string"},
                "summary": {"type": "string"},
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "normalized_goal": {"type": "string"},
                "task_type": {
                    "type": "string",
                    "enum": [
                        TaskType.QA.value,
                        TaskType.NEW_PLC_DEVELOPMENT.value,
                        TaskType.MODIFY_EXISTING_CODE.value,
                        TaskType.TEST_EXISTING_CODE.value,
                        TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
                        TaskType.REPAIR_EXISTING_CODE.value,
                        TaskType.PROJECT_LEVEL_DEVELOPMENT.value,
                    ],
                },
                "requires_test": {"type": "boolean"},
                "requires_formal": {"type": "boolean"},
            },
            ["task_id", "summary"],
        ),
        _tool_spec(
            "request_clarification",
            "Persist required user clarification questions and pause the task.",
            {
                "task_id": {"type": "string"},
                "questions": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string"},
                                    "reason": {"type": "string"},
                                    "required": {"type": "boolean"},
                                },
                                "required": ["question"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                },
                "rationale_summary": {"type": "string"},
            },
            ["task_id", "questions"],
        ),
        _worker_tool_spec("call_plc_dev", "Generate or update PLC artifacts."),
        _worker_tool_spec("call_plc_test", "Run PLC tests for current code."),
        _worker_tool_spec("call_plc_formal", "Run formal verification for current code."),
        _worker_tool_spec("call_plc_repair", "Repair current code using failure evidence."),
        _tool_spec(
            "run_parallel_workers",
            "Dispatch a guarded batch of non-repair PLC workers.",
            {
                "task_id": {"type": "string"},
                "workers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "objectives": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rationale_summary": {"type": "string"},
            },
            ["task_id", "workers"],
        ),
        _tool_spec(
            "read_artifact",
            "Read artifact metadata or bounded UTF-8 content.",
            {
                "task_id": {"type": "string"},
                "artifact_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["summary", "full"]},
                "max_chars": {"type": "integer", "minimum": 1},
            },
            ["task_id", "artifact_id"],
        ),
        _tool_spec(
            "run_quality_gate",
            "Run and persist Quality Gate assessment.",
            {
                "task_id": {"type": "string"},
                "rationale_summary": {"type": "string"},
            },
            ["task_id"],
        ),
        _tool_spec(
            "write_final_report",
            "Write final report and Main Agent replay artifacts.",
            {
                "task_id": {"type": "string"},
                "final_status": {"type": "string"},
                "summary": {"type": "string"},
                "rationale_summary": {"type": "string"},
                "decisions": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "plan": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "metadata": {"type": "object", "additionalProperties": True},
            },
            ["task_id", "final_status", "summary"],
        ),
        _tool_spec(
            "finish_task",
            "Apply terminal task status after report and guard checks.",
            {
                "task_id": {"type": "string"},
                "final_status": {"type": "string"},
                "rationale_summary": {"type": "string"},
            },
            ["task_id"],
        ),
    ]


def call_main_agent_tool(
    context: AgentToolContext,
    tool_name: str,
    arguments: dict[str, Any],
) -> AgentToolResult:
    """Invoke a Main Agent tool by Chat Completions tool-call name."""

    service = AgentToolService(context)
    tool_arguments = dict(arguments)
    if tool_name == "update_plan":
        return service.update_plan(**tool_arguments)
    if tool_name == "request_clarification":
        return service.request_clarification(**tool_arguments)
    if tool_name == "call_plc_dev":
        return service.call_plc_dev(**tool_arguments)
    if tool_name == "call_plc_test":
        return service.call_plc_test(**tool_arguments)
    if tool_name == "call_plc_formal":
        return service.call_plc_formal(**tool_arguments)
    if tool_name == "call_plc_repair":
        return service.call_plc_repair(**tool_arguments)
    if tool_name == "run_parallel_workers":
        workers = tool_arguments.pop("workers")
        objectives = tool_arguments.pop("objectives", None)
        return service.run_parallel_workers(
            requests=[
                ParallelWorkerRequest(
                    worker_type=worker,
                    objective=objectives[index] if objectives and index < len(objectives) else None,
                )
                for index, worker in enumerate(workers)
            ],
            **tool_arguments,
        )
    if tool_name == "read_artifact":
        return service.read_artifact(**tool_arguments)
    if tool_name == "run_quality_gate":
        return service.run_quality_gate(**tool_arguments)
    if tool_name == "write_final_report":
        return service.write_final_report(**tool_arguments)
    if tool_name == "finish_task":
        return service.finish_task(**tool_arguments)
    return AgentToolResult(
        tool=tool_name,
        status=ToolStatus.REJECTED,
        summary=f"Unknown Main Agent tool: {tool_name}",
        violation=ToolViolation(
            code="unknown_tool",
            message=f"Unknown Main Agent tool: {tool_name}",
        ),
    )


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


def _tool_spec(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _worker_tool_spec(name: str, description: str) -> dict[str, Any]:
    return _tool_spec(
        name,
        description,
        {
            "task_id": {"type": "string"},
            "objective": {"type": "string"},
            "rationale_summary": {"type": "string"},
        },
        ["task_id"],
    )


def _clarification_question_from_tool(
    value: dict[str, Any] | str,
    *,
    now: Any,
) -> ClarificationQuestion:
    if isinstance(value, str):
        question = value
        reason = "Main Agent requested clarification before continuing."
        required = True
    else:
        question = str(value.get("question") or "").strip()
        reason = str(
            value.get("reason")
            or "Main Agent requested clarification before continuing."
        )
        required = bool(value.get("required", True))
    return ClarificationQuestion(
        question_id=prefixed_id("question"),
        question=question,
        reason=reason,
        required=required,
        status="open",
        asked_at=now,
    )


def _normalized_task_type_from_tool(
    value: str | None,
    *,
    current_task_type: str,
) -> str:
    allowed = {item.value for item in TaskType}
    if value in allowed and value != TaskType.UNKNOWN.value:
        return str(value)

    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "qa": TaskType.QA.value,
        "question_answering": TaskType.QA.value,
        "analysis": TaskType.QA.value,
        "development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "plc_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "new_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l0_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l1_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l2_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l3_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l4_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "modify": TaskType.MODIFY_EXISTING_CODE.value,
        "modification": TaskType.MODIFY_EXISTING_CODE.value,
        "test": TaskType.TEST_EXISTING_CODE.value,
        "testing": TaskType.TEST_EXISTING_CODE.value,
        "formal": TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
        "formal_verification": TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
        "repair": TaskType.REPAIR_EXISTING_CODE.value,
        "fix": TaskType.REPAIR_EXISTING_CODE.value,
        "project": TaskType.PROJECT_LEVEL_DEVELOPMENT.value,
    }
    if normalized in aliases:
        return aliases[normalized]
    if "repair" in normalized or "fix" in normalized:
        return TaskType.REPAIR_EXISTING_CODE.value
    if "formal" in normalized:
        return TaskType.FORMAL_VERIFY_EXISTING_CODE.value
    if "test" in normalized:
        return TaskType.TEST_EXISTING_CODE.value
    if "modify" in normalized or "change" in normalized:
        return TaskType.MODIFY_EXISTING_CODE.value
    if "develop" in normalized or "plc" in normalized:
        return TaskType.NEW_PLC_DEVELOPMENT.value
    if current_task_type in allowed and current_task_type != TaskType.UNKNOWN.value:
        return current_task_type
    return TaskType.NEW_PLC_DEVELOPMENT.value


def _main_agent_artifact_refs(task: TaskState) -> list[MainAgentArtifactReference]:
    refs = [
        artifact
        for artifact in (
            task.current_artifacts.raw_user_request,
            task.current_artifacts.requirements_ir,
            task.current_artifacts.current_code,
            task.current_artifacts.current_io_contract,
            task.current_artifacts.latest_test_cases,
            task.current_artifacts.latest_test_report,
            task.current_artifacts.latest_failing_trace,
            task.current_artifacts.latest_formal_properties,
            task.current_artifacts.latest_formal_report,
            task.current_artifacts.latest_counterexample,
            task.current_artifacts.latest_patch,
            task.current_artifacts.latest_repair_summary,
            task.current_artifacts.latest_gate_report,
        )
        if artifact is not None
    ]
    seen: set[str] = set()
    output: list[MainAgentArtifactReference] = []
    for ref in refs:
        if ref.artifact_id in seen:
            continue
        seen.add(ref.artifact_id)
        output.append(
            MainAgentArtifactReference(
                artifact_id=ref.artifact_id,
                type=_value(ref.type),
                version=ref.version,
                uri=ref.uri,
                summary=ref.summary,
                content_hash=ref.content_hash,
            )
        )
    return output


def _main_agent_decisions_from_tool(
    decisions: list[dict[str, Any]] | None,
) -> list[MainAgentDecision]:
    output: list[MainAgentDecision] = []
    for index, decision in enumerate(decisions or [], start=1):
        if not isinstance(decision, dict):
            decision = {"summary": str(decision)}
        normalized = {
            "decision_type": str(
                decision.get("decision_type")
                or decision.get("type")
                or "tool_loop_decision"
            ),
            "summary": str(
                decision.get("summary")
                or decision.get("message")
                or decision.get("action")
                or f"Decision {index}"
            ),
            "action": decision.get("action"),
            "tool_name": decision.get("tool_name") or decision.get("tool"),
            "artifact_refs": decision.get("artifact_refs") or [],
            "details": _json_object(decision.get("details") or {}),
        }
        output.append(MainAgentDecision.model_validate(normalized))
    return output


def _main_agent_plan_from_tool(
    plan: list[dict[str, Any]] | None,
) -> list[MainAgentPlanStep]:
    output: list[MainAgentPlanStep] = []
    for index, step in enumerate(plan or [], start=1):
        if not isinstance(step, dict):
            step = {"action": str(step)}
        normalized = {
            "order": step.get("order") or index,
            "action": str(
                step.get("action")
                or step.get("summary")
                or step.get("title")
                or f"Plan step {index}"
            ),
            "status": str(step.get("status") or "planned"),
            "reason": step.get("reason"),
            "worker_type": step.get("worker_type"),
            "tool_name": step.get("tool_name") or step.get("tool"),
        }
        output.append(MainAgentPlanStep.model_validate(normalized))
    return output


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": _json_value(value)}
    return {str(key): _json_value(item) for key, item in value.items()}


def _build_main_agent_event(
    *,
    task: TaskState,
    event_type: EventType,
    title: str,
    message: str,
    payload: dict[str, Any],
    created_at: Any,
) -> RouterEvent:
    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task.task_id,
        seq=0,
        type=event_type,
        source=EventSource(
            type=EventSourceType.MAIN_AGENT,
            id=task.trace.latest_main_agent_run_id,
        ),
        severity=EventSeverity.INFO,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
        ),
        payload=payload,
        created_at=created_at,
    )


def _build_task_event(
    *,
    task: TaskState,
    event_type: EventType,
    title: str,
    message: str,
    payload: dict[str, Any],
    created_at: Any,
) -> RouterEvent:
    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task.task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.RUNTIME),
        severity=EventSeverity.INFO,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
        ),
        payload=payload,
        created_at=created_at,
    )


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
    openai_trace_id: str | None = None,
    main_agent_run_id: str | None = None,
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
        correlation=EventCorrelation(
            openai_trace_id=openai_trace_id,
            main_agent_run_id=main_agent_run_id,
        ),
        payload={"task_id": task_id, "status": final_status},
        created_at=created_at,
    )


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    try:
        import json

        json.dumps(value)
    except TypeError:
        return str(value)
    return value
