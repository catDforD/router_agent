"""Build file-centric WorkerInput payloads from persisted task state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any
from uuid import uuid4

from app.core.time import utc_now
from app.models.router_schema import (
    ExpectedFileSpec,
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

REPORT_DIR_TEMPLATE = ".router/reports/{worker_job_id}"


class WorkerInputBuildError(ValueError):
    """Raised when a WorkerInput cannot be built from the current task state."""


def build_worker_input(
    task: TaskState,
    worker_type: WorkerType | str,
    *,
    objective: str | None = None,
    input_paths: list[str] | None = None,
    output_paths: list[str] | None = None,
    worker_job_id: str | None = None,
    trace_context: TraceContext | None = None,
    timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS,
    max_iterations: int | None = DEFAULT_WORKER_MAX_ITERATIONS,
    created_at: datetime | None = None,
    worker_config: WorkerConfig | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkerInput:
    """Build and validate one file-centric WorkerInput for worker dispatch."""

    worker = _worker_type_value(worker_type)
    if worker not in WORKER_MODE_BY_TYPE:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")

    now = created_at or utc_now()
    job_id = worker_job_id or _new_worker_job_id(worker)
    selected_paths = _dedupe(input_paths or select_worker_input_paths(task, worker))
    expected_paths = output_paths or default_worker_output_paths(worker, job_id, task)
    normalized_worker_config = _build_worker_config(task, worker, worker_config)
    input_signature = _input_signature(worker, selected_paths, normalized_worker_config)
    merged_metadata: dict[str, Any] = dict(metadata or {})
    merged_metadata["input_signature"] = input_signature
    workspace_root = _workspace_root(task)
    if workspace_root is None:
        raise WorkerInputBuildError("worker dispatch requires a workspace root")

    return WorkerInput(
        schema_version="router.v2",
        task_id=task.task_id,
        worker_job_id=job_id,
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode=worker_mode_for(worker),
        objective=objective or _default_objective(task, worker),
        workspace_root=workspace_root,
        current_directory=_current_directory(task, workspace_root),
        input_paths=selected_paths,
        output_paths=_dedupe(expected_paths),
        context=_worker_context(task),
        constraints=[],
        expected_outputs=expected_outputs_for(worker, expected_paths),
        budget=WorkerBudget(
            timeout_seconds=timeout_seconds,
            max_iterations=max_iterations,
        ),
        trace_context=_trace_context(task, job_id, trace_context),
        idempotency_key=f"{task.task_id}:{worker}:{job_id}",
        created_at=now,
        worker_config=normalized_worker_config,
        metadata=merged_metadata,
    )


def select_worker_input_paths(
    task: TaskState,
    worker_type: WorkerType | str,
) -> list[str]:
    """Select current workspace paths required for the requested worker."""

    worker = _worker_type_value(worker_type)
    files = task.current_files

    if worker == WorkerType.PLC_DEV.value:
        return _dedupe(
            [
                files.raw_user_request,
                files.requirements,
            ]
        )

    if worker in {WorkerType.PLC_TEST.value, WorkerType.PLC_FORMAL.value}:
        if files.current_code is None:
            raise WorkerInputBuildError(f"{worker} requires current PLC code path")
        return _dedupe([files.requirements, files.current_code])

    if worker == WorkerType.PLC_REPAIR.value:
        if files.current_code is None:
            raise WorkerInputBuildError("plc-repair requires current PLC code path")
        evidence = _dedupe(
            [
                files.latest_test_report,
                files.latest_failing_trace,
                files.latest_formal_report,
                files.latest_counterexample,
            ]
        )
        if not evidence:
            raise WorkerInputBuildError(
                "plc-repair requires latest test or formal failure evidence path"
            )
        return _dedupe([files.current_code, *evidence])

    raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")


def default_worker_output_paths(
    worker_type: WorkerType | str,
    worker_job_id: str,
    task: TaskState,
) -> list[str]:
    worker = _worker_type_value(worker_type)
    report_dir = REPORT_DIR_TEMPLATE.format(worker_job_id=worker_job_id)
    code_path = task.current_files.current_code or "src/plc_code.st"
    if worker == WorkerType.PLC_DEV.value:
        return [
            "src/plc_code.st",
            f"{report_dir}/requirements.json",
            f"{report_dir}/io_contract.json",
        ]
    if worker == WorkerType.PLC_TEST.value:
        return [f"{report_dir}/test_report.json"]
    if worker == WorkerType.PLC_FORMAL.value:
        return [f"{report_dir}/formal_report.json"]
    if worker == WorkerType.PLC_REPAIR.value:
        return [
            code_path,
            f"{report_dir}/patch.diff",
            f"{report_dir}/repair_summary.json",
        ]
    raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")


def worker_mode_for(worker_type: WorkerType | str) -> WorkerMode:
    """Return the deterministic WorkerMode for a Router worker type."""

    worker = _worker_type_value(worker_type)
    try:
        return WORKER_MODE_BY_TYPE[worker]
    except KeyError as exc:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}") from exc


def expected_outputs_for(
    worker_type: WorkerType | str,
    output_paths: list[str],
) -> list[ExpectedFileSpec]:
    """Return expected output file specs for a Router worker type."""

    worker = _worker_type_value(worker_type)
    if worker not in WORKER_MODE_BY_TYPE:
        raise WorkerInputBuildError(f"unsupported worker_type: {worker!r}")
    return [
        ExpectedFileSpec(
            path=path,
            required=True,
            description=f"Expected {path} output from {worker}.",
            mime_type=_mime_type_for_path(path),
        )
        for path in output_paths
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


def _input_signature(
    worker_type: str,
    input_paths: list[str],
    worker_config: WorkerConfig | None,
) -> dict[str, Any]:
    config_payload = (
        worker_config.model_dump(mode="json", exclude_none=True)
        if worker_config is not None
        else {}
    )
    encoded_config = json.dumps(
        config_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "worker_type": worker_type,
        "input_paths": sorted(input_paths),
        "worker_config_hash": f"sha256:{hashlib.sha256(encoded_config.encode('utf-8')).hexdigest()}",
    }


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
        return WorkerConfig(
            target_language=_default_target_language(project_language),
            compiler_type=WorkerCompilerType.MATIEC,
            enable_socratic_spec=None,
            socratic_skip=None,
            rpc_pipeline=_default_pipeline(task),
        )
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


def _workspace_root(task: TaskState) -> str | None:
    if task.workspace is not None:
        return task.workspace.root
    return task.project_context.workspace_root


def _current_directory(task: TaskState, workspace_root: str) -> str:
    if task.workspace is not None and task.workspace.current_directory:
        return task.workspace.current_directory
    return workspace_root


def _mime_type_for_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".diff") or lower.endswith(".patch"):
        return "text/x-diff"
    if lower.endswith(".md"):
        return "text/markdown"
    return "text/plain"


def _dedupe(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


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
