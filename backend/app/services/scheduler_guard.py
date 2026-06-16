"""Deterministic scheduling policy checks for Router runtime actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ClarificationStatus,
    FailureStatus,
    Severity,
    TaskPhase,
    TaskState,
    TaskStatus,
    TaskType,
    WorkerType,
)


class SchedulerGuardViolationCode(str, Enum):
    """Stable internal codes for rejected scheduler actions."""

    TERMINAL_TASK = "terminal_task"
    WAITING_FOR_USER = "waiting_for_user"
    INTAKE_NOT_CLASSIFIED = "intake_not_classified"
    REQUIRED_CLARIFICATION_OPEN = "required_clarification_open"
    PARALLEL_LIMIT_EXCEEDED = "parallel_limit_exceeded"
    WORKER_CALL_LIMIT_EXCEEDED = "worker_call_limit_exceeded"
    MISSING_WORKER_INPUT = "missing_worker_input"
    MISSING_CURRENT_CODE = "missing_current_code"
    MISSING_REQUIREMENTS = "missing_requirements"
    MISSING_REPAIR_EVIDENCE = "missing_repair_evidence"
    NO_OPEN_BLOCKING_FAILURE = "no_open_blocking_failure"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"
    PARALLEL_REPAIR_UNSUPPORTED = "parallel_repair_unsupported"
    BLOCKING_FAILURE_PRESENT = "blocking_failure_present"
    REQUIRED_TEST_MISSING = "required_test_missing"
    REQUIRED_FORMAL_MISSING = "required_formal_missing"
    REGRESSION_REQUIRED = "regression_required"
    FORMAL_REGRESSION_REQUIRED = "formal_regression_required"
    QUALITY_GATE_REQUIRED = "quality_gate_required"


class SchedulerGuardViolation(Exception):
    """Raised when a proposed scheduler action violates runtime policy."""

    def __init__(
        self,
        code: SchedulerGuardViolationCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ProposedWorkerJob:
    """Minimal worker-call shape used to validate a parallel batch."""

    worker_type: WorkerType | str
    input_artifacts: Sequence[ArtifactRef]


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}
TEST_OR_FORMAL_WORKERS = {
    WorkerType.PLC_TEST.value,
    WorkerType.PLC_FORMAL.value,
}
REPAIR_EVIDENCE_TYPES = {
    ArtifactType.TEST_REPORT.value,
    ArtifactType.FAILING_TRACE.value,
    ArtifactType.FORMAL_REPORT.value,
    ArtifactType.COUNTEREXAMPLE.value,
}


def validate_worker_call(
    state: TaskState,
    worker_type: WorkerType | str,
    input_artifacts: Sequence[ArtifactRef],
) -> None:
    """Validate a single proposed worker dispatch before any side effects."""

    worker = _value(worker_type)
    artifacts = tuple(input_artifacts)
    _validate_common_worker_preconditions(state, worker)

    if worker == WorkerType.PLC_DEV.value:
        _require_any_artifact_type(
            artifacts,
            {ArtifactType.RAW_USER_REQUEST.value, ArtifactType.REQUIREMENTS_IR.value},
            code=SchedulerGuardViolationCode.MISSING_WORKER_INPUT,
            message="plc-dev requires raw_user_request or requirements_ir input",
            worker_type=worker,
        )
        return

    if worker in TEST_OR_FORMAL_WORKERS:
        _validate_current_code_and_requirements(state, artifacts, worker_type=worker)
        return

    if worker == WorkerType.PLC_REPAIR.value:
        validate_repair_allowed(state, artifacts)
        return

    raise SchedulerGuardViolation(
        SchedulerGuardViolationCode.MISSING_WORKER_INPUT,
        f"unsupported worker_type: {worker!r}",
        details={"worker_type": worker},
    )


def validate_repair_allowed(
    state: TaskState,
    input_artifacts: Sequence[ArtifactRef],
) -> None:
    """Validate repair-specific dispatch rules."""

    artifacts = tuple(input_artifacts)
    _validate_current_code_in_inputs(
        state,
        artifacts,
        worker_type=WorkerType.PLC_REPAIR.value,
    )

    if not _has_open_blocking_failure(state):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.NO_OPEN_BLOCKING_FAILURE,
            "plc-repair requires at least one open blocking failure",
            details={"worker_type": WorkerType.PLC_REPAIR.value},
        )

    if state.runtime_limits.repair_rounds >= state.runtime_limits.max_repair_rounds:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.REPAIR_LIMIT_REACHED,
            "maximum repair rounds reached",
            details={
                "repair_rounds": state.runtime_limits.repair_rounds,
                "max_repair_rounds": state.runtime_limits.max_repair_rounds,
            },
        )

    if not _artifact_types(artifacts) & REPAIR_EVIDENCE_TYPES:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_REPAIR_EVIDENCE,
            "plc-repair requires test or formal failure evidence",
            details={
                "worker_type": WorkerType.PLC_REPAIR.value,
                "required_artifact_types": sorted(REPAIR_EVIDENCE_TYPES),
            },
        )


def validate_parallel_jobs(
    state: TaskState,
    jobs: Sequence[ProposedWorkerJob | Mapping[str, Any]],
) -> None:
    """Validate a proposed parallel dispatch batch as one atomic action."""

    proposed = tuple(jobs)
    proposed_count = len(proposed)
    active_after_dispatch = state.runtime_limits.active_parallel_workers + proposed_count
    if active_after_dispatch > state.runtime_limits.max_parallel_workers:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.PARALLEL_LIMIT_EXCEEDED,
            "parallel worker limit would be exceeded",
            details={
                "active_parallel_workers": state.runtime_limits.active_parallel_workers,
                "proposed_jobs": proposed_count,
                "max_parallel_workers": state.runtime_limits.max_parallel_workers,
            },
        )

    calls_after_dispatch = state.runtime_limits.worker_calls_used + proposed_count
    if calls_after_dispatch > state.runtime_limits.max_worker_calls:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.WORKER_CALL_LIMIT_EXCEEDED,
            "worker call budget would be exceeded",
            details={
                "worker_calls_used": state.runtime_limits.worker_calls_used,
                "proposed_jobs": proposed_count,
                "max_worker_calls": state.runtime_limits.max_worker_calls,
            },
        )

    for job in proposed:
        if _job_worker_type(job) == WorkerType.PLC_REPAIR.value:
            raise SchedulerGuardViolation(
                SchedulerGuardViolationCode.PARALLEL_REPAIR_UNSUPPORTED,
                "plc-repair cannot be dispatched in a parallel batch in v1",
                details={"worker_type": WorkerType.PLC_REPAIR.value},
            )

    for job in proposed:
        validate_worker_call(state, _job_worker_type(job), _job_input_artifacts(job))


def validate_finish_task(
    state: TaskState,
    final_status: TaskStatus | str,
) -> None:
    """Validate a proposed terminal status before Runtime marks the task done."""

    if _value(final_status) != TaskStatus.SUCCEEDED.value:
        return

    if _has_open_required_clarification(state):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.REQUIRED_CLARIFICATION_OPEN,
            "cannot finish as succeeded with open required clarification",
        )

    if state.gates.has_blocking_failure or _has_open_blocking_failure(state):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.BLOCKING_FAILURE_PRESENT,
            "cannot finish as succeeded while blocking failures remain",
        )

    if state.gates.test_required and state.gates.latest_test_passed is not True:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.REQUIRED_TEST_MISSING,
            "cannot finish as succeeded before required tests pass",
        )

    if state.gates.formal_required and state.gates.latest_formal_passed is not True:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.REQUIRED_FORMAL_MISSING,
            "cannot finish as succeeded before required formal verification passes",
        )

    if state.gates.regression_required:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.REGRESSION_REQUIRED,
            "cannot finish as succeeded while regression testing is required",
        )

    if state.gates.formal_regression_required:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.FORMAL_REGRESSION_REQUIRED,
            "cannot finish as succeeded while formal regression is required",
        )

    if state.gates.can_finish_as_success is not True:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.QUALITY_GATE_REQUIRED,
            "cannot finish as succeeded before Quality Gate passes",
        )


def _validate_common_worker_preconditions(state: TaskState, worker_type: str) -> None:
    if _value(state.status) in TERMINAL_STATUSES:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.TERMINAL_TASK,
            "cannot dispatch workers for a terminal task",
            details={"status": _value(state.status), "worker_type": worker_type},
        )

    if _value(state.status) == TaskStatus.WAITING_USER.value:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.WAITING_FOR_USER,
            "cannot dispatch workers while task is waiting for user input",
            details={"worker_type": worker_type},
        )

    if _is_intake_not_classified(state):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.INTAKE_NOT_CLASSIFIED,
            "cannot dispatch PLC workers before intake classification completes",
            details={
                "status": _value(state.status),
                "phase": _value(state.phase),
                "task_type": _value(state.task_type),
                "worker_type": worker_type,
            },
        )

    if _has_open_required_clarification(state):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.REQUIRED_CLARIFICATION_OPEN,
            "cannot dispatch workers with open required clarification",
            details={"worker_type": worker_type},
        )

    if (
        state.runtime_limits.active_parallel_workers + 1
        > state.runtime_limits.max_parallel_workers
    ):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.PARALLEL_LIMIT_EXCEEDED,
            "parallel worker limit would be exceeded",
            details={
                "active_parallel_workers": state.runtime_limits.active_parallel_workers,
                "max_parallel_workers": state.runtime_limits.max_parallel_workers,
            },
        )

    if state.runtime_limits.worker_calls_used >= state.runtime_limits.max_worker_calls:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.WORKER_CALL_LIMIT_EXCEEDED,
            "worker call budget has been exhausted",
            details={
                "worker_calls_used": state.runtime_limits.worker_calls_used,
                "max_worker_calls": state.runtime_limits.max_worker_calls,
            },
        )


def _validate_current_code_and_requirements(
    state: TaskState,
    input_artifacts: Sequence[ArtifactRef],
    *,
    worker_type: str,
) -> None:
    _validate_current_code_in_inputs(state, input_artifacts, worker_type=worker_type)

    requirements = state.current_artifacts.requirements_ir
    if requirements is None:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_REQUIREMENTS,
            f"{worker_type} requires current requirements_ir",
            details={"worker_type": worker_type},
        )

    if not _contains_artifact_ref(input_artifacts, requirements):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_REQUIREMENTS,
            f"{worker_type} input must include current requirements_ir",
            details={
                "worker_type": worker_type,
                "expected_artifact_id": requirements.artifact_id,
                "input_artifact_ids": _artifact_ids(input_artifacts),
            },
        )


def _validate_current_code_in_inputs(
    state: TaskState,
    input_artifacts: Sequence[ArtifactRef],
    *,
    worker_type: str,
) -> None:
    current_code = state.current_artifacts.current_code
    if current_code is None:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
            f"{worker_type} requires current plc_code",
            details={"worker_type": worker_type},
        )

    if not _contains_artifact_ref(input_artifacts, current_code):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
            f"{worker_type} input must include current plc_code",
            details={
                "worker_type": worker_type,
                "expected_artifact_id": current_code.artifact_id,
                "input_artifact_ids": _artifact_ids(input_artifacts),
            },
        )


def _require_any_artifact_type(
    input_artifacts: Sequence[ArtifactRef],
    required_types: set[str],
    *,
    code: SchedulerGuardViolationCode,
    message: str,
    worker_type: str,
) -> None:
    if not _artifact_types(input_artifacts) & required_types:
        raise SchedulerGuardViolation(
            code,
            message,
            details={
                "worker_type": worker_type,
                "required_artifact_types": sorted(required_types),
            },
        )


def _is_intake_not_classified(state: TaskState) -> bool:
    return (
        _value(state.status) == TaskStatus.CREATED.value
        or _value(state.phase) == TaskPhase.INTAKE.value
        or _value(state.task_type) == TaskType.UNKNOWN.value
    )


def _has_open_required_clarification(state: TaskState) -> bool:
    return any(
        question.required
        and _value(question.status) == ClarificationStatus.OPEN.value
        for question in state.unresolved_questions
    )


def _has_open_blocking_failure(state: TaskState) -> bool:
    return any(
        _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == Severity.BLOCKING.value
        for failure in state.failures
    )


def _artifact_types(input_artifacts: Sequence[ArtifactRef]) -> set[str]:
    return {_value(artifact.type) for artifact in input_artifacts}


def _artifact_ids(input_artifacts: Sequence[ArtifactRef]) -> list[str]:
    return [artifact.artifact_id for artifact in input_artifacts]


def _contains_artifact_ref(
    input_artifacts: Sequence[ArtifactRef],
    expected: ArtifactRef,
) -> bool:
    return any(
        artifact.artifact_id == expected.artifact_id
        and _value(artifact.type) == _value(expected.type)
        for artifact in input_artifacts
    )


def _job_worker_type(job: ProposedWorkerJob | Mapping[str, Any]) -> str:
    if isinstance(job, ProposedWorkerJob):
        return _value(job.worker_type)
    return _value(job["worker_type"])


def _job_input_artifacts(
    job: ProposedWorkerJob | Mapping[str, Any],
) -> Sequence[ArtifactRef]:
    if isinstance(job, ProposedWorkerJob):
        return job.input_artifacts
    artifacts = job["input_artifacts"]
    if not isinstance(artifacts, Sequence):
        raise TypeError("job input_artifacts must be a sequence")
    return artifacts


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
