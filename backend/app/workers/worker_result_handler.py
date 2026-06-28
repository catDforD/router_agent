"""Apply Router worker results to persisted task state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import RepositoryNotFoundError
from app.core.ids import prefixed_id
from app.models.router_schema import (
    Assumption,
    ClarificationQuestion,
    CurrentFiles,
    Failure,
    FailureSource,
    FailureStatus,
    GateState,
    RuntimeLimits,
    Severity,
    TaskPhase,
    TaskState,
    TaskStatus,
    WorkerExecutionStatus,
    WorkerJobRef,
    WorkerOutcomeStatus,
    WorkerResult,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRecord, WorkerJobRepository


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


class WorkerResultHandlerError(Exception):
    """Base class for worker result handling failures."""


class WorkerResultHandlerMissingTaskError(WorkerResultHandlerError):
    """Raised when a worker result references a missing task."""


class WorkerResultHandlerMissingWorkerJobError(WorkerResultHandlerError):
    """Raised when a worker result references a missing worker job."""


class WorkerResultHandlerIdentityError(WorkerResultHandlerError):
    """Raised when a worker result does not match persisted job identity."""


@dataclass(frozen=True)
class WorkerResultHandleResult:
    """Result of applying or replaying a worker result."""

    task: TaskState
    applied: bool
    summary: str


class WorkerResultHandler:
    """Projects terminal WorkerResult payloads back into TaskState."""

    def __init__(self, session: Session) -> None:
        self.task_repository = TaskRepository(session)
        self.worker_job_repository = WorkerJobRepository(session)

    def handle_worker_result(self, result: WorkerResult) -> WorkerResultHandleResult:
        """Validate and apply one worker result to its persisted task."""

        task = self._get_task(result.task_id)
        job = self._get_worker_job(result.worker_job_id)
        self._validate_identity(result, job)

        if _value(task.status) in TERMINAL_STATUSES:
            return WorkerResultHandleResult(
                task=task,
                applied=False,
                summary=(
                    "Worker result retained for audit only because task is "
                    f"terminal: {result.worker_job_id}"
                ),
            )

        if result.worker_job_id in task.completed_worker_job_ids:
            return WorkerResultHandleResult(
                task=task,
                applied=False,
                summary=f"Worker result already applied: {result.worker_job_id}",
            )

        updated = self._apply_result(task, job, result)
        persisted = self.task_repository.update_task_state(updated)
        return WorkerResultHandleResult(
            task=persisted,
            applied=True,
            summary=f"Applied {result.worker_type} result {result.worker_job_id}.",
        )

    def _get_task(self, task_id: str) -> TaskState:
        try:
            return self.task_repository.get_task(task_id)
        except RepositoryNotFoundError as exc:
            raise WorkerResultHandlerMissingTaskError(
                f"task not found for worker result: {task_id}"
            ) from exc

    def _get_worker_job(self, worker_job_id: str) -> WorkerJobRecord:
        try:
            return self.worker_job_repository.get_job(worker_job_id)
        except RepositoryNotFoundError as exc:
            raise WorkerResultHandlerMissingWorkerJobError(
                f"worker job not found for result: {worker_job_id}"
            ) from exc

    def _validate_identity(
        self,
        result: WorkerResult,
        job: WorkerJobRecord,
    ) -> None:
        mismatches: dict[str, dict[str, str]] = {}
        _record_mismatch(mismatches, "task_id", result.task_id, job.task_id)
        _record_mismatch(
            mismatches,
            "worker_job_id",
            result.worker_job_id,
            job.id,
        )
        _record_mismatch(
            mismatches,
            "worker_type",
            _value(result.worker_type),
            job.worker_type,
        )
        _record_mismatch(
            mismatches,
            "input.worker_type",
            _value(result.worker_type),
            _value(job.input.worker_type),
        )
        _record_mismatch(
            mismatches,
            "input.mcp_tool",
            _value(result.mcp_tool),
            _value(job.input.mcp_tool),
        )

        if job.result is not None:
            _record_mismatch(
                mismatches,
                "stored_result.task_id",
                result.task_id,
                job.result.task_id,
            )
            _record_mismatch(
                mismatches,
                "stored_result.worker_job_id",
                result.worker_job_id,
                job.result.worker_job_id,
            )
            _record_mismatch(
                mismatches,
                "stored_result.worker_type",
                _value(result.worker_type),
                _value(job.result.worker_type),
            )
            _record_mismatch(
                mismatches,
                "stored_result.mcp_tool",
                _value(result.mcp_tool),
                _value(job.result.mcp_tool),
            )

        if mismatches:
            raise WorkerResultHandlerIdentityError(
                f"worker result identity mismatch: {mismatches}"
            )

    def _apply_result(
        self,
        task: TaskState,
        job: WorkerJobRecord,
        result: WorkerResult,
    ) -> TaskState:
        execution_status = _value(result.execution_status)
        worker_type = _value(result.worker_type)
        outcome_status = _value(result.outcome.status)
        completed_at = result.completed_at

        current_files = task.current_files
        failures = _merge_failures(task.failures, result.failures, result)
        assumptions = _merge_assumptions(task.assumptions, result.assumptions)
        questions = task.unresolved_questions
        gates = task.gates
        runtime_limits = task.runtime_limits
        status = task.status
        phase = task.phase

        if execution_status == WorkerExecutionStatus.COMPLETED.value:
            current_files = _project_files(current_files, result.written_paths)
            if outcome_status == WorkerOutcomeStatus.NEED_CLARIFICATION.value:
                questions = _merge_questions(
                    questions,
                    (
                        result.clarification_request.questions
                        if result.clarification_request is not None
                        else []
                    ),
                )
                if (
                    result.clarification_request is not None
                    and result.clarification_request.blocking
                ) or result.outcome.blocking:
                    status = TaskStatus.WAITING_USER.value
                    phase = TaskPhase.CLARIFYING.value
            elif worker_type == WorkerType.PLC_DEV.value:
                gates, phase = _apply_dev_result(
                    task,
                    gates,
                    outcome_status,
                )
            elif worker_type == WorkerType.PLC_TEST.value:
                failures, gates = _apply_test_result(
                    failures,
                    gates,
                    result,
                    outcome_status,
                )
            elif worker_type == WorkerType.PLC_FORMAL.value:
                failures, gates = _apply_formal_result(
                    failures,
                    gates,
                    result,
                    outcome_status,
                )
            elif worker_type == WorkerType.PLC_REPAIR.value:
                failures, gates, runtime_limits, phase = _apply_repair_result(
                    task,
                    failures,
                    job.input,
                    gates,
                    runtime_limits,
                    result,
                    outcome_status,
                )
        elif result.failures:
            failures = _merge_failures(task.failures, result.failures, result)

        active_worker_jobs = _remove_active_job(
            task.active_worker_jobs,
            result.worker_job_id,
        )
        completed_worker_job_ids = _append_unique(
            task.completed_worker_job_ids,
            result.worker_job_id,
        )

        gates = gates.model_copy(
            update={
                "has_blocking_failure": _has_open_blocking_failure(failures),
                "can_finish_as_success": False,
            }
        )

        if (
            _value(phase) == TaskPhase.REPAIRING.value
            and not _has_open_blocking_failure(failures)
        ):
            phase = TaskPhase.QUALITY_GATE.value

        updated = task.model_copy(
            deep=True,
            update={
                "status": status,
                "phase": phase,
                "updated_at": completed_at,
                "runtime_limits": runtime_limits,
                "gates": gates,
                "current_files": current_files,
                "active_worker_jobs": active_worker_jobs,
                "completed_worker_job_ids": completed_worker_job_ids,
                "assumptions": assumptions,
                "unresolved_questions": questions,
                "failures": failures,
            },
        )
        return TaskState.model_validate(updated.model_dump(mode="json"))


def handle_worker_result(
    result: WorkerResult,
    *,
    session: Session,
) -> WorkerResultHandleResult:
    """Public convenience entrypoint for scripts and future runtime tools."""

    return WorkerResultHandler(session).handle_worker_result(result)


def _apply_dev_result(
    task: TaskState,
    gates: GateState,
    outcome_status: str,
) -> tuple[GateState, str]:
    if outcome_status != WorkerOutcomeStatus.PASSED.value:
        return gates, task.phase

    phase = task.phase
    if (
        gates.test_required
        or task.difficulty.requires_test
        or task.current_files.latest_test_report is not None
    ):
        phase = TaskPhase.TESTING.value
    elif gates.formal_required or task.difficulty.requires_formal:
        phase = TaskPhase.FORMAL_VERIFYING.value

    return gates.model_copy(
        update={
            "latest_test_passed": None,
            "latest_formal_passed": None,
            "regression_required": False,
            "formal_regression_required": False,
        }
    ), phase


def _apply_test_result(
    failures: list[Failure],
    gates: GateState,
    result: WorkerResult,
    outcome_status: str,
) -> tuple[list[Failure], GateState]:
    if outcome_status == WorkerOutcomeStatus.PASSED.value:
        failures = _resolve_failures(
            failures,
            source=FailureSource.TEST.value,
            worker_job_id=result.worker_job_id,
            resolved_by_path=_first_report_path(result, "test_report"),
            resolved_at=result.completed_at,
        )
        gates = gates.model_copy(
            update={
                "test_required": True,
                "latest_test_passed": True,
                "regression_required": False,
            }
        )
    elif outcome_status == WorkerOutcomeStatus.FAILED.value:
        if not result.failures:
            failures = [
                *failures,
                _blocking_worker_failure(
                    result,
                    source=FailureSource.TEST,
                    title="PLC test worker failed",
                    description=(
                        "PLC test worker reported a failed outcome without "
                        "structured failure details."
                    ),
                    evidence_path=_first_report_path(result, "test_report"),
                ),
            ]
        gates = gates.model_copy(
            update={"test_required": True, "latest_test_passed": False}
        )
    return failures, gates


def _apply_formal_result(
    failures: list[Failure],
    gates: GateState,
    result: WorkerResult,
    outcome_status: str,
) -> tuple[list[Failure], GateState]:
    if outcome_status == WorkerOutcomeStatus.PASSED.value:
        failures = _resolve_failures(
            failures,
            source=FailureSource.FORMAL.value,
            worker_job_id=result.worker_job_id,
            resolved_by_path=_first_report_path(result, "formal_report"),
            resolved_at=result.completed_at,
        )
        gates = gates.model_copy(
            update={
                "formal_required": True,
                "latest_formal_passed": True,
                "formal_regression_required": False,
            }
        )
    elif outcome_status == WorkerOutcomeStatus.FAILED.value:
        if not result.failures:
            failures = [
                *failures,
                _blocking_worker_failure(
                    result,
                    source=FailureSource.FORMAL,
                    title="PLC formal verification worker failed",
                    description=(
                        "PLC formal verification worker reported a failed "
                        "outcome without structured failure details."
                    ),
                    evidence_path=_first_report_path(result, "formal_report"),
                ),
            ]
        gates = gates.model_copy(
            update={"formal_required": True, "latest_formal_passed": False}
        )
    return failures, gates


def _apply_repair_result(
    task: TaskState,
    failures: list[Failure],
    worker_input: Any,
    gates: GateState,
    runtime_limits: RuntimeLimits,
    result: WorkerResult,
    outcome_status: str,
) -> tuple[list[Failure], GateState, RuntimeLimits, str]:
    if outcome_status != WorkerOutcomeStatus.PASSED.value:
        return failures, gates, runtime_limits, task.phase

    has_formal_failure = any(
        _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == Severity.BLOCKING.value
        and _value(failure.source) == FailureSource.FORMAL.value
        for failure in task.failures
    )
    repair_summary_path = _first_report_path(result, "repair_summary")
    failures = _resolve_repaired_failures(
        failures,
        result=result,
        worker_input=worker_input,
        resolved_by_path=repair_summary_path,
    )
    runtime_limits = runtime_limits.model_copy(
        update={"repair_rounds": runtime_limits.repair_rounds + 1}
    )
    requires_regression = _repair_changed_code(result)
    gate_updates: dict[str, Any] = {
        "regression_required": requires_regression,
    }
    if requires_regression:
        gate_updates.update(
            {
                "latest_test_passed": None,
                "latest_formal_passed": None,
                "formal_regression_required": (
                    True if has_formal_failure else gates.formal_regression_required
                ),
            }
        )
    else:
        gate_updates["formal_regression_required"] = False
    gates = gates.model_copy(update=gate_updates)
    phase = TaskPhase.REGRESSION.value if requires_regression else TaskPhase.QUALITY_GATE.value
    return failures, gates, runtime_limits, phase


def _project_files(
    current_files: CurrentFiles,
    written_paths: list[str],
) -> CurrentFiles:
    updates: dict[str, Any] = {"all_paths": list(current_files.all_paths)}
    for path in written_paths:
        if path not in updates["all_paths"]:
            updates["all_paths"].append(path)
        pointer_field = _current_file_field_for_path(path)
        if pointer_field is not None:
            updates[pointer_field] = path
    return current_files.model_copy(update=updates)


def _merge_failures(
    existing: list[Failure],
    incoming: list[Failure],
    result: WorkerResult,
) -> list[Failure]:
    by_id = {failure.failure_id: failure for failure in existing}
    merged = list(existing)
    for failure in incoming:
        if failure.failure_id in by_id:
            continue
        if failure.created_by_worker_job_id is None:
            failure = failure.model_copy(
                update={"created_by_worker_job_id": result.worker_job_id}
            )
        merged.append(failure)
        by_id[failure.failure_id] = failure
    return merged


def _blocking_worker_failure(
    result: WorkerResult,
    *,
    source: FailureSource,
    title: str,
    description: str,
    evidence_path: str | None,
) -> Failure:
    return Failure(
        failure_id=prefixed_id("failure"),
        source=source,
        severity=Severity.BLOCKING,
        title=title,
        description=description,
        evidence_paths=[evidence_path] if evidence_path is not None else [],
        status=FailureStatus.OPEN,
        created_by_worker_job_id=result.worker_job_id,
        created_at=result.completed_at,
    )


def _merge_assumptions(
    existing: list[Assumption],
    incoming: list[Assumption],
) -> list[Assumption]:
    seen = {assumption.assumption_id for assumption in existing}
    merged = list(existing)
    for assumption in incoming:
        if assumption.assumption_id not in seen:
            merged.append(assumption)
            seen.add(assumption.assumption_id)
    return merged


def _merge_questions(
    existing: list[ClarificationQuestion],
    incoming: list[ClarificationQuestion],
) -> list[ClarificationQuestion]:
    seen = {question.question_id for question in existing}
    merged = list(existing)
    for question in incoming:
        if question.question_id not in seen:
            merged.append(question)
            seen.add(question.question_id)
    return merged


def _resolve_failures(
    failures: list[Failure],
    *,
    source: str,
    worker_job_id: str,
    resolved_by_path: str | None,
    resolved_at: Any,
) -> list[Failure]:
    resolved: list[Failure] = []
    for failure in failures:
        if (
            _value(failure.status) == FailureStatus.OPEN.value
            and _value(failure.severity) == Severity.BLOCKING.value
            and _value(failure.source) == source
        ):
            resolved.append(
                failure.model_copy(
                    update={
                        "status": FailureStatus.RESOLVED.value,
                        "resolved_by_worker_job_id": worker_job_id,
                        "resolved_by_path": resolved_by_path,
                        "resolved_at": resolved_at,
                    }
                )
            )
        else:
            resolved.append(failure)
    return resolved


def _resolve_repaired_failures(
    failures: list[Failure],
    *,
    result: WorkerResult,
    worker_input: Any,
    resolved_by_path: str | None,
) -> list[Failure]:
    selected_ids = set(worker_input.context.selected_failure_ids)
    repair_targets = set()
    if worker_input.worker_config is not None:
        repair_targets.update(
            _string_list(worker_input.worker_config.repair_targets)
        )

    resolved: list[Failure] = []
    for failure in failures:
        if _should_resolve_repaired_failure(failure, selected_ids, repair_targets):
            resolved.append(
                failure.model_copy(
                    update={
                        "status": FailureStatus.RESOLVED.value,
                        "resolved_by_worker_job_id": result.worker_job_id,
                        "resolved_by_path": resolved_by_path,
                        "resolved_at": result.completed_at,
                    }
                )
            )
        else:
            resolved.append(failure)
    return resolved


def _should_resolve_repaired_failure(
    failure: Failure,
    selected_ids: set[str],
    repair_targets: set[str],
) -> bool:
    if (
        _value(failure.status) != FailureStatus.OPEN.value
        or _value(failure.severity) != Severity.BLOCKING.value
    ):
        return False
    if selected_ids:
        return failure.failure_id in selected_ids
    if not repair_targets:
        return True
    source = _value(failure.source)
    target_aliases = {
        "compile": {"compile", "compile_failure"},
        "test": {"test", "test_failure"},
        "formal": {"formal", "formal_validation_failure"},
    }
    return any(target in target_aliases.get(source, {source}) for target in repair_targets)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_value(item) for item in value if item is not None]


def _remove_active_job(
    active_worker_jobs: list[WorkerJobRef],
    worker_job_id: str,
) -> list[WorkerJobRef]:
    return [
        job
        for job in active_worker_jobs
        if job.worker_job_id != worker_job_id
    ]


def _has_open_blocking_failure(failures: list[Failure]) -> bool:
    return any(
        _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == Severity.BLOCKING.value
        for failure in failures
    )


def _append_unique(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]


def _first_report_path(result: WorkerResult, token: str) -> str | None:
    return next((path for path in result.report_paths if token in path.lower()), None)


def _repair_changed_code(result: WorkerResult) -> bool:
    return any(
        path.lower().endswith((".st", ".scl", ".fbd", ".diff", ".patch"))
        for path in result.written_paths
    )


def _current_file_field_for_path(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith((".st", ".scl", ".fbd")) or (
        lower.endswith(".xml") and "io_contract" not in lower
    ):
        return "current_code"
    if "requirements" in lower:
        return "requirements"
    if "io_contract" in lower:
        return "current_io_contract"
    if "test_cases" in lower:
        return "latest_test_cases"
    if "test_report" in lower:
        return "latest_test_report"
    if "failing_trace" in lower:
        return "latest_failing_trace"
    if "formal_properties" in lower:
        return "latest_formal_properties"
    if "formal_report" in lower:
        return "latest_formal_report"
    if "counterexample" in lower:
        return "latest_counterexample"
    if lower.endswith((".diff", ".patch")) or "patch" in lower:
        return "latest_patch"
    if "repair_summary" in lower:
        return "latest_repair_summary"
    if "gate_report" in lower:
        return "latest_gate_report"
    if "final_report" in lower:
        return "final_report"
    return None


def _record_mismatch(
    mismatches: dict[str, dict[str, str]],
    field: str,
    actual: str,
    expected: str,
) -> None:
    if actual != expected:
        mismatches[field] = {"expected": expected, "actual": actual}


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
