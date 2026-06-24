from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.main_agent import MainAgentService
from app.models.db_models import Base
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.task_service import TaskMutationConflictError, TaskService


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
def service(db_session: Session, tmp_path: Path) -> TaskService:
    return TaskService(session=db_session, artifact_root=tmp_path / "artifacts")


def test_create_task_persists_task_state_with_request_context(
    db_session: Session,
    service: TaskService,
) -> None:
    result = service.create_task(
        message="Write a motor start stop routine.",
        project_context={
            "target_plc_language": "ST",
            "target_platform": "Codesys",
        },
    )

    restored = TaskRepository(db_session).get_task(result.task.task_id)

    assert restored.task_id == result.task.task_id
    assert restored.status == "created"
    assert restored.phase == "intake"
    assert restored.raw_user_request == "Write a motor start stop routine."
    assert restored.project_context.target_plc_language == "ST"
    assert restored.project_context.target_platform == "Codesys"
    assert restored.task_type == "unknown"


def test_create_task_keeps_classification_pending_until_agent_runs(
    service: TaskService,
) -> None:
    result = service.create_task(
        message=(
            "Implement conveyor logic with emergency stop, interlock, "
            "and fault latching."
        )
    )
    task = result.task

    assert task.task_type == "unknown"
    assert task.difficulty.level == "L0"
    assert task.difficulty.confidence == 0.1
    assert task.difficulty.reasons == [
        "Task created before detailed classification."
    ]
    assert task.difficulty.signals.has_emergency_stop is False
    assert task.difficulty.signals.has_interlock is False
    assert task.difficulty.signals.has_fault_latching is False
    assert task.difficulty.requires_test is False
    assert task.difficulty.requires_formal is False
    assert task.difficulty.requires_repair_loop is False
    assert task.gates.test_required is False
    assert task.gates.formal_required is False
    assert task.runtime_limits.max_repair_rounds == 3
    assert task.runtime_limits.max_parallel_workers == 4


def test_create_task_writes_raw_request_artifact_and_updates_task_pointer(
    db_session: Session,
    service: TaskService,
) -> None:
    result = service.create_task(message="Create conveyor logic.")

    restored = TaskRepository(db_session).get_task(result.task.task_id)
    artifact = ArtifactRepository(db_session).get_artifact(
        result.raw_user_request_artifact_id
    )

    assert artifact.task_id == result.task.task_id
    assert artifact.type == "raw_user_request"
    assert artifact.visibility == "user"
    assert restored.current_artifacts.raw_user_request is not None
    assert (
        restored.current_artifacts.raw_user_request.artifact_id
        == artifact.artifact_id
    )
    assert artifact.artifact_id in restored.current_artifacts.all_artifact_ids


def test_create_task_appends_visible_task_created_event(
    db_session: Session,
    service: TaskService,
) -> None:
    result = service.create_task(message="Create pump logic.")

    events = EventService(db_session).list_visible_events(result.task.task_id)

    assert len(events) == 1
    assert events[0].type == "task.created"
    assert events[0].visibility == "user"
    assert events[0].correlation.artifact_ids == [
        result.raw_user_request_artifact_id
    ]
    assert events[0].seq == 1
    assert result.task.event_seq == 1


def test_append_user_message_stores_artifact_and_task_updated_event(
    db_session: Session,
    service: TaskService,
) -> None:
    created = service.create_task(message="Create pump logic.").task
    previous_updated_at = created.updated_at

    appended = service.append_user_message(
        task_id=created.task_id,
        message="Also include manual mode.",
    )
    stored_content = service.artifact_store.read_artifact_content(
        appended.message_artifact_id
    )
    events = EventService(db_session).list_visible_events(created.task_id)

    assert appended.task.updated_at >= previous_updated_at
    assert stored_content.artifact.type == "misc"
    assert b"Also include manual mode." in stored_content.content
    assert appended.message_artifact_id in appended.task.current_artifacts.all_artifact_ids
    assert [event.type for event in events] == ["task.created", "task.updated"]
    assert events[-1].correlation.artifact_ids == [appended.message_artifact_id]


def test_cancel_task_updates_state_and_emits_event(
    db_session: Session,
    service: TaskService,
    tmp_path: Path,
) -> None:
    task = service.create_task(message="Create pump logic.").task
    started = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).start_main_agent_run(task.task_id)

    cancelled = service.cancel_task(task.task_id)
    events = EventService(db_session).list_visible_events(task.task_id)

    assert cancelled.status == "cancelled"
    assert cancelled.phase == "completed"
    assert cancelled.completed_at is not None
    assert [event.type for event in events] == [
        "task.created",
        "agent.started",
        "task.cancelled",
    ]
    assert events[-1].correlation.openai_trace_id == started.trace.openai_trace_id
    assert (
        events[-1].correlation.main_agent_run_id
        == started.trace.latest_main_agent_run_id
    )


def test_cancel_task_is_idempotent_when_already_cancelled(
    db_session: Session,
    service: TaskService,
) -> None:
    task = service.create_task(message="Create pump logic.").task
    first = service.cancel_task(task.task_id)

    second = service.cancel_task(task.task_id)
    events = EventService(db_session).list_visible_events(task.task_id)

    assert second == first
    assert [event.type for event in events] == ["task.created", "task.cancelled"]


def test_cancel_task_refreshes_stale_session_after_background_event(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'router.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    try:
        with factory() as session:
            task = TaskService(
                session=session,
                artifact_root=artifact_root,
            ).create_task(message="Create pump logic.").task
            session.commit()

        stale_session = factory()
        try:
            stale_service = TaskService(
                session=stale_session,
                artifact_root=artifact_root,
            )
            stale_service.get_task(task.task_id)

            with factory() as background_session:
                MainAgentService(
                    session=background_session,
                    artifact_root=artifact_root,
                ).start_main_agent_run(task.task_id)
                background_session.commit()

            cancelled = stale_service.cancel_task(task.task_id)
            stale_session.commit()
        finally:
            stale_session.close()

        with factory() as session:
            restored = TaskRepository(session).get_task(task.task_id)
            events = EventService(session).list_visible_events(task.task_id)

        assert cancelled.status == "cancelled"
        assert restored.status == "cancelled"
        assert [event.type for event in events] == [
            "task.created",
            "agent.started",
            "task.cancelled",
        ]
        assert [event.seq for event in events] == [1, 2, 3]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_cancel_task_rejects_terminal_status(
    db_session: Session,
    service: TaskService,
) -> None:
    task = service.create_task(message="Create pump logic.").task
    succeeded = task.model_copy(update={"status": "succeeded"})
    TaskRepository(db_session).update_task_state(succeeded)

    with pytest.raises(TaskMutationConflictError):
        service.cancel_task(task.task_id)


def test_append_user_message_rejects_terminal_task(
    service: TaskService,
) -> None:
    task = service.create_task(message="Create pump logic.").task
    service.cancel_task(task.task_id)

    with pytest.raises(TaskMutationConflictError):
        service.append_user_message(
            task_id=task.task_id,
            message="One more note.",
        )
