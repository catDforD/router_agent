import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import RepositoryConflictError
from app.models.db_models import Base, EventRow, TaskRow
from app.models.router_schema import Artifact, RouterEvent, TaskState, WorkerInput, WorkerResult
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.event_repo import EventRepository
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRepository


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def db_session() -> Session:
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


def naive_datetime(value: Any) -> Any:
    if value is None:
        return None
    return value.replace(tzinfo=None)


def task_state() -> TaskState:
    return TaskState.model_validate(load_fixture("task_state.valid.json"))


def artifact() -> Artifact:
    return Artifact.model_validate(load_fixture("artifact.plc_code.valid.json"))


def router_event(event_id: str = "event-001") -> RouterEvent:
    payload = load_fixture("event.worker_started.valid.json")
    payload["event_id"] = event_id
    return RouterEvent.model_validate(payload)


def worker_input() -> WorkerInput:
    return WorkerInput.model_validate(load_fixture("worker_input.plc_dev.valid.json"))


def worker_result_for_dev_job() -> WorkerResult:
    payload = load_fixture("worker_result.test_failed.valid.json")
    payload["worker_job_id"] = "worker-job-dev-001"
    payload["worker_type"] = "plc-dev"
    payload["mcp_tool"] = "plc_dev.run"
    payload["trace_context"]["worker_job_id"] = "worker-job-dev-001"
    return WorkerResult.model_validate(payload)


def create_task(session: Session) -> TaskState:
    task = task_state()
    TaskRepository(session).create_task(task)
    return task


def test_create_task_then_get_task_restores_complete_task_state(
    db_session: Session,
) -> None:
    task = create_task(db_session)

    restored = TaskRepository(db_session).get_task(task.task_id)

    assert restored == task


def test_task_projection_columns_match_saved_task_state(db_session: Session) -> None:
    task = create_task(db_session)

    row = db_session.get(TaskRow, task.task_id)

    assert row is not None
    assert row.id == task.task_id
    assert row.session_id == task.session_id
    assert row.user_id == task.user_id
    assert row.status == task.status
    assert row.phase == task.phase
    assert row.task_type == task.task_type
    assert row.difficulty_level == task.difficulty.level
    assert row.created_at == naive_datetime(task.created_at)
    assert row.updated_at == naive_datetime(task.updated_at)
    assert row.completed_at == naive_datetime(task.completed_at)


def test_append_event_assigns_monotonic_sequence_per_task(db_session: Session) -> None:
    task = create_task(db_session)
    repository = EventRepository(db_session)

    first = repository.append_event(router_event("event-001"))
    second = repository.append_event(router_event("event-002"))
    listed = repository.list_events(task.task_id)
    updated_task = TaskRepository(db_session).get_task(task.task_id)

    assert first.seq == 1
    assert second.seq == 2
    assert [event.seq for event in listed] == [1, 2]
    assert updated_task.event_seq == 2


def test_task_update_preserves_newer_event_sequence(
    db_session: Session,
) -> None:
    task = create_task(db_session)
    event_repository = EventRepository(db_session)
    task_repository = TaskRepository(db_session)

    first = event_repository.append_event(router_event("event-001"))
    stale_update = task.model_copy(update={"status": "running"})
    updated = task_repository.update_task_state(stale_update)
    second = event_repository.append_event(router_event("event-002"))
    restored = task_repository.get_task(task.task_id)

    assert first.seq == 1
    assert updated.event_seq == 1
    assert second.seq == 2
    assert restored.event_seq == 2
    assert restored.status == "running"


def test_duplicate_event_sequence_is_rejected_by_database_constraint(
    db_session: Session,
) -> None:
    task = create_task(db_session)
    event = EventRepository(db_session).append_event(router_event("event-001"))

    duplicate = EventRow(
        id="event-duplicate",
        task_id=task.task_id,
        seq=event.seq,
        type=event.type,
        severity=event.severity,
        visibility=event.visibility,
        event_json=event.model_copy(update={"event_id": "event-duplicate"}).model_dump(
            mode="json"
        ),
        created_at=event.created_at,
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_create_artifact_then_get_artifact_restores_complete_artifact(
    db_session: Session,
) -> None:
    create_task(db_session)
    saved = ArtifactRepository(db_session).create_artifact(artifact())

    restored = ArtifactRepository(db_session).get_artifact(saved.artifact_id)

    assert restored == saved


def test_duplicate_artifact_id_is_rejected(db_session: Session) -> None:
    create_task(db_session)
    repository = ArtifactRepository(db_session)
    saved = artifact()
    repository.create_artifact(saved)

    with pytest.raises(RepositoryConflictError):
        repository.create_artifact(saved)


def test_worker_job_can_be_created_and_completed(db_session: Session) -> None:
    create_task(db_session)
    repository = WorkerJobRepository(db_session)
    input_payload = worker_input()
    result_payload = worker_result_for_dev_job()

    created = repository.create_job(input_payload)
    completed = repository.complete_job(input_payload.worker_job_id, result_payload)
    restored = repository.get_job(input_payload.worker_job_id)

    assert created.status == "running"
    assert created.input == input_payload
    assert created.result is None
    assert completed.status == "completed"
    assert completed.result == result_payload
    assert restored.input == input_payload
    assert restored.result == result_payload


def test_gate_results_can_be_created_and_listed_by_task(db_session: Session) -> None:
    task = create_task(db_session)
    repository = GateResultRepository(db_session)

    first = repository.create_result(
        task_id=task.task_id,
        gate_type="quality_gate",
        status="failed",
        blocking=True,
        evidence_artifact_ids=["artifact-test-report-001"],
        result={"reason": "test failed"},
        created_at=task.created_at,
        gate_result_id="gate-result-001",
    )
    second = repository.create_result(
        task_id=task.task_id,
        gate_type="quality_gate",
        status="passed",
        blocking=False,
        evidence_artifact_ids=[],
        result={"reason": "recheck passed"},
        created_at=task.updated_at,
        gate_result_id="gate-result-002",
    )

    listed = repository.list_results(task.task_id)

    assert [result.id for result in listed] == [first.id, second.id]
    assert listed[0].result == first.result
    assert listed[1].result == second.result
