import json
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.mcp.adapter import McpAdapter
from app.models.db_models import Base
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    CurrentArtifacts,
    ExpectedOutputSpec,
    Failure,
    FailureReproduction,
    GateState,
    TaskPhase,
    TaskState,
    TaskStatus,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerJobRef,
    WorkerMode,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.workers.worker_result_handler import (
    WorkerResultHandler,
    WorkerResultHandlerInvalidArtifactError,
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
def task(db_session: Session) -> TaskState:
    base = TaskState.model_validate(load_fixture("task_state.valid.json"))
    running = base.model_copy(
        deep=True,
        update={
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "event_seq": 0,
            "current_artifacts": CurrentArtifacts(all_artifact_ids=[]),
            "active_worker_jobs": [],
            "completed_worker_job_ids": [],
            "failures": [],
            "unresolved_questions": [],
        },
    )
    TaskRepository(db_session).create_task(running)
    return running


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def store(db_session: Session, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(session=db_session, artifact_root=tmp_path / "artifacts")


def adapter(db_session: Session, tmp_path: Path, *, mock_runner: Any | None = None) -> McpAdapter:
    return McpAdapter(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        mock_runner=mock_runner,
    )


def handler(db_session: Session) -> WorkerResultHandler:
    return WorkerResultHandler(db_session)


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
            summary="Raw request for worker result handler test.",
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
            summary="Requirements for handler test.",
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
            summary="PLC code v1 for handler test.",
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


def create_failed_test_report(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
    code: ArtifactRef,
) -> ArtifactRef:
    artifact_store = store(db_session, tmp_path)
    report = artifact_store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.TEST_REPORT,
            version=1,
            name="test_report_failed.json",
            content={"status": "failed", "failed": 1},
            summary="Failed test report for repair input.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            parent_artifact_ids=(code.artifact_id,),
            metadata={"test_metadata": {"status": "failed", "total": 1, "failed": 1}},
            mime_type="application/json",
        )
    ).artifact
    return artifact_store.get_artifact_ref(report.artifact_id)


def worker_input(
    task: TaskState,
    worker_type: WorkerType | str,
    input_artifacts: list[ArtifactRef],
) -> WorkerInput:
    worker = worker_type.value if isinstance(worker_type, WorkerType) else worker_type
    worker_job_id = f"worker-job-{worker.replace('-', '-')}-{uuid4().hex[:12]}"
    return WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id=worker_job_id,
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode=worker_mode(worker),
        objective=f"Run mock {worker}.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.normalized_goal or task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language="ST",
            target_platform="Codesys",
            repair_round=task.runtime_limits.repair_rounds,
            assumptions=[],
        ),
        constraints=[],
        expected_outputs=expected_outputs(worker),
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=worker_job_id),
        idempotency_key=f"{task.task_id}:{worker_job_id}",
        created_at=task.created_at,
    )


def worker_mode(worker_type: str) -> WorkerMode:
    return {
        WorkerType.PLC_DEV.value: WorkerMode.CREATE,
        WorkerType.PLC_TEST.value: WorkerMode.TEST,
        WorkerType.PLC_FORMAL.value: WorkerMode.FORMAL_VERIFY,
        WorkerType.PLC_REPAIR.value: WorkerMode.REPAIR,
    }[worker_type]


def expected_outputs(worker_type: str) -> list[ExpectedOutputSpec]:
    output_types = {
        WorkerType.PLC_DEV.value: [
            ArtifactType.REQUIREMENTS_IR,
            ArtifactType.PLC_CODE,
            ArtifactType.IO_CONTRACT,
        ],
        WorkerType.PLC_TEST.value: [ArtifactType.TEST_REPORT],
        WorkerType.PLC_FORMAL.value: [ArtifactType.FORMAL_REPORT],
        WorkerType.PLC_REPAIR.value: [
            ArtifactType.PATCH,
            ArtifactType.PLC_CODE,
            ArtifactType.REPAIR_SUMMARY,
        ],
    }[worker_type]
    return [
        ExpectedOutputSpec(
            artifact_type=artifact_type,
            required=True,
            description=f"Mock {artifact_type.value} output.",
        )
        for artifact_type in output_types
    ]


def set_active_job(
    db_session: Session,
    task_id: str,
    payload: WorkerInput,
) -> None:
    repository = TaskRepository(db_session)
    current = repository.get_task(task_id)
    repository.update_task_state(
        current.model_copy(
            deep=True,
            update={
                "active_worker_jobs": [
                    WorkerJobRef(
                        worker_job_id=payload.worker_job_id,
                        worker_type=payload.worker_type,
                        status="running",
                        objective=payload.objective,
                        started_at=payload.created_at,
                    )
                ]
            },
        )
    )


def blocking_failure(task: TaskState, *, source: str = "test") -> Failure:
    return Failure(
        failure_id=f"failure-{source}-001",
        source=source,
        severity="blocking",
        title=f"Open {source} failure",
        description=f"The current code has an open {source} failure.",
        reproduction=FailureReproduction(steps=[f"Run {source} reproduction."]),
        evidence_artifact_ids=["artifact-evidence-001"],
        status="open",
        created_by_worker_job_id=f"worker-job-{source}-failed",
        created_at=task.created_at,
    )


def update_task_state(
    db_session: Session,
    task: TaskState,
    **updates: Any,
) -> TaskState:
    repository = TaskRepository(db_session)
    current = repository.get_task(task.task_id)
    updated = current.model_copy(deep=True, update=updates)
    return repository.update_task_state(updated)


def test_plc_dev_result_updates_artifacts_and_job_tracking(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_DEV,
        [create_raw_artifact(db_session, tmp_path, task)],
    )
    result = adapter(db_session, tmp_path).call_worker(payload)
    set_active_job(db_session, task.task_id, payload)

    handled = handler(db_session).handle_worker_result(result)

    assert handled.applied is True
    assert handled.task.current_artifacts.requirements_ir is not None
    assert handled.task.current_artifacts.current_code is not None
    assert handled.task.current_artifacts.current_io_contract is not None
    assert handled.task.current_artifacts.current_code.version == 1
    assert payload.worker_job_id in handled.task.completed_worker_job_ids
    assert handled.task.active_worker_jobs == []
    assert {
        artifact.artifact_id for artifact in result.produced_artifacts
    } <= set(handled.task.current_artifacts.all_artifact_ids)


def test_failed_plc_test_appends_failure_and_sets_blocking_gate(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    payload = worker_input(task, WorkerType.PLC_TEST, [requirements, code])
    result = adapter(db_session, tmp_path).call_worker(
        payload,
        scenario="test_failed_then_repair_pass",
    )

    handled = handler(db_session).handle_worker_result(result)

    assert handled.task.current_artifacts.latest_test_report is not None
    assert handled.task.current_artifacts.latest_failing_trace is not None
    assert handled.task.gates.latest_test_passed is False
    assert handled.task.gates.has_blocking_failure is True
    assert len(handled.task.failures) == 1
    assert handled.task.failures[0].source == "test"
    assert handled.task.failures[0].status == "open"


def test_failed_plc_formal_records_counterexample_and_failure(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    payload = worker_input(task, WorkerType.PLC_FORMAL, [requirements, code])
    result = adapter(db_session, tmp_path).call_worker(
        payload,
        scenario="formal_failed_then_repair_pass",
    )

    handled = handler(db_session).handle_worker_result(result)

    assert handled.task.current_artifacts.latest_formal_report is not None
    assert handled.task.current_artifacts.latest_counterexample is not None
    assert handled.task.gates.latest_formal_passed is False
    assert handled.task.gates.has_blocking_failure is True
    assert len(handled.task.failures) == 1
    assert handled.task.failures[0].source == "formal"


def test_passed_plc_repair_updates_code_and_regression_state(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    report = create_failed_test_report(db_session, tmp_path, task, code)
    state_with_failure = update_task_state(
        db_session,
        task,
        failures=[blocking_failure(task, source="test")],
        gates=TaskRepository(db_session).get_task(task.task_id).gates.model_copy(
            update={"has_blocking_failure": True}
        ),
    )
    payload = worker_input(state_with_failure, WorkerType.PLC_REPAIR, [code, report])
    result = adapter(db_session, tmp_path).call_worker(payload)

    handled = handler(db_session).handle_worker_result(result)

    assert handled.task.current_artifacts.current_code is not None
    assert handled.task.current_artifacts.current_code.version == 2
    assert handled.task.current_artifacts.latest_patch is not None
    assert handled.task.current_artifacts.latest_repair_summary is not None
    assert handled.task.runtime_limits.repair_rounds == 1
    assert handled.task.gates.regression_required is True
    assert handled.task.gates.latest_test_passed is None


def test_repair_after_formal_failure_requires_formal_regression(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    report = create_failed_test_report(db_session, tmp_path, task, code)
    state_with_failure = update_task_state(
        db_session,
        task,
        failures=[blocking_failure(task, source="formal")],
        gates=TaskRepository(db_session).get_task(task.task_id).gates.model_copy(
            update={"has_blocking_failure": True}
        ),
    )
    payload = worker_input(state_with_failure, WorkerType.PLC_REPAIR, [code, report])
    result = adapter(db_session, tmp_path).call_worker(payload)

    handled = handler(db_session).handle_worker_result(result)

    assert handled.task.gates.regression_required is True
    assert handled.task.gates.formal_regression_required is True
    assert handled.task.failures[0].status == "open"


def test_reapplying_result_is_idempotent(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    report = create_failed_test_report(db_session, tmp_path, task, code)
    state_with_failure = update_task_state(
        db_session,
        task,
        failures=[blocking_failure(task, source="test")],
        gates=TaskRepository(db_session).get_task(task.task_id).gates.model_copy(
            update={"has_blocking_failure": True}
        ),
    )
    payload = worker_input(state_with_failure, WorkerType.PLC_REPAIR, [code, report])
    result = adapter(db_session, tmp_path).call_worker(payload)

    first = handler(db_session).handle_worker_result(result)
    second = handler(db_session).handle_worker_result(result)

    assert first.applied is True
    assert second.applied is False
    assert second.task.runtime_limits.repair_rounds == 1
    assert second.task.completed_worker_job_ids.count(payload.worker_job_id) == 1
    assert len(second.task.failures) == 1


def test_passing_validator_resolves_same_source_failures(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    state_with_failure = update_task_state(
        db_session,
        task,
        failures=[blocking_failure(task, source="test")],
        gates=TaskRepository(db_session).get_task(task.task_id).gates.model_copy(
            update={"has_blocking_failure": True, "regression_required": True}
        ),
    )
    payload = worker_input(state_with_failure, WorkerType.PLC_TEST, [requirements, code])
    result = adapter(db_session, tmp_path).call_worker(payload)

    handled = handler(db_session).handle_worker_result(result)

    assert handled.task.gates.latest_test_passed is True
    assert handled.task.gates.regression_required is False
    assert handled.task.gates.has_blocking_failure is False
    assert handled.task.failures[0].status == "resolved"
    assert handled.task.failures[0].resolved_by_worker_job_id == payload.worker_job_id
    assert handled.task.failures[0].resolved_by_artifact_id == (
        handled.task.current_artifacts.latest_test_report.artifact_id
    )


def test_clarification_timeout_and_error_paths_are_conservative(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    raw = create_raw_artifact(db_session, tmp_path, task)
    clarification_input = worker_input(task, WorkerType.PLC_DEV, [raw])
    clarification_result = adapter(db_session, tmp_path).call_worker(
        clarification_input,
        scenario="need_clarification",
    )

    clarified = handler(db_session).handle_worker_result(clarification_result).task

    assert clarified.status == "waiting_user"
    assert clarified.phase == "clarifying"
    assert len(clarified.unresolved_questions) == 1

    resumed = update_task_state(
        db_session,
        task,
        status=TaskStatus.RUNNING,
        phase=TaskPhase.PLANNING,
        unresolved_questions=[],
        gates=clarified.gates.model_copy(update={"latest_test_passed": True}),
    )
    timeout_input = worker_input(resumed, WorkerType.PLC_DEV, [raw])
    timeout_result = adapter(db_session, tmp_path).call_worker(
        timeout_input,
        scenario="worker_timeout",
    )

    timed_out = handler(db_session).handle_worker_result(timeout_result).task

    assert timeout_input.worker_job_id in timed_out.completed_worker_job_ids
    assert timed_out.current_artifacts.current_code is None
    assert timed_out.failures == []
    assert timed_out.gates.latest_test_passed is True

    def invalid_runner(worker_input: WorkerInput, *, scenario: str) -> object:
        return {"invalid": True}

    error_input = worker_input(timed_out, WorkerType.PLC_DEV, [raw])
    error_result = adapter(
        db_session,
        tmp_path,
        mock_runner=invalid_runner,
    ).call_worker(error_input)

    errored = handler(db_session).handle_worker_result(error_result).task

    assert error_input.worker_job_id in errored.completed_worker_job_ids
    assert errored.current_artifacts.current_code is None
    assert errored.failures == []
    assert errored.gates.latest_test_passed is True


def test_missing_produced_artifact_is_rejected_without_task_mutation(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_DEV,
        [create_raw_artifact(db_session, tmp_path, task)],
    )
    result = adapter(db_session, tmp_path).call_worker(payload)
    before = TaskRepository(db_session).get_task(task.task_id)
    missing_ref = result.produced_artifacts[0].model_copy(
        update={"artifact_id": "artifact-missing"}
    )
    bad_result = result.model_copy(
        update={
            "produced_artifacts": [
                missing_ref,
                *result.produced_artifacts[1:],
            ]
        }
    )

    with pytest.raises(WorkerResultHandlerInvalidArtifactError):
        handler(db_session).handle_worker_result(bad_result)

    after = TaskRepository(db_session).get_task(task.task_id)
    assert after == before
