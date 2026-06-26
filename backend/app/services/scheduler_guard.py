"""Deterministic scheduling policy checks for Router runtime actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from app.models.router_schema import (
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
    input_paths: Sequence[str]


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
def validate_worker_call(
    state: TaskState,
    worker_type: WorkerType | str,
    input_paths: Sequence[str],
) -> None:
    """Validate a single proposed worker dispatch before any side effects."""

    worker = _value(worker_type)
    paths = tuple(input_paths)
    _validate_common_worker_preconditions(state, worker)

    if worker == WorkerType.PLC_DEV.value:
        _require_any_path(
            paths,
            code=SchedulerGuardViolationCode.MISSING_WORKER_INPUT,
            message="plc-dev requires at least one input path",
            worker_type=worker,
        )
        return

    if worker in TEST_OR_FORMAL_WORKERS:
        _validate_current_code_in_inputs(state, paths, worker_type=worker)
        return

    if worker == WorkerType.PLC_REPAIR.value:
        validate_repair_allowed(state, paths)
        return

    raise SchedulerGuardViolation(
        SchedulerGuardViolationCode.MISSING_WORKER_INPUT,
        f"unsupported worker_type: {worker!r}",
        details={"worker_type": worker},
    )


def validate_repair_allowed(
    state: TaskState,
    input_paths: Sequence[str],
) -> None:
    """Validate repair-specific dispatch rules."""

    paths = tuple(input_paths)
    _validate_current_code_in_inputs(
        state,
        paths,
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

    evidence_paths = {
        path
        for path in (
            state.current_files.latest_test_report,
            state.current_files.latest_failing_trace,
            state.current_files.latest_formal_report,
            state.current_files.latest_counterexample,
        )
        if path
    }
    if not evidence_paths & set(paths):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_REPAIR_EVIDENCE,
            "plc-repair requires test or formal failure evidence paths",
            details={
                "worker_type": WorkerType.PLC_REPAIR.value,
                "required_paths": sorted(evidence_paths),
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
        validate_worker_call(state, _job_worker_type(job), _job_input_paths(job))


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
            "cannot dispatch PLC workers before task is prepared for worker dispatch",
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


def _validate_current_code_in_inputs(
    state: TaskState,
    input_paths: Sequence[str],
    *,
    worker_type: str,
) -> None:
    current_code = state.current_files.current_code
    if current_code is None:
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
            f"{worker_type} requires current PLC code path",
            details={"worker_type": worker_type},
        )

    if current_code not in set(input_paths):
        raise SchedulerGuardViolation(
            SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
            f"{worker_type} input must include current PLC code path",
            details={
                "worker_type": worker_type,
                "expected_path": current_code,
                "input_paths": list(input_paths),
            },
        )


def _require_any_path(
    input_paths: Sequence[str],
    *,
    code: SchedulerGuardViolationCode,
    message: str,
    worker_type: str,
) -> None:
    if not input_paths:
        raise SchedulerGuardViolation(
            code,
            message,
            details={
                "worker_type": worker_type,
                "input_paths": [],
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


def _job_worker_type(job: ProposedWorkerJob | Mapping[str, Any]) -> str:
    if isinstance(job, ProposedWorkerJob):
        return _value(job.worker_type)
    return _value(job["worker_type"])


def _job_input_paths(
    job: ProposedWorkerJob | Mapping[str, Any],
) -> Sequence[str]:
    if isinstance(job, ProposedWorkerJob):
        return job.input_paths
    paths = job["input_paths"]
    if not isinstance(paths, Sequence):
        raise TypeError("job input_paths must be a sequence")
    return paths


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
