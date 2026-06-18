import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.tools import (
    AgentToolContext,
    AgentToolService,
    ParallelWorkerRequest,
    get_main_agent_tools,
)
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    CurrentArtifacts,
    DifficultyProfile,
    DifficultySignals,
    Failure,
    FailureReproduction,
    GateState,
    TaskPhase,
    TaskState,
    TaskStatus,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService
from app.workers.worker_input_builder import (
    WorkerInputBuildError,
    build_worker_input,
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


@pytest.fixture()
def service(db_session: Session, tmp_path: Path) -> AgentToolService:
    return AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
        )
    )


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def classified_task(
    db_session: Session,
    *,
    task_id: str = "task-agent-tools",
    qa: bool = False,
) -> TaskState:
    base = TaskState.model_validate(load_fixture("task_state.valid.json"))
    difficulty = quiet_difficulty() if qa else base.difficulty
    gates = quiet_gates() if qa else base.gates
    task = base.model_copy(
        deep=True,
        update={
            "task_id": task_id,
            "session_id": f"session-{task_id}",
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "task_type": "qa" if qa else "new_plc_development",
            "difficulty": difficulty,
            "gates": gates,
            "normalized_goal": base.raw_user_request,
            "event_seq": 0,
            "current_artifacts": CurrentArtifacts(all_artifact_ids=[]),
            "active_worker_jobs": [],
            "completed_worker_job_ids": [],
            "failures": [],
            "unresolved_questions": [],
        },
    )
    return TaskRepository(db_session).create_task(task)


def quiet_signals() -> DifficultySignals:
    return DifficultySignals(
        has_existing_code=False,
        has_io_points=False,
        has_timing_logic=False,
        has_state_machine=False,
        has_safety_constraints=False,
        has_emergency_stop=False,
        has_interlock=False,
        has_fault_latching=False,
        has_mode_switching=False,
        multi_module=False,
        requirement_incomplete=False,
    )


def quiet_difficulty() -> DifficultyProfile:
    return DifficultyProfile(
        level="L1",
        score=0.1,
        confidence=0.9,
        reasons=["QA task for agent tool test."],
        signals=quiet_signals(),
        requires_test=False,
        requires_formal=False,
        requires_repair_loop=False,
        need_clarification=False,
    )


def quiet_gates(**updates: Any) -> GateState:
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


def store(db_session: Session, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(session=db_session, artifact_root=tmp_path / "artifacts")


def create_raw_artifact(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> ArtifactRef:
    artifact = store(db_session, tmp_path).write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.RAW_USER_REQUEST,
            version=1,
            name="raw_user_request.json",
            content={"message": task.raw_user_request},
            summary="Raw request for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="application/json",
        )
    ).artifact
    return store(db_session, tmp_path).get_artifact_ref(artifact.artifact_id)


def create_requirements_and_code(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> tuple[ArtifactRef, ArtifactRef]:
    artifact_store = store(db_session, tmp_path)
    raw = create_raw_artifact(db_session, tmp_path, task)
    requirements = artifact_store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.REQUIREMENTS_IR,
            version=1,
            name="requirements_ir_v1.json",
            content={"goal": task.raw_user_request},
            summary="Requirements for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            parent_artifact_ids=(raw.artifact_id,),
            mime_type="application/json",
        )
    ).artifact
    code = artifact_store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.PLC_CODE,
            version=1,
            name="plc_code_v1.st",
            content="FUNCTION_BLOCK FB_MotorControl\nMotorRun := StartCmd;\nEND_FUNCTION_BLOCK\n",
            summary="PLC code v1 for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            parent_artifact_ids=(requirements.artifact_id,),
            metadata={"code_metadata": {"code_version": 1, "is_current": True}},
            mime_type="text/plain",
        )
    ).artifact
    return (
        artifact_store.get_artifact_ref(requirements.artifact_id),
        artifact_store.get_artifact_ref(code.artifact_id),
    )


def create_text_artifact(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
    *,
    content: str = "0123456789abcdef",
) -> ArtifactRef:
    artifact = store(db_session, tmp_path).write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.MISC,
            version=1,
            name="notes.txt",
            content=content,
            summary="Text artifact for read tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="text/plain",
        )
    ).artifact
    return store(db_session, tmp_path).get_artifact_ref(artifact.artifact_id)


def blocking_failure(task: TaskState, evidence: ArtifactRef) -> Failure:
    return Failure(
        failure_id="failure-test-001",
        source="test",
        severity="blocking",
        title="Blocking test failure",
        description="The current PLC code failed a blocking test.",
        reproduction=FailureReproduction(input_trace_artifact_id=evidence.artifact_id),
        evidence_artifact_ids=[evidence.artifact_id],
        status="open",
        created_by_worker_job_id="worker-job-test-failed",
        created_at=task.created_at,
    )


def worker_job_rows(db_session: Session) -> list[WorkerJobRow]:
    return list(db_session.execute(select(WorkerJobRow)).scalars())


def test_sdk_tool_list_exposes_expected_names() -> None:
    tools = get_main_agent_tools()

    assert [tool.name for tool in tools] == [
        "call_plc_dev",
        "call_plc_test",
        "call_plc_formal",
        "call_plc_repair",
        "run_parallel_workers",
        "read_artifact",
        "run_quality_gate",
        "finish_task",
    ]


def test_worker_input_builder_selects_validator_inputs(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    current = TaskRepository(db_session).get_task(task.task_id)

    payload = build_worker_input(current, WorkerType.PLC_TEST)

    assert payload.worker_type == "plc-test"
    assert payload.mode == "test"
    assert [artifact.artifact_id for artifact in payload.input_artifacts] == [
        requirements.artifact_id,
        code.artifact_id,
    ]
    assert payload.context.user_goal == current.normalized_goal
    assert payload.context.repair_round == 0


def test_worker_input_builder_rejects_missing_repair_evidence(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)
    current = TaskRepository(db_session).get_task(task.task_id)

    with pytest.raises(WorkerInputBuildError):
        build_worker_input(current, WorkerType.PLC_REPAIR)


def test_call_plc_dev_invokes_mock_worker_and_returns_compact_refs(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_raw_artifact(db_session, tmp_path, task)

    result = service.call_plc_dev(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "applied"
    assert result.worker_type == "plc-dev"
    assert result.artifact_refs
    assert result.artifact is None
    assert updated.current_artifacts.current_code is not None
    assert updated.active_worker_jobs == []
    assert updated.runtime_limits.active_parallel_workers == 0
    assert updated.runtime_limits.worker_calls_used == 1
    assert result.worker_job_id in updated.completed_worker_job_ids


def test_worker_tool_rationale_is_observable_without_changing_worker_input(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    create_raw_artifact(db_session, tmp_path, task)
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
        openai_trace_id="trace-001",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.call_plc_dev(
        task.task_id,
        objective="Generate motor control code.",
        rationale_summary="No current code exists, so start with PLC development.",
    )
    events = EventService(db_session).list_visible_events(task.task_id)
    job = worker_job_rows(db_session)[0]

    assert result.status == "applied"
    assert "main_agent.tool_called" in [event.type for event in events]
    assert "main_agent.tool_result" in [event.type for event in events]
    tool_call = next(event for event in events if event.type == "main_agent.tool_called")
    assert (
        tool_call.payload["rationale_summary"]
        == "No current code exists, so start with PLC development."
    )
    assert job.input_json["objective"] == "Generate motor control code."
    assert "rationale_summary" not in job.input_json["metadata"]


def test_call_plc_test_without_current_code_is_rejected_without_side_effects(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)

    result = service.call_plc_test(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "missing_current_code"
    assert updated.runtime_limits.worker_calls_used == 0
    assert updated.active_worker_jobs == []
    assert worker_job_rows(db_session) == []


def test_guard_rejection_is_recorded_when_observability_is_enabled(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.call_plc_test(
        task.task_id,
        rationale_summary="Testing is required before final delivery.",
    )
    events = EventService(db_session).list_visible_events(task.task_id)
    tool_result = next(event for event in events if event.type == "main_agent.tool_result")

    assert result.status == "rejected"
    assert [event.type for event in events] == [
        "main_agent.turn_started",
        "main_agent.tool_called",
        "main_agent.tool_result",
    ]
    assert tool_result.payload["status"] == "rejected"
    assert tool_result.payload["details"]["violation"]["code"] == "missing_current_code"
    assert worker_job_rows(db_session) == []


def test_call_plc_repair_without_failure_is_rejected_without_side_effects(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)

    result = service.call_plc_repair(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "no_open_blocking_failure"
    assert updated.runtime_limits.worker_calls_used == 0
    assert updated.active_worker_jobs == []
    assert worker_job_rows(db_session) == []


def test_run_parallel_workers_rejects_invalid_batch_atomically(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)

    result = service.run_parallel_workers(
        task.task_id,
        [
            ParallelWorkerRequest(worker_type="plc-test"),
            ParallelWorkerRequest(worker_type="plc-formal"),
        ],
    )
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "missing_current_code"
    assert updated.runtime_limits.worker_calls_used == 0
    assert updated.active_worker_jobs == []
    assert worker_job_rows(db_session) == []


def test_read_artifact_summary_and_bounded_full_modes(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    artifact = create_text_artifact(db_session, tmp_path, task)

    summary = service.read_artifact(task.task_id, artifact.artifact_id, mode="summary")
    full = service.read_artifact(
        task.task_id,
        artifact.artifact_id,
        mode="full",
        max_chars=5,
    )

    assert summary.status == "applied"
    assert summary.artifact is not None
    assert summary.artifact.content is None
    assert full.artifact is not None
    assert full.artifact.content == "01234"
    assert full.artifact.content_truncated is True
    assert full.artifact.content_chars == 5


def test_read_artifact_rejects_foreign_task_artifact(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    first = classified_task(db_session, task_id="task-first")
    second = classified_task(db_session, task_id="task-second")
    foreign = create_text_artifact(db_session, tmp_path, second)

    result = service.read_artifact(first.task_id, foreign.artifact_id, mode="full")

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "foreign_artifact"
    assert result.artifact is None


def test_run_quality_gate_returns_assessment_and_gate_report(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)
    create_raw_artifact(db_session, tmp_path, task)

    result = service.run_quality_gate(task.task_id)

    assert result.status == "applied"
    assert result.details["assessment_status"] == "passed"
    assert result.details["blocking"] is False
    assert result.artifact_refs[0].type == "gate_report"
    assert result.gate_state is not None
    assert result.gate_state.can_finish_as_success is True


def test_finish_task_rejects_succeeded_without_quality_gate(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)

    result = service.finish_task(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "quality_gate_required"
    assert updated.status == "running"


def test_finish_task_is_noop_in_report_first_orchestration_context(
    db_session: Session,
    tmp_path: Path,
) -> None:
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            report_first_finalization=True,
        )
    )
    task = classified_task(db_session, qa=True)
    create_raw_artifact(db_session, tmp_path, task)
    service.run_quality_gate(task.task_id)

    result = service.finish_task(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)
    events = EventService(db_session).list_visible_events(task.task_id)

    assert result.status == "no-op"
    assert result.details["report_first_finalization"] is True
    assert result.next_recommended_action == "return_final_output"
    assert updated.status == "running"
    assert "task.succeeded" not in [event.type for event in events]


def test_finish_task_marks_succeeded_after_quality_gate_passes(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)
    create_raw_artifact(db_session, tmp_path, task)
    service.run_quality_gate(task.task_id)

    result = service.finish_task(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)
    events = EventService(db_session).list_visible_events(task.task_id)

    assert result.status == "applied"
    assert updated.status == "succeeded"
    assert updated.phase == "completed"
    assert updated.completed_at is not None
    assert [event.type for event in events][-1] == "task.succeeded"


def test_tool_checkpoint_callback_runs_after_gate_and_finish(
    db_session: Session,
    tmp_path: Path,
) -> None:
    checkpoints: list[str] = []
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            checkpoint=lambda: checkpoints.append("checkpoint"),
        )
    )
    task = classified_task(db_session, qa=True)
    create_raw_artifact(db_session, tmp_path, task)

    service.run_quality_gate(task.task_id)
    service.finish_task(task.task_id)

    assert checkpoints == ["checkpoint", "checkpoint"]


def test_finish_task_rejects_cancelled_task_without_overwrite(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)
    cancelled = task.model_copy(
        deep=True,
        update={
            "status": TaskStatus.CANCELLED.value,
            "phase": TaskPhase.COMPLETED.value,
        },
    )
    TaskRepository(db_session).update_task_state(cancelled)

    result = service.finish_task(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "terminal_task"
    assert updated.status == "cancelled"
    assert EventService(db_session).list_visible_events(task.task_id) == []
