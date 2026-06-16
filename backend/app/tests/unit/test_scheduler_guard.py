import json
from pathlib import Path
from typing import Any

import pytest

from app.models.router_schema import (
    ArtifactRef,
    ClarificationQuestion,
    CurrentArtifacts,
    Failure,
    FailureReproduction,
    GateState,
    RuntimeLimits,
    TaskState,
)
from app.services.scheduler_guard import (
    ProposedWorkerJob,
    SchedulerGuardViolation,
    SchedulerGuardViolationCode,
    validate_finish_task,
    validate_parallel_jobs,
    validate_worker_call,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def task_state() -> TaskState:
    return TaskState.model_validate(load_fixture("task_state.valid.json"))


def artifact_ref(artifact_id: str, artifact_type: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        type=artifact_type,
        version=1,
        uri=f"local://artifacts/task-001/{artifact_id}",
        summary=f"{artifact_type} artifact",
    )


def raw_request_ref() -> ArtifactRef:
    return artifact_ref("artifact-raw-request-001", "raw_user_request")


def requirements_ref() -> ArtifactRef:
    return artifact_ref("artifact-requirements-001", "requirements_ir")


def code_ref() -> ArtifactRef:
    return artifact_ref("artifact-code-001", "plc_code")


def report_ref() -> ArtifactRef:
    return artifact_ref("artifact-test-report-001", "test_report")


def running_task(
    *,
    current_code: ArtifactRef | None = None,
    requirements: ArtifactRef | None = None,
    gates: GateState | None = None,
    runtime_limits: RuntimeLimits | None = None,
    failures: list[Failure] | None = None,
) -> TaskState:
    base = task_state()
    raw = raw_request_ref()
    all_artifacts = [raw.artifact_id]
    if requirements is not None:
        all_artifacts.append(requirements.artifact_id)
    if current_code is not None:
        all_artifacts.append(current_code.artifact_id)

    return base.model_copy(
        deep=True,
        update={
            "status": "running",
            "phase": "planning",
            "task_type": "new_plc_development",
            "current_artifacts": CurrentArtifacts(
                raw_user_request=raw,
                requirements_ir=requirements,
                current_code=current_code,
                all_artifact_ids=all_artifacts,
            ),
            "gates": gates or base.gates,
            "runtime_limits": runtime_limits or base.runtime_limits,
            "failures": failures or [],
        },
    )


def blocking_failure(state: TaskState) -> Failure:
    return Failure(
        failure_id="failure-blocking-001",
        source="test",
        severity="blocking",
        title="Blocking test failure",
        description="The generated code violates a blocking test.",
        expected="The output remains false.",
        actual="The output became true.",
        reproduction=FailureReproduction(
            input_trace_artifact_id=report_ref().artifact_id
        ),
        evidence_artifact_ids=[report_ref().artifact_id],
        status="open",
        created_by_worker_job_id="worker-job-test-001",
        created_at=state.created_at,
    )


def open_required_question(state: TaskState) -> ClarificationQuestion:
    return ClarificationQuestion(
        question_id="question-001",
        question="Which PLC platform should be targeted?",
        reason="Target platform affects generated code.",
        required=True,
        status="open",
        asked_at=state.created_at,
    )


def assert_guard_code(
    expected_code: SchedulerGuardViolationCode,
    func: Any,
    *args: Any,
) -> SchedulerGuardViolation:
    with pytest.raises(SchedulerGuardViolation) as exc_info:
        func(*args)
    assert exc_info.value.code == expected_code
    assert exc_info.value.message
    assert isinstance(exc_info.value.details, dict)
    return exc_info.value


def test_intake_dispatch_is_rejected_before_classification() -> None:
    state = task_state()

    violation = assert_guard_code(
        SchedulerGuardViolationCode.INTAKE_NOT_CLASSIFIED,
        validate_worker_call,
        state,
        "plc-dev",
        [raw_request_ref()],
    )

    assert violation.details["status"] == "created"


def test_waiting_user_dispatch_is_rejected() -> None:
    base = running_task(requirements=requirements_ref())
    state = base.model_copy(
        deep=True,
        update={
            "status": "waiting_user",
            "phase": "clarifying",
            "unresolved_questions": [open_required_question(base)],
        },
    )

    assert_guard_code(
        SchedulerGuardViolationCode.WAITING_FOR_USER,
        validate_worker_call,
        state,
        "plc-dev",
        [requirements_ref()],
    )


def test_test_before_dev_is_rejected() -> None:
    state = running_task(requirements=requirements_ref())

    assert_guard_code(
        SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
        validate_worker_call,
        state,
        "plc-test",
        [requirements_ref()],
    )


def test_formal_before_dev_is_rejected() -> None:
    state = running_task(requirements=requirements_ref())

    assert_guard_code(
        SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
        validate_worker_call,
        state,
        "plc-formal",
        [requirements_ref()],
    )


def test_worker_call_limit_is_rejected() -> None:
    base = task_state()
    exhausted_limits = base.runtime_limits.model_copy(
        update={"worker_calls_used": 20, "max_worker_calls": 20}
    )
    state = running_task(
        requirements=requirements_ref(),
        runtime_limits=exhausted_limits,
    )

    assert_guard_code(
        SchedulerGuardViolationCode.WORKER_CALL_LIMIT_EXCEEDED,
        validate_worker_call,
        state,
        "plc-dev",
        [requirements_ref()],
    )


def test_repair_before_failure_is_rejected() -> None:
    code = code_ref()
    state = running_task(current_code=code, requirements=requirements_ref())

    assert_guard_code(
        SchedulerGuardViolationCode.NO_OPEN_BLOCKING_FAILURE,
        validate_worker_call,
        state,
        "plc-repair",
        [code, report_ref()],
    )


def test_repair_without_evidence_is_rejected() -> None:
    code = code_ref()
    state = running_task(current_code=code, requirements=requirements_ref())
    state = state.model_copy(deep=True, update={"failures": [blocking_failure(state)]})

    assert_guard_code(
        SchedulerGuardViolationCode.MISSING_REPAIR_EVIDENCE,
        validate_worker_call,
        state,
        "plc-repair",
        [code],
    )


def test_fourth_repair_round_is_rejected() -> None:
    code = code_ref()
    base = task_state()
    repair_limit_reached = base.runtime_limits.model_copy(
        update={"repair_rounds": 3, "max_repair_rounds": 3}
    )
    state = running_task(
        current_code=code,
        requirements=requirements_ref(),
        runtime_limits=repair_limit_reached,
    )
    state = state.model_copy(deep=True, update={"failures": [blocking_failure(state)]})

    assert_guard_code(
        SchedulerGuardViolationCode.REPAIR_LIMIT_REACHED,
        validate_worker_call,
        state,
        "plc-repair",
        [code, report_ref()],
    )


def test_parallel_batch_exceeding_limit_is_rejected() -> None:
    base = task_state()
    almost_full = base.runtime_limits.model_copy(
        update={"active_parallel_workers": 3, "max_parallel_workers": 4}
    )
    state = running_task(
        requirements=requirements_ref(),
        runtime_limits=almost_full,
    )

    assert_guard_code(
        SchedulerGuardViolationCode.PARALLEL_LIMIT_EXCEEDED,
        validate_parallel_jobs,
        state,
        [
            ProposedWorkerJob("plc-dev", [requirements_ref()]),
            ProposedWorkerJob("plc-dev", [requirements_ref()]),
        ],
    )


def test_parallel_invalid_member_rejects_entire_batch() -> None:
    state = running_task(requirements=requirements_ref())

    assert_guard_code(
        SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
        validate_parallel_jobs,
        state,
        [ProposedWorkerJob("plc-test", [requirements_ref()])],
    )


def test_parallel_repair_is_rejected() -> None:
    code = code_ref()
    state = running_task(current_code=code, requirements=requirements_ref())
    state = state.model_copy(deep=True, update={"failures": [blocking_failure(state)]})

    assert_guard_code(
        SchedulerGuardViolationCode.PARALLEL_REPAIR_UNSUPPORTED,
        validate_parallel_jobs,
        state,
        [ProposedWorkerJob("plc-repair", [code, report_ref()])],
    )


def test_finish_succeeded_with_blocking_failure_is_rejected() -> None:
    base = task_state()
    gates = base.gates.model_copy(update={"has_blocking_failure": True})
    state = running_task(gates=gates)

    assert_guard_code(
        SchedulerGuardViolationCode.BLOCKING_FAILURE_PRESENT,
        validate_finish_task,
        state,
        "succeeded",
    )


def test_l3_task_skipping_formal_is_rejected() -> None:
    base = task_state()
    gates = base.gates.model_copy(
        update={
            "test_required": True,
            "formal_required": True,
            "latest_test_passed": True,
            "latest_formal_passed": None,
            "has_blocking_failure": False,
        }
    )
    state = running_task(gates=gates)

    assert_guard_code(
        SchedulerGuardViolationCode.REQUIRED_FORMAL_MISSING,
        validate_finish_task,
        state,
        "succeeded",
    )


def test_regression_required_finish_is_rejected() -> None:
    base = task_state()
    gates = base.gates.model_copy(
        update={
            "test_required": False,
            "formal_required": False,
            "regression_required": True,
            "has_blocking_failure": False,
        }
    )
    state = running_task(gates=gates)

    assert_guard_code(
        SchedulerGuardViolationCode.REGRESSION_REQUIRED,
        validate_finish_task,
        state,
        "succeeded",
    )


def test_non_success_finish_is_not_blocked_by_success_rules() -> None:
    base = task_state()
    gates = base.gates.model_copy(
        update={
            "test_required": True,
            "formal_required": True,
            "regression_required": True,
            "formal_regression_required": True,
            "has_blocking_failure": True,
        }
    )
    state = running_task(gates=gates)

    validate_finish_task(state, "partial_failed")


def test_finish_succeeded_without_quality_gate_marker_is_rejected() -> None:
    base = task_state()
    gates = base.gates.model_copy(
        update={
            "test_required": False,
            "formal_required": False,
            "regression_required": False,
            "formal_regression_required": False,
            "has_blocking_failure": False,
            "can_finish_as_success": False,
        }
    )
    state = running_task(gates=gates)

    assert_guard_code(
        SchedulerGuardViolationCode.QUALITY_GATE_REQUIRED,
        validate_finish_task,
        state,
        "succeeded",
    )


def test_finish_succeeded_with_quality_gate_marker_is_allowed() -> None:
    base = task_state()
    gates = base.gates.model_copy(
        update={
            "test_required": False,
            "formal_required": False,
            "regression_required": False,
            "formal_regression_required": False,
            "has_blocking_failure": False,
            "can_finish_as_success": True,
        }
    )
    state = running_task(gates=gates)

    validate_finish_task(state, "succeeded")


def test_rejected_action_does_not_mutate_task_state() -> None:
    state = running_task(requirements=requirements_ref())
    before = state.model_dump(mode="json")

    assert_guard_code(
        SchedulerGuardViolationCode.MISSING_CURRENT_CODE,
        validate_worker_call,
        state,
        "plc-test",
        [requirements_ref()],
    )

    assert state.model_dump(mode="json") == before
