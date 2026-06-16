"""Quality Gate assessment and persistence service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_artifact_id, new_event_id
from app.core.time import utc_now
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    ArtifactVisibility,
    ClarificationStatus,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    FailureStatus,
    Severity,
    TaskState,
    TaskType,
)
from app.repositories.gate_repo import GateResultRecord, GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService


REQUIREMENTS_GATE = "requirements_gate"
CODE_GATE = "code_gate"
TEST_GATE = "test_gate"
FORMAL_GATE = "formal_gate"
REGRESSION_GATE = "regression_gate"
FINAL_GATE = "final_gate"
QUALITY_GATE_TYPES = (
    REQUIREMENTS_GATE,
    CODE_GATE,
    TEST_GATE,
    FORMAL_GATE,
    REGRESSION_GATE,
    FINAL_GATE,
)

PASSED = "passed"
FAILED = "failed"
DEVELOPMENT_TASK_TYPES = {
    TaskType.NEW_PLC_DEVELOPMENT.value,
    TaskType.MODIFY_EXISTING_CODE.value,
    TaskType.TEST_EXISTING_CODE.value,
    TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
    TaskType.REPAIR_EXISTING_CODE.value,
    TaskType.PROJECT_LEVEL_DEVELOPMENT.value,
}
FORMAL_SAFETY_SIGNALS = (
    "has_safety_constraints",
    "has_emergency_stop",
    "has_interlock",
    "has_fault_latching",
    "has_mode_switching",
    "has_state_machine",
)
DIFFICULTY_RANK = {
    "L0": 0,
    "L1": 1,
    "L2": 2,
    "L3": 3,
    "L4": 4,
}


@dataclass(frozen=True)
class GateOutcome:
    """Result for one named Quality Gate check."""

    gate_type: str
    status: str
    blocking: bool
    message: str
    evidence_artifact_ids: tuple[str, ...] = ()
    details: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.status == PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_type": self.gate_type,
            "status": self.status,
            "blocking": self.blocking,
            "message": self.message,
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class QualityGateAssessment:
    """Aggregate Quality Gate assessment for a TaskState."""

    task_id: str
    status: str
    blocking: bool
    message: str
    outcomes: tuple[GateOutcome, ...]
    evidence_artifact_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == PASSED

    def outcome_for(self, gate_type: str) -> GateOutcome:
        for outcome in self.outcomes:
            if outcome.gate_type == gate_type:
                return outcome
        raise KeyError(f"unknown gate outcome: {gate_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "blocking": self.blocking,
            "message": self.message,
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


@dataclass(frozen=True)
class QualityGateRunResult:
    """Persisted Quality Gate run output."""

    task: TaskState
    assessment: QualityGateAssessment
    gate_report: ArtifactRef
    gate_results: tuple[GateResultRecord, ...]


def assess_quality_gate(state: TaskState) -> QualityGateAssessment:
    """Assess final delivery readiness without mutating state."""

    outcomes = (
        _assess_requirements_gate(state),
        _assess_code_gate(state),
        _assess_test_gate(state),
        _assess_formal_gate(state),
        _assess_regression_gate(state),
        _assess_final_gate(state),
    )
    blocking = any(outcome.blocking for outcome in outcomes)
    status = FAILED if blocking else PASSED
    failed_gates = [outcome.gate_type for outcome in outcomes if outcome.blocking]
    if failed_gates:
        message = "Quality Gate failed: " + ", ".join(failed_gates)
    else:
        message = "Quality Gate passed."

    return QualityGateAssessment(
        task_id=state.task_id,
        status=status,
        blocking=blocking,
        message=message,
        outcomes=outcomes,
        evidence_artifact_ids=_dedupe(
            artifact_id
            for outcome in outcomes
            for artifact_id in outcome.evidence_artifact_ids
        ),
    )


class QualityGateService:
    """Runs Quality Gate checks and records their audit trail."""

    def __init__(self, session: Session, artifact_root: Path) -> None:
        self.task_repository = TaskRepository(session)
        self.artifact_store = ArtifactStore(session=session, artifact_root=artifact_root)
        self.event_service = EventService(session)
        self.gate_result_repository = GateResultRepository(session)

    def run_quality_gate(self, task_id: str) -> QualityGateRunResult:
        started_at = utc_now()
        self.task_repository.get_task(task_id)
        self.event_service.append_event(
            _build_gate_event(
                task_id=task_id,
                event_type=EventType.GATE_STARTED,
                title="Quality Gate started",
                message="Quality Gate started evaluating final delivery readiness.",
                created_at=started_at,
                artifact_ids=None,
                payload={"task_id": task_id},
            )
        )

        task = self.task_repository.get_task(task_id)
        assessment = assess_quality_gate(task)
        report_artifact = self._write_gate_report(task, assessment, created_at=started_at)
        gate_results = self._persist_gate_results(
            task_id=task_id,
            assessment=assessment,
            created_at=started_at,
        )

        task_after_report = self.task_repository.get_task(task_id)
        updated_task = task_after_report.model_copy(
            deep=True,
            update={
                "gates": task_after_report.gates.model_copy(
                    update={"can_finish_as_success": assessment.passed}
                ),
                "updated_at": started_at,
            },
        )
        self.task_repository.update_task_state(updated_task)

        terminal_event_type = (
            EventType.GATE_PASSED if assessment.passed else EventType.GATE_FAILED
        )
        terminal_severity = (
            EventSeverity.INFO if assessment.passed else EventSeverity.ERROR
        )
        self.event_service.append_event(
            _build_gate_event(
                task_id=task_id,
                event_type=terminal_event_type,
                title=(
                    "Quality Gate passed"
                    if assessment.passed
                    else "Quality Gate failed"
                ),
                message=assessment.message,
                created_at=started_at,
                artifact_ids=[report_artifact.artifact_id],
                payload={
                    "task_id": task_id,
                    "status": assessment.status,
                    "blocking": assessment.blocking,
                    "gate_report_artifact_id": report_artifact.artifact_id,
                    "failed_gates": [
                        outcome.gate_type
                        for outcome in assessment.outcomes
                        if outcome.blocking
                    ],
                },
                severity=terminal_severity,
            )
        )

        return QualityGateRunResult(
            task=self.task_repository.get_task(task_id),
            assessment=assessment,
            gate_report=report_artifact,
            gate_results=tuple(gate_results),
        )

    def _write_gate_report(
        self,
        task: TaskState,
        assessment: QualityGateAssessment,
        *,
        created_at: datetime,
    ) -> ArtifactRef:
        version = self._next_gate_report_version(task.task_id)
        report = {
            "schema_version": "router.v1",
            "kind": "quality_gate_report",
            "created_at": created_at.isoformat(),
            "task": {
                "task_id": task.task_id,
                "status": _value(task.status),
                "phase": _value(task.phase),
                "task_type": _value(task.task_type),
                "difficulty_level": _value(task.difficulty.level),
            },
            "assessment": assessment.to_dict(),
        }
        artifact = self.artifact_store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type=ArtifactType.GATE_REPORT,
                version=version,
                name=f"gate_report_v{version}.json",
                content=report,
                summary=assessment.message,
                visibility=ArtifactVisibility.USER,
                created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
                metadata={"tags": ["quality_gate", assessment.status]},
                artifact_id=new_artifact_id(),
                created_at=created_at,
                mime_type="application/json",
            )
        ).artifact
        return self.artifact_store.get_artifact_ref(artifact.artifact_id)

    def _persist_gate_results(
        self,
        *,
        task_id: str,
        assessment: QualityGateAssessment,
        created_at: datetime,
    ) -> list[GateResultRecord]:
        records: list[GateResultRecord] = []
        for outcome in assessment.outcomes:
            records.append(
                self.gate_result_repository.create_result(
                    task_id=task_id,
                    gate_type=outcome.gate_type,
                    status=outcome.status,
                    blocking=outcome.blocking,
                    evidence_artifact_ids=list(outcome.evidence_artifact_ids),
                    result={
                        "aggregate_status": assessment.status,
                        "aggregate_blocking": assessment.blocking,
                        **outcome.to_dict(),
                    },
                    created_at=created_at,
                )
            )
        return records

    def _next_gate_report_version(self, task_id: str) -> int:
        reports = [
            artifact
            for artifact in self.artifact_store.list_task_artifacts(task_id)
            if _value(artifact.type) == ArtifactType.GATE_REPORT.value
        ]
        if not reports:
            return 1
        return max(report.version for report in reports) + 1


def _assess_requirements_gate(state: TaskState) -> GateOutcome:
    evidence = _artifact_ids(
        state.current_artifacts.raw_user_request,
        state.current_artifacts.requirements_ir,
    )
    if state.raw_user_request.strip() or evidence:
        return _passed(
            REQUIREMENTS_GATE,
            "Requirement source is available.",
            evidence_artifact_ids=evidence,
        )
    return _failed(
        REQUIREMENTS_GATE,
        "Quality Gate requires a raw user request or requirements artifact.",
    )


def _assess_code_gate(state: TaskState) -> GateOutcome:
    if not _requires_code(state):
        return _passed(CODE_GATE, "Task does not require PLC code evidence.")

    current_code = state.current_artifacts.current_code
    if current_code is not None:
        return _passed(
            CODE_GATE,
            "Current PLC code artifact is available.",
            evidence_artifact_ids=(current_code.artifact_id,),
        )

    return _failed(
        CODE_GATE,
        "Quality Gate requires a current PLC code artifact before delivery.",
    )


def _assess_test_gate(state: TaskState) -> GateOutcome:
    if not _requires_test(state):
        return _passed(TEST_GATE, "Task does not require test evidence.")

    report = state.current_artifacts.latest_test_report
    if report is not None and state.gates.latest_test_passed is True:
        return _passed(
            TEST_GATE,
            "Latest test report passed.",
            evidence_artifact_ids=(report.artifact_id,),
        )

    evidence = _artifact_ids(report)
    return _failed(
        TEST_GATE,
        "Quality Gate requires a passing latest test report before delivery.",
        evidence_artifact_ids=evidence,
        details={"latest_test_passed": state.gates.latest_test_passed},
    )


def _assess_formal_gate(state: TaskState) -> GateOutcome:
    if not _requires_formal(state):
        return _passed(FORMAL_GATE, "Task does not require formal evidence.")

    report = state.current_artifacts.latest_formal_report
    if report is not None and state.gates.latest_formal_passed is True:
        return _passed(
            FORMAL_GATE,
            "Latest formal verification report passed.",
            evidence_artifact_ids=(report.artifact_id,),
        )

    evidence = _artifact_ids(report, state.current_artifacts.latest_counterexample)
    return _failed(
        FORMAL_GATE,
        "Quality Gate requires a passing latest formal verification report before delivery.",
        evidence_artifact_ids=evidence,
        details={"latest_formal_passed": state.gates.latest_formal_passed},
    )


def _assess_regression_gate(state: TaskState) -> GateOutcome:
    blockers: list[str] = []
    if state.gates.regression_required:
        blockers.append("regression_required")
    if state.gates.formal_regression_required:
        blockers.append("formal_regression_required")

    if not blockers:
        return _passed(REGRESSION_GATE, "No pending regression gate flags remain.")

    evidence = _artifact_ids(
        state.current_artifacts.latest_patch,
        state.current_artifacts.latest_test_report,
        state.current_artifacts.latest_formal_report,
    )
    return _failed(
        REGRESSION_GATE,
        "Quality Gate requires pending regression work to pass before delivery.",
        evidence_artifact_ids=evidence,
        details={"pending": blockers},
    )


def _assess_final_gate(state: TaskState) -> GateOutcome:
    blockers: list[str] = []
    evidence: list[str] = []

    if _has_open_required_clarification(state):
        blockers.append("required_clarification_open")
    if state.active_worker_jobs:
        blockers.append("active_worker_jobs")
    if state.gates.has_blocking_failure:
        blockers.append("has_blocking_failure")

    open_blocking_failures = _open_blocking_failures(state)
    if open_blocking_failures:
        blockers.append("open_blocking_failure")
        evidence.extend(
            artifact_id
            for failure in open_blocking_failures
            for artifact_id in failure.evidence_artifact_ids
        )

    if not blockers:
        return _passed(FINAL_GATE, "No final delivery blockers remain.")

    return _failed(
        FINAL_GATE,
        "Quality Gate requires final delivery blockers to be resolved.",
        evidence_artifact_ids=_dedupe(evidence),
        details={"blockers": blockers},
    )


def _requires_code(state: TaskState) -> bool:
    return _value(state.task_type) in DEVELOPMENT_TASK_TYPES


def _requires_test(state: TaskState) -> bool:
    return (
        state.gates.test_required
        or state.difficulty.requires_test
        or _difficulty_at_least(state, "L2")
    )


def _requires_formal(state: TaskState) -> bool:
    return (
        state.gates.formal_required
        or state.difficulty.requires_formal
        or _difficulty_at_least(state, "L3")
        or _has_formal_safety_signal(state)
    )


def _has_formal_safety_signal(state: TaskState) -> bool:
    return any(
        bool(getattr(state.difficulty.signals, signal))
        for signal in FORMAL_SAFETY_SIGNALS
    )


def _difficulty_at_least(state: TaskState, minimum: str) -> bool:
    return DIFFICULTY_RANK[_value(state.difficulty.level)] >= DIFFICULTY_RANK[minimum]


def _has_open_required_clarification(state: TaskState) -> bool:
    return any(
        question.required and _value(question.status) == ClarificationStatus.OPEN.value
        for question in state.unresolved_questions
    )


def _open_blocking_failures(state: TaskState) -> list[Any]:
    return [
        failure
        for failure in state.failures
        if _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == Severity.BLOCKING.value
    ]


def _passed(
    gate_type: str,
    message: str,
    *,
    evidence_artifact_ids: tuple[str, ...] = (),
    details: dict[str, Any] | None = None,
) -> GateOutcome:
    return GateOutcome(
        gate_type=gate_type,
        status=PASSED,
        blocking=False,
        message=message,
        evidence_artifact_ids=_dedupe(evidence_artifact_ids),
        details=details,
    )


def _failed(
    gate_type: str,
    message: str,
    *,
    evidence_artifact_ids: tuple[str, ...] = (),
    details: dict[str, Any] | None = None,
) -> GateOutcome:
    return GateOutcome(
        gate_type=gate_type,
        status=FAILED,
        blocking=True,
        message=message,
        evidence_artifact_ids=_dedupe(evidence_artifact_ids),
        details=details,
    )


def _build_gate_event(
    *,
    task_id: str,
    event_type: EventType,
    title: str,
    message: str,
    created_at: datetime,
    artifact_ids: list[str] | None,
    payload: dict[str, Any],
    severity: EventSeverity = EventSeverity.INFO,
) -> Any:
    from app.models.router_schema import RouterEvent

    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.QUALITY_GATE),
        severity=severity,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(artifact_ids=artifact_ids),
        payload=payload,
        created_at=created_at,
    )


def _artifact_ids(*refs: ArtifactRef | None) -> tuple[str, ...]:
    return _dedupe(ref.artifact_id for ref in refs if ref is not None)


def _dedupe(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
