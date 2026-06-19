import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.db_models import Base
from app.models.router_schema import (
    ArtifactRef,
    CurrentArtifacts,
    DifficultyProfile,
    DifficultySignals,
    Failure,
    FailureReproduction,
    GateState,
    TaskState,
    TaskTrace,
)
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.quality_gate import (
    FINAL_GATE,
    FORMAL_GATE,
    QUALITY_GATE_TYPES,
    REGRESSION_GATE,
    TEST_GATE,
    QualityGateService,
    assess_quality_gate,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def task_state() -> TaskState:
    return TaskState.model_validate(load_fixture("task_state.valid.json"))


def artifact_ref(artifact_id: str, artifact_type: str, version: int = 1) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        type=artifact_type,
        version=version,
        uri=f"local://artifacts/task-001/{artifact_id}",
        summary=f"{artifact_type} artifact",
    )


def raw_request_ref() -> ArtifactRef:
    return artifact_ref("artifact-raw-request-001", "raw_user_request")


def code_ref(version: int = 1) -> ArtifactRef:
    return artifact_ref(f"artifact-code-{version:03d}", "plc_code", version)


def report_ref() -> ArtifactRef:
    return artifact_ref("artifact-test-report-001", "test_report")


def formal_report_ref() -> ArtifactRef:
    return artifact_ref("artifact-formal-report-001", "formal_report")


def patch_ref() -> ArtifactRef:
    return artifact_ref("artifact-patch-001", "patch")


def quiet_signals(**updates: bool) -> DifficultySignals:
    values = {
        "has_existing_code": False,
        "has_io_points": False,
        "has_timing_logic": False,
        "has_state_machine": False,
        "has_safety_constraints": False,
        "has_emergency_stop": False,
        "has_interlock": False,
        "has_fault_latching": False,
        "has_mode_switching": False,
        "multi_module": False,
        "requirement_incomplete": False,
    }
    values.update(updates)
    return DifficultySignals(**values)


def difficulty(
    *,
    level: str,
    requires_test: bool = False,
    requires_formal: bool = False,
    signals: DifficultySignals | None = None,
) -> DifficultyProfile:
    return DifficultyProfile(
        level=level,
        score=0.0,
        confidence=0.9,
        reasons=["quality gate test state"],
        signals=signals or quiet_signals(),
        requires_test=requires_test,
        requires_formal=requires_formal,
        requires_repair_loop=False,
        need_clarification=False,
    )


def gate_state(**updates: Any) -> GateState:
    values: dict[str, Any] = {
        "test_required": False,
        "formal_required": False,
        "regression_required": False,
        "formal_regression_required": False,
        "latest_test_passed": None,
        "latest_formal_passed": None,
        "has_blocking_failure": False,
        "can_finish_as_success": False,
    }
    values.update(updates)
    return GateState(**values)


def gate_task(
    *,
    task_type: str = "qa",
    difficulty_profile: DifficultyProfile | None = None,
    gates: GateState | None = None,
    current_code: ArtifactRef | None = None,
    latest_test_report: ArtifactRef | None = None,
    latest_formal_report: ArtifactRef | None = None,
    latest_patch: ArtifactRef | None = None,
    failures: list[Failure] | None = None,
    task_id: str = "task-001",
) -> TaskState:
    raw = raw_request_ref()
    artifact_refs = [
        ref
        for ref in (
            raw,
            current_code,
            latest_test_report,
            latest_formal_report,
            latest_patch,
        )
        if ref is not None
    ]
    base = task_state()
    return base.model_copy(
        deep=True,
        update={
            "task_id": task_id,
            "status": "running",
            "phase": "planning",
            "task_type": task_type,
            "difficulty": difficulty_profile or difficulty(level="L1"),
            "gates": gates or gate_state(),
            "current_artifacts": CurrentArtifacts(
                raw_user_request=raw,
                current_code=current_code,
                latest_test_report=latest_test_report,
                latest_formal_report=latest_formal_report,
                latest_patch=latest_patch,
                all_artifact_ids=[ref.artifact_id for ref in artifact_refs],
            ),
            "failures": failures or [],
            "active_worker_jobs": [],
            "completed_worker_job_ids": [],
            "unresolved_questions": [],
            "event_seq": 0,
        },
    )


def blocking_failure(state: TaskState) -> Failure:
    report = report_ref()
    return Failure(
        failure_id="failure-blocking-001",
        source="test",
        severity="blocking",
        title="Blocking test failure",
        description="The generated code violates a blocking test.",
        expected="The output remains false.",
        actual="The output became true.",
        reproduction=FailureReproduction(input_trace_artifact_id=report.artifact_id),
        evidence_artifact_ids=[report.artifact_id],
        status="open",
        created_by_worker_job_id="worker-job-test-001",
        created_at=state.created_at,
    )


def test_assessment_returns_all_gate_outcomes() -> None:
    assessment = assess_quality_gate(gate_task())

    assert [outcome.gate_type for outcome in assessment.outcomes] == list(
        QUALITY_GATE_TYPES
    )


def test_l1_qa_task_can_pass_without_test_or_formal_reports() -> None:
    assessment = assess_quality_gate(
        gate_task(
            task_type="qa",
            difficulty_profile=difficulty(level="L1"),
            gates=gate_state(test_required=False, formal_required=False),
        )
    )

    assert assessment.status == "passed"
    assert assessment.outcome_for(TEST_GATE).passed
    assert assessment.outcome_for(FORMAL_GATE).passed


def test_l2_development_without_passing_test_evidence_fails_test_gate() -> None:
    assessment = assess_quality_gate(
        gate_task(
            task_type="new_plc_development",
            difficulty_profile=difficulty(level="L2", requires_test=True),
            gates=gate_state(test_required=True),
            current_code=code_ref(),
        )
    )

    outcome = assessment.outcome_for(TEST_GATE)

    assert assessment.status == "failed"
    assert outcome.status == "failed"
    assert outcome.blocking is True


def test_l3_task_without_passing_formal_evidence_fails_formal_gate() -> None:
    assessment = assess_quality_gate(
        gate_task(
            task_type="new_plc_development",
            difficulty_profile=difficulty(level="L3", requires_formal=True),
            gates=gate_state(
                test_required=True,
                formal_required=True,
                latest_test_passed=True,
            ),
            current_code=code_ref(),
            latest_test_report=report_ref(),
        )
    )

    outcome = assessment.outcome_for(FORMAL_GATE)

    assert assessment.status == "failed"
    assert outcome.status == "failed"
    assert outcome.blocking is True


def test_open_blocking_failure_fails_final_gate() -> None:
    base = gate_task()
    state = base.model_copy(deep=True, update={"failures": [blocking_failure(base)]})

    assessment = assess_quality_gate(state)

    outcome = assessment.outcome_for(FINAL_GATE)
    assert assessment.status == "failed"
    assert outcome.status == "failed"
    assert outcome.evidence_artifact_ids == (report_ref().artifact_id,)


def test_pending_regression_flags_fail_regression_gate() -> None:
    assessment = assess_quality_gate(
        gate_task(
            gates=gate_state(regression_required=True),
            latest_patch=patch_ref(),
        )
    )

    outcome = assessment.outcome_for(REGRESSION_GATE)
    assert assessment.status == "failed"
    assert outcome.status == "failed"
    assert outcome.blocking is True
    assert patch_ref().artifact_id in outcome.evidence_artifact_ids


def test_persisted_passing_run_writes_audit_records_and_success_marker(
    db_session: Session,
    tmp_path: Path,
) -> None:
    state = gate_task(task_id="task-passing").model_copy(
        deep=True,
        update={
            "trace": TaskTrace(
                openai_trace_id="trace-001",
                main_agent_run_ids=["main-agent-run-001"],
                latest_main_agent_run_id="main-agent-run-001",
            )
        },
    )
    TaskRepository(db_session).create_task(state)

    result = QualityGateService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).run_quality_gate(state.task_id)

    events = EventService(db_session).list_visible_events(state.task_id)
    gate_results = GateResultRepository(db_session).list_results(state.task_id)
    gate_report = ArtifactRepository(db_session).get_artifact(
        result.gate_report.artifact_id
    )

    assert result.assessment.status == "passed"
    assert result.task.gates.can_finish_as_success is True
    assert result.task.current_artifacts.latest_gate_report is not None
    assert result.task.current_artifacts.latest_gate_report.artifact_id == gate_report.artifact_id
    assert gate_report.type == "gate_report"
    assert len(gate_results) == len(QUALITY_GATE_TYPES)
    assert [event.type for event in events] == ["gate.started", "gate.passed"]
    assert events[0].correlation.openai_trace_id == "trace-001"
    assert events[0].correlation.main_agent_run_id == "main-agent-run-001"
    assert events[-1].correlation.openai_trace_id == "trace-001"
    assert events[-1].correlation.main_agent_run_id == "main-agent-run-001"
    assert events[-1].correlation.artifact_ids == [gate_report.artifact_id]


def test_persisted_failing_run_writes_audit_records_and_clears_success_marker(
    db_session: Session,
    tmp_path: Path,
) -> None:
    state = gate_task(
        task_id="task-failing",
        task_type="new_plc_development",
        difficulty_profile=difficulty(level="L3", requires_formal=True),
        gates=gate_state(
            test_required=True,
            formal_required=True,
            latest_test_passed=True,
        ),
        current_code=code_ref(),
        latest_test_report=report_ref(),
    )
    TaskRepository(db_session).create_task(state)

    result = QualityGateService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).run_quality_gate(state.task_id)

    events = EventService(db_session).list_visible_events(state.task_id)
    gate_results = GateResultRepository(db_session).list_results(state.task_id)
    formal_result = next(
        gate_result for gate_result in gate_results if gate_result.gate_type == FORMAL_GATE
    )

    assert result.assessment.status == "failed"
    assert result.task.gates.can_finish_as_success is False
    assert result.task.current_artifacts.latest_gate_report is not None
    assert formal_result.status == "failed"
    assert formal_result.blocking is True
    assert [event.type for event in events] == ["gate.started", "gate.failed"]
    assert events[-1].correlation.artifact_ids == [result.gate_report.artifact_id]
