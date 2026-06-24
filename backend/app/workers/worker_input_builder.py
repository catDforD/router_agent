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
    WorkerCompilerType,
    WorkerBudget,
    WorkerContext,
    WorkerConfig,
    WorkerFuzzMethod,
    WorkerInput,
    WorkerMode,
    WorkerPipelineStage,
    WorkerRepairSource,
    WorkerRepairTarget,
    WorkerTargetLanguage,
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
    worker_config: WorkerConfig | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkerInput:
    """Build and validate a Router v1 WorkerInput for one worker dispatch."""

    worker = _worker_type_value(worker_type)
    if worker not in WORKER_MODE_BY_TYPE:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")

    now = created_at or utc_now()
    job_id = worker_job_id or _new_worker_job_id(worker)
    selected_artifacts = input_artifacts or select_worker_input_artifacts(task, worker)
    normalized_worker_config = _build_worker_config(task, worker, worker_config)
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
        worker_config=normalized_worker_config,
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


def _build_worker_config(
    task: TaskState,
    worker_type: str,
    worker_config: WorkerConfig | dict[str, Any] | None,
) -> WorkerConfig | None:
    base = _default_worker_config(task, worker_type)
    if worker_config is None:
        return base
    override = (
        worker_config
        if isinstance(worker_config, WorkerConfig)
        else WorkerConfig.model_validate(worker_config)
    )
    if base is None:
        return override
    merged = {
        **base.model_dump(exclude_none=True),
        **override.model_dump(exclude_none=True),
    }
    return WorkerConfig.model_validate(merged)


def _default_worker_config(task: TaskState, worker_type: str) -> WorkerConfig | None:
    worker = _worker_type_value(worker_type)
    project_language = _optional_value(task.project_context.target_plc_language)
    if worker == WorkerType.PLC_DEV.value:
        config = WorkerConfig(
            target_language=_default_target_language(project_language),
            compiler_type=WorkerCompilerType.MATIEC,
            enable_socratic_spec=None,
            socratic_skip=None,
            rpc_pipeline=_default_pipeline(task),
        )
        return config
    if worker == WorkerType.PLC_TEST.value:
        return WorkerConfig(
            fuzz_method=WorkerFuzzMethod.BOUNDARY,
            case_count=50,
            enable_fuzz_test=True,
        )
    if worker == WorkerType.PLC_FORMAL.value:
        return WorkerConfig(
            compiler_type=WorkerCompilerType.MATIEC,
            natural_language_requirements=task.normalized_goal or task.raw_user_request,
        )
    if worker == WorkerType.PLC_REPAIR.value:
        return WorkerConfig(
            repair_source=_default_repair_source(task),
            repair_targets=_default_repair_targets(task),
            compiler_type=WorkerCompilerType.MATIEC,
            repair_failure_notes=_repair_failure_notes(task),
        )
    return None


def _default_target_language(value: str | None) -> WorkerTargetLanguage | None:
    if value is None:
        return WorkerTargetLanguage.ST
    try:
        return WorkerTargetLanguage(value)
    except ValueError:
        return WorkerTargetLanguage.ST


def _default_pipeline(task: TaskState) -> list[WorkerPipelineStage] | None:
    stages: list[WorkerPipelineStage] = []
    if task.difficulty.requires_test:
        stages.append(WorkerPipelineStage.FUZZ)
    if task.difficulty.requires_formal:
        stages.append(WorkerPipelineStage.FORMAL)
    return stages or None


def _default_repair_source(task: TaskState) -> WorkerRepairSource | None:
    has_compile = any(_failure_source_value(failure.source) == "compile" for failure in task.failures)
    has_test = any(_failure_source_value(failure.source) == "test" for failure in task.failures)
    has_formal = any(_failure_source_value(failure.source) == "formal" for failure in task.failures)
    if sum(bool(flag) for flag in (has_compile, has_test, has_formal)) > 1:
        return WorkerRepairSource.MULTI
    if has_compile:
        return WorkerRepairSource.COMPILE
    if has_test:
        return WorkerRepairSource.TEST_FAILURE
    if has_formal:
        return WorkerRepairSource.FORMAL_VALIDATION_FAILURE
    return WorkerRepairSource.COMPILE


def _default_repair_targets(task: TaskState) -> list[WorkerRepairTarget] | None:
    targets: list[WorkerRepairTarget] = []
    if any(_failure_source_value(failure.source) == "compile" for failure in task.failures):
        targets.append(WorkerRepairTarget.COMPILE)
    if any(_failure_source_value(failure.source) == "test" for failure in task.failures):
        targets.append(WorkerRepairTarget.TEST_FAILURE)
    if any(_failure_source_value(failure.source) == "formal" for failure in task.failures):
        targets.append(WorkerRepairTarget.FORMAL_VALIDATION_FAILURE)
    return targets or None


def _repair_failure_notes(task: TaskState) -> str | None:
    open_failures = [
        failure
        for failure in task.failures
        if _value(failure.status) == FailureStatus.OPEN.value
    ]
    if not open_failures:
        return None
    return " | ".join(
        f"{failure.failure_id}: {failure.title}"
        for failure in open_failures
    )


def _failure_source_value(value: Any) -> str:
    return _value(value)


def _optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return _value(value)


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
