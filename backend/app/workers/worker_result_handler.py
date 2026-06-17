"""Apply Router worker results to persisted task state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import RepositoryNotFoundError
from app.models.router_schema import (
    Artifact,
    ArtifactRef,
    ArtifactType,
    Assumption,
    ClarificationQuestion,
    CurrentArtifacts,
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
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRecord, WorkerJobRepository


ARTIFACT_POINTER_FIELD_BY_TYPE: dict[str, str] = {
    ArtifactType.RAW_USER_REQUEST.value: "raw_user_request",
    ArtifactType.REQUIREMENTS_IR.value: "requirements_ir",
    ArtifactType.PLC_CODE.value: "current_code",
    ArtifactType.IO_CONTRACT.value: "current_io_contract",
    ArtifactType.TEST_CASES.value: "latest_test_cases",
    ArtifactType.TEST_REPORT.value: "latest_test_report",
    ArtifactType.FAILING_TRACE.value: "latest_failing_trace",
    ArtifactType.FORMAL_PROPERTIES.value: "latest_formal_properties",
    ArtifactType.FORMAL_REPORT.value: "latest_formal_report",
    ArtifactType.COUNTEREXAMPLE.value: "latest_counterexample",
    ArtifactType.PATCH.value: "latest_patch",
    ArtifactType.REPAIR_SUMMARY.value: "latest_repair_summary",
    ArtifactType.GATE_REPORT.value: "latest_gate_report",
    ArtifactType.FINAL_REPORT.value: "final_report",
}


EVIDENCE_ARTIFACT_TYPES = {
    ArtifactType.TEST_REPORT.value,
    ArtifactType.FAILING_TRACE.value,
    ArtifactType.FORMAL_REPORT.value,
    ArtifactType.COUNTEREXAMPLE.value,
}


class WorkerResultHandlerError(Exception):
    """Base class for worker result handling failures."""


class WorkerResultHandlerMissingTaskError(WorkerResultHandlerError):
    """Raised when a worker result references a missing task."""


class WorkerResultHandlerMissingWorkerJobError(WorkerResultHandlerError):
    """Raised when a worker result references a missing worker job."""


class WorkerResultHandlerIdentityError(WorkerResultHandlerError):
    """Raised when a worker result does not match persisted job identity."""


class WorkerResultHandlerInvalidArtifactError(WorkerResultHandlerError):
    """Raised when a worker result references invalid produced artifacts."""


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
        self.artifact_repository = ArtifactRepository(session)

    def handle_worker_result(self, result: WorkerResult) -> WorkerResultHandleResult:
        """Validate and apply one worker result to its persisted task."""

        task = self._get_task(result.task_id)
        job = self._get_worker_job(result.worker_job_id)
        self._validate_identity(result, job)

        if result.worker_job_id in task.completed_worker_job_ids:
            return WorkerResultHandleResult(
                task=task,
                applied=False,
                summary=f"Worker result already applied: {result.worker_job_id}",
            )

        artifacts = self._validate_produced_artifacts(result)
        updated = self._apply_result(task, result, artifacts)
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

    def _validate_produced_artifacts(
        self,
        result: WorkerResult,
    ) -> dict[str, Artifact]:
        artifacts: dict[str, Artifact] = {}
        for artifact_ref in result.produced_artifacts:
            try:
                artifact = self.artifact_repository.get_artifact(
                    artifact_ref.artifact_id
                )
            except RepositoryNotFoundError as exc:
                raise WorkerResultHandlerInvalidArtifactError(
                    f"produced artifact not found: {artifact_ref.artifact_id}"
                ) from exc

            if artifact.task_id != result.task_id:
                raise WorkerResultHandlerInvalidArtifactError(
                    "produced artifact belongs to another task: "
                    f"{artifact.artifact_id}"
                )
            if _value(artifact.type) != _value(artifact_ref.type):
                raise WorkerResultHandlerInvalidArtifactError(
                    "produced artifact type mismatch: "
                    f"{artifact.artifact_id}"
                )
            if artifact.version != artifact_ref.version:
                raise WorkerResultHandlerInvalidArtifactError(
                    "produced artifact version mismatch: "
                    f"{artifact.artifact_id}"
                )
            artifacts[artifact.artifact_id] = artifact
        return artifacts

    def _apply_result(
        self,
        task: TaskState,
        result: WorkerResult,
        artifacts: dict[str, Artifact],
    ) -> TaskState:
        execution_status = _value(result.execution_status)
        worker_type = _value(result.worker_type)
        outcome_status = _value(result.outcome.status)
        completed_at = result.completed_at

        current_artifacts = task.current_artifacts
        failures = _merge_failures(task.failures, result.failures, result)
        assumptions = _merge_assumptions(task.assumptions, result.assumptions)
        questions = task.unresolved_questions
        gates = task.gates
        runtime_limits = task.runtime_limits
        status = task.status
        phase = task.phase

        if execution_status == WorkerExecutionStatus.COMPLETED.value:
            current_artifacts = _project_artifacts(
                current_artifacts,
                result.produced_artifacts,
            )
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
                    current_artifacts,
                )
            elif worker_type == WorkerType.PLC_TEST.value:
                failures, gates = _apply_test_result(
                    failures,
                    gates,
                    result,
                    current_artifacts,
                    outcome_status,
                )
            elif worker_type == WorkerType.PLC_FORMAL.value:
                failures, gates = _apply_formal_result(
                    failures,
                    gates,
                    result,
                    current_artifacts,
                    outcome_status,
                )
            elif worker_type == WorkerType.PLC_REPAIR.value:
                gates, runtime_limits, phase = _apply_repair_result(
                    task,
                    gates,
                    runtime_limits,
                    result,
                    current_artifacts,
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
                "current_artifacts": current_artifacts,
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
    current_artifacts: CurrentArtifacts,
) -> tuple[GateState, str]:
    if outcome_status != WorkerOutcomeStatus.PASSED.value:
        return gates, task.phase

    phase = task.phase
    if (
        gates.test_required
        or task.difficulty.requires_test
        or task.current_artifacts.latest_test_report is not None
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
    current_artifacts: CurrentArtifacts,
    outcome_status: str,
) -> tuple[list[Failure], GateState]:
    if outcome_status == WorkerOutcomeStatus.PASSED.value:
        failures = _resolve_failures(
            failures,
            source=FailureSource.TEST.value,
            worker_job_id=result.worker_job_id,
            resolved_by_artifact_id=_artifact_id(
                current_artifacts.latest_test_report
            ),
            resolved_at=result.completed_at,
        )
        gates = gates.model_copy(
            update={
                "latest_test_passed": True,
                "regression_required": False,
            }
        )
    elif outcome_status == WorkerOutcomeStatus.FAILED.value:
        gates = gates.model_copy(update={"latest_test_passed": False})
    return failures, gates


def _apply_formal_result(
    failures: list[Failure],
    gates: GateState,
    result: WorkerResult,
    current_artifacts: CurrentArtifacts,
    outcome_status: str,
) -> tuple[list[Failure], GateState]:
    if outcome_status == WorkerOutcomeStatus.PASSED.value:
        failures = _resolve_failures(
            failures,
            source=FailureSource.FORMAL.value,
            worker_job_id=result.worker_job_id,
            resolved_by_artifact_id=_artifact_id(
                current_artifacts.latest_formal_report
            ),
            resolved_at=result.completed_at,
        )
        gates = gates.model_copy(
            update={
                "latest_formal_passed": True,
                "formal_regression_required": False,
            }
        )
    elif outcome_status == WorkerOutcomeStatus.FAILED.value:
        gates = gates.model_copy(update={"latest_formal_passed": False})
    return failures, gates


def _apply_repair_result(
    task: TaskState,
    gates: GateState,
    runtime_limits: RuntimeLimits,
    result: WorkerResult,
    current_artifacts: CurrentArtifacts,
    outcome_status: str,
) -> tuple[GateState, RuntimeLimits, str]:
    if outcome_status != WorkerOutcomeStatus.PASSED.value:
        return gates, runtime_limits, task.phase

    has_formal_failure = any(
        _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == Severity.BLOCKING.value
        and _value(failure.source) == FailureSource.FORMAL.value
        for failure in task.failures
    )
    runtime_limits = runtime_limits.model_copy(
        update={"repair_rounds": runtime_limits.repair_rounds + 1}
    )
    gates = gates.model_copy(
        update={
            "latest_test_passed": None,
            "latest_formal_passed": None,
            "regression_required": True,
            "formal_regression_required": (
                True if has_formal_failure else gates.formal_regression_required
            ),
        }
    )
    return gates, runtime_limits, TaskPhase.REGRESSION.value


def _project_artifacts(
    current_artifacts: CurrentArtifacts,
    produced_artifacts: list[ArtifactRef],
) -> CurrentArtifacts:
    updates: dict[str, Any] = {
        "all_artifact_ids": list(current_artifacts.all_artifact_ids)
    }
    for artifact_ref in produced_artifacts:
        if artifact_ref.artifact_id not in updates["all_artifact_ids"]:
            updates["all_artifact_ids"].append(artifact_ref.artifact_id)
        pointer_field = ARTIFACT_POINTER_FIELD_BY_TYPE.get(_value(artifact_ref.type))
        if pointer_field is not None:
            updates[pointer_field] = artifact_ref
    return current_artifacts.model_copy(update=updates)


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
    resolved_by_artifact_id: str | None,
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
                        "resolved_by_artifact_id": resolved_by_artifact_id,
                        "resolved_at": resolved_at,
                    }
                )
            )
        else:
            resolved.append(failure)
    return resolved


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


def _artifact_id(artifact_ref: ArtifactRef | None) -> str | None:
    return artifact_ref.artifact_id if artifact_ref is not None else None


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
