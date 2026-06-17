"""Build Router v1 WorkerInput payloads from persisted task state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from app.core.time import utc_now
from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ExpectedOutputSpec,
    FailureStatus,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerMode,
    WorkerType,
    TaskState,
)


DEFAULT_WORKER_TIMEOUT_SECONDS = 300
DEFAULT_WORKER_MAX_ITERATIONS = 1

WORKER_MODE_BY_TYPE: dict[str, WorkerMode] = {
    WorkerType.PLC_DEV.value: WorkerMode.CREATE,
    WorkerType.PLC_TEST.value: WorkerMode.TEST,
    WorkerType.PLC_FORMAL.value: WorkerMode.FORMAL_VERIFY,
    WorkerType.PLC_REPAIR.value: WorkerMode.REPAIR,
}

EXPECTED_OUTPUT_TYPES_BY_WORKER: dict[str, tuple[ArtifactType, ...]] = {
    WorkerType.PLC_DEV.value: (
        ArtifactType.REQUIREMENTS_IR,
        ArtifactType.PLC_CODE,
        ArtifactType.IO_CONTRACT,
    ),
    WorkerType.PLC_TEST.value: (ArtifactType.TEST_REPORT,),
    WorkerType.PLC_FORMAL.value: (ArtifactType.FORMAL_REPORT,),
    WorkerType.PLC_REPAIR.value: (
        ArtifactType.PATCH,
        ArtifactType.PLC_CODE,
        ArtifactType.REPAIR_SUMMARY,
    ),
}

REPAIR_EVIDENCE_FIELDS = (
    "latest_test_report",
    "latest_failing_trace",
    "latest_formal_report",
    "latest_counterexample",
)


class WorkerInputBuildError(ValueError):
    """Raised when a WorkerInput cannot be built from the current task state."""


def build_worker_input(
    task: TaskState,
    worker_type: WorkerType | str,
    *,
    objective: str | None = None,
    input_artifacts: list[ArtifactRef] | None = None,
    worker_job_id: str | None = None,
    trace_context: TraceContext | None = None,
    timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS,
    max_iterations: int | None = DEFAULT_WORKER_MAX_ITERATIONS,
    created_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkerInput:
    """Build and validate a Router v1 WorkerInput for one worker dispatch."""

    worker = _worker_type_value(worker_type)
    if worker not in WORKER_MODE_BY_TYPE:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")

    now = created_at or utc_now()
    job_id = worker_job_id or _new_worker_job_id(worker)
    selected_artifacts = input_artifacts or select_worker_input_artifacts(task, worker)
    return WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id=job_id,
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode=worker_mode_for(worker),
        objective=objective or _default_objective(task, worker),
        input_artifacts=selected_artifacts,
        context=_worker_context(task),
        constraints=[],
        expected_outputs=expected_outputs_for(worker),
        budget=WorkerBudget(
            timeout_seconds=timeout_seconds,
            max_iterations=max_iterations,
        ),
        trace_context=_trace_context(task, job_id, trace_context),
        idempotency_key=f"{task.task_id}:{job_id}",
        created_at=now,
        metadata=metadata,
    )


def select_worker_input_artifacts(
    task: TaskState,
    worker_type: WorkerType | str,
) -> list[ArtifactRef]:
    """Select the current task artifacts required for the requested worker."""

    worker = _worker_type_value(worker_type)
    artifacts = task.current_artifacts

    if worker == WorkerType.PLC_DEV.value:
        if artifacts.raw_user_request is not None:
            return [artifacts.raw_user_request]
        if artifacts.requirements_ir is not None:
            return [artifacts.requirements_ir]
        raise WorkerInputBuildError(
            "plc-dev requires raw_user_request or requirements_ir artifact"
        )

    if worker in {WorkerType.PLC_TEST.value, WorkerType.PLC_FORMAL.value}:
        if artifacts.requirements_ir is None:
            raise WorkerInputBuildError(f"{worker} requires current requirements_ir")
        if artifacts.current_code is None:
            raise WorkerInputBuildError(f"{worker} requires current plc_code")
        return [artifacts.requirements_ir, artifacts.current_code]

    if worker == WorkerType.PLC_REPAIR.value:
        if artifacts.current_code is None:
            raise WorkerInputBuildError("plc-repair requires current plc_code")
        evidence = [
            artifact
            for artifact in (
                getattr(artifacts, field_name)
                for field_name in REPAIR_EVIDENCE_FIELDS
            )
            if artifact is not None
        ]
        if not evidence:
            raise WorkerInputBuildError(
                "plc-repair requires latest test or formal failure evidence"
            )
        return [artifacts.current_code, *evidence]

    raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")


def worker_mode_for(worker_type: WorkerType | str) -> WorkerMode:
    """Return the deterministic WorkerMode for a Router worker type."""

    worker = _worker_type_value(worker_type)
    try:
        return WORKER_MODE_BY_TYPE[worker]
    except KeyError as exc:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}") from exc


def expected_outputs_for(worker_type: WorkerType | str) -> list[ExpectedOutputSpec]:
    """Return the expected output artifact specs for a Router worker type."""

    worker = _worker_type_value(worker_type)
    try:
        output_types = EXPECTED_OUTPUT_TYPES_BY_WORKER[worker]
    except KeyError as exc:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}") from exc
    return [
        ExpectedOutputSpec(
            artifact_type=artifact_type,
            required=True,
            description=f"Expected {artifact_type.value} output from {worker}.",
        )
        for artifact_type in output_types
    ]


def _worker_context(task: TaskState) -> WorkerContext:
    return WorkerContext(
        user_goal=task.normalized_goal or task.raw_user_request,
        task_type=task.task_type,
        difficulty_level=task.difficulty.level,
        target_plc_language=_optional_value(task.project_context.target_plc_language),
        target_platform=task.project_context.target_platform,
        repair_round=task.runtime_limits.repair_rounds,
        selected_failure_ids=[
            failure.failure_id
            for failure in task.failures
            if _value(failure.status) == FailureStatus.OPEN.value
        ],
        assumptions=task.assumptions,
    )


def _trace_context(
    task: TaskState,
    worker_job_id: str,
    trace_context: TraceContext | None,
) -> TraceContext:
    base = trace_context or TraceContext(
        openai_trace_id=task.trace.openai_trace_id,
        main_agent_run_id=task.trace.latest_main_agent_run_id,
    )
    return base.model_copy(update={"worker_job_id": worker_job_id})


def _default_objective(task: TaskState, worker_type: str) -> str:
    goal = task.normalized_goal or task.raw_user_request
    return f"Run {worker_type} for task goal: {goal}"


def _new_worker_job_id(worker_type: str) -> str:
    return f"worker-job-{worker_type.replace('-', '-')}-{uuid4().hex[:12]}"


def _worker_type_value(worker_type: WorkerType | str) -> str:
    return _value(worker_type)


def _optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return _value(value)


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
