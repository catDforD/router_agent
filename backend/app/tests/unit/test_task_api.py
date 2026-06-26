import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import events as event_api
from app.api import sessions as sessions_api
from app.api import tasks as tasks_api
from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.core.errors import RepositoryNotFoundError
from app.main import create_app
from app.models.db_models import Base
from app.models.router_schema import ArtifactType
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.services import event_service as event_service_module
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService
from app.services.task_service import TaskService


@pytest.fixture()
def api_context(tmp_path: Path) -> Iterator[tuple[Settings, sessionmaker[Session]]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'router.db'}"
    engine = get_engine_for_url(database_url)
    Base.metadata.create_all(engine)
    factory = get_session_factory_for_url(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
    )
    try:
        yield settings, factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_engine_for_url.cache_clear()
        get_session_factory_for_url.cache_clear()


@pytest.fixture()
def limited_event_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    def limited_stream(
        session_factory: sessionmaker[Session],
        task_id: str,
        *,
        after_seq: int = 0,
    ) -> Iterator[str]:
        yield from event_service_module.iter_event_stream(
            session_factory,
            task_id,
            after_seq=after_seq,
            poll_interval_seconds=0,
            heartbeat_interval_seconds=0,
            stop_after_idle_heartbeats=1,
        )

    monkeypatch.setattr(event_api, "iter_event_stream", limited_stream)

    def limited_session_stream(
        session_factory: sessionmaker[Session],
        session_id: str,
        *,
        after_seq: int = 0,
    ) -> Iterator[str]:
        yield from event_service_module.iter_session_event_stream(
            session_factory,
            session_id,
            after_seq=after_seq,
            poll_interval_seconds=0,
            heartbeat_interval_seconds=0,
            stop_after_idle_heartbeats=1,
        )

    monkeypatch.setattr(sessions_api, "iter_session_event_stream", limited_session_stream)


@pytest.fixture(autouse=True)
def scheduled_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, str | None]]:
    scheduled: list[tuple[str, str, str | None]] = []

    def fake_start(task_id: str, settings: Settings | None = None) -> None:
        scheduled.append(
            ("start", task_id, settings.database_url if settings is not None else None)
        )

    def fake_resume(task_id: str, settings: Settings | None = None) -> None:
        scheduled.append(
            ("resume", task_id, settings.database_url if settings is not None else None)
        )

    def fake_followup(
        task_id: str,
        message_artifact_id: str,
        settings: Settings | None = None,
    ) -> None:
        scheduled.append(
            (
                "followup",
                task_id,
                settings.database_url if settings is not None else None,
            )
        )

    monkeypatch.setattr(tasks_api, "run_runtime_start_task", fake_start)
    monkeypatch.setattr(tasks_api, "run_runtime_resume_task", fake_resume)
    monkeypatch.setattr(sessions_api, "run_runtime_start_task", fake_start)
    monkeypatch.setattr(tasks_api, "run_runtime_followup_task", fake_followup)
    return scheduled


def create_task(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    message: str = "Create motor control logic.",
) -> str:
    with session_factory() as session:
        result = TaskService(
            session=session,
            artifact_root=settings.artifact_root,
        ).create_task(message=message)
        session.commit()
        return result.task.task_id


def task_payload() -> dict[str, Any]:
    return {
        "message": "Create conveyor logic with emergency stop.",
        "project_context": {
            "target_plc_language": "ST",
            "target_platform": "Codesys",
        },
    }


def test_create_task_endpoint_returns_handle_and_persists_side_effects(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = api_context

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/tasks", json=task_payload())

    assert response.status_code == 201
    payload = response.json()
    task_id = payload["task_id"]
    assert payload == {
        "task_id": task_id,
        "status": "created",
        "events_url": f"/api/tasks/{task_id}/events",
    }
    assert scheduled_runtime == [("start", task_id, settings.database_url)]

    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        artifacts = ArtifactRepository(session).list_task_artifacts(task_id)
        events = EventService(session).list_visible_events(task_id)

    assert task.raw_user_request == task_payload()["message"]
    assert task.project_context.target_plc_language == "ST"
    assert task.current_artifacts.raw_user_request is not None
    assert [artifact.type for artifact in artifacts] == ["raw_user_request"]
    assert [event.type for event in events] == ["task.created"]
    assert events[0].correlation.artifact_ids == [artifacts[0].artifact_id]

    with TestClient(create_app(settings)) as client:
        stream = client.get(f"/api/tasks/{task_id}/events")

    assert stream.status_code == 200
    assert "event: task.created\n" in stream.text


def test_create_session_endpoint_returns_session_and_streams_session_events(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = api_context

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/sessions", json=task_payload())

    assert response.status_code == 201
    payload = response.json()
    session_id = payload["session"]["session_id"]
    task_id = payload["task_id"]
    assert payload["session"]["latest_task_id"] == task_id
    assert payload["session"]["latest_run_id"] == task_id
    assert payload["events_url"] == f"/api/sessions/{session_id}/events"
    assert scheduled_runtime == [("start", task_id, settings.database_url)]

    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        task_events = EventService(session).list_visible_events(task_id)
        session_events = EventService(session).list_visible_session_events(session_id)

    assert task.session_id == session_id
    assert [event.type for event in task_events] == ["task.created"]
    assert [event.type for event in session_events] == ["task.created"]
    assert session_events[0].seq == 1
    assert session_events[0].payload["message"] == task_payload()["message"]

    with TestClient(create_app(settings)) as client:
        stream = client.get(f"/api/sessions/{session_id}/events")

    assert stream.status_code == 200
    assert "event: task.created\n" in stream.text


def test_append_session_message_creates_new_run_after_completed_task(
    api_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = api_context
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/sessions", json=task_payload()).json()
    session_id = created["session"]["session_id"]
    first_task_id = created["task_id"]

    with session_factory() as session:
        task = TaskRepository(session).get_task(first_task_id)
        TaskRepository(session).update_task_state(
            task.model_copy(update={"status": "succeeded", "phase": "completed"})
        )
        session.commit()

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "继续说明上一次回答。"},
        )

    assert response.status_code == 200
    payload = response.json()
    second_task_id = payload["task_id"]
    assert second_task_id != first_task_id
    assert payload["session"]["latest_task_id"] == second_task_id
    assert scheduled_runtime[-1] == ("start", second_task_id, settings.database_url)

    with session_factory() as session:
        first = TaskRepository(session).get_task(first_task_id)
        second = TaskRepository(session).get_task(second_task_id)
        session_events = EventService(session).list_visible_session_events(session_id)

    assert first.status == "succeeded"
    assert second.status == "created"
    assert second.session_id == session_id
    assert [event.payload.get("message") for event in session_events] == [
        task_payload()["message"],
        "继续说明上一次回答。",
    ]


def test_create_task_endpoint_rejects_blank_message(
    api_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, _session_factory = api_context

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/tasks", json={"message": "   "})

    assert response.status_code == 422
    assert scheduled_runtime == []


def test_get_task_endpoint_returns_current_task_state(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory, message="Create pump logic.")

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["raw_user_request"] == "Create pump logic."
    assert payload["event_seq"] == 1


def test_delete_task_endpoint_removes_task(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory, message="Create pump logic.")

    with session_factory() as session:
        result = tasks_api.delete_task(
            task_id,
            session=session,
            service=TaskService(session=session, artifact_root=settings.artifact_root),
        )

    assert result is None
    with session_factory() as session:
        with pytest.raises(RepositoryNotFoundError):
            TaskRepository(session).get_task(task_id)


def test_list_tasks_endpoint_returns_recent_tasks(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    older_task_id = create_task(settings, session_factory, message="Create pump logic.")
    newer_task_id = create_task(settings, session_factory, message="Create fan logic.")

    with session_factory() as session:
        response = tasks_api.list_tasks(
            limit=10,
            service=TaskService(session=session, artifact_root=settings.artifact_root),
        )

    assert [task.task_id for task in response.tasks] == [
        newer_task_id,
        older_task_id,
    ]
    assert response.tasks[0].raw_user_request == "Create fan logic."


def test_get_task_endpoint_missing_task_returns_not_found(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, _session_factory = api_context

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/tasks/missing-task")

    assert response.status_code == 404


def test_get_task_trace_endpoint_returns_compact_summary(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory, message="Create pump logic.")

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task_id}/trace")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["main_agent_run_ids"] == []
    assert [event["type"] for event in payload["events"]] == ["task.created"]
    assert payload["events"][0]["payload_keys"] == [
        "message",
        "raw_user_request_artifact_id",
        "run_id",
        "session_id",
        "status",
        "task_id",
    ]
    assert payload["artifacts"][0]["type"] == "raw_user_request"
    assert "inline_content" not in payload["artifacts"][0]


def test_get_task_trace_endpoint_missing_task_returns_not_found(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, _session_factory = api_context

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/tasks/missing-task/trace")

    assert response.status_code == 404


def test_get_task_trace_endpoint_omits_artifact_content(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    secret = "DO-NOT-EMBED-TRACE-CONTENT"
    task_id = create_task(settings, session_factory, message="Create pump logic.")
    with session_factory() as session:
        ArtifactStore(
            session=session,
            artifact_root=settings.artifact_root,
        ).write_artifact_content(
            ArtifactContentWrite(
                task_id=task_id,
                artifact_type=ArtifactType.MAIN_AGENT_LOG,
                version=1,
                name="main_agent_log.json",
                content={"secret": secret},
                summary="Internal replay log.",
                visibility="internal",
                metadata={"tags": ["trace_test"]},
                mime_type="application/json",
            )
        )
        session.commit()

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task_id}/trace")

    assert response.status_code == 200
    payload_text = json.dumps(response.json())
    assert "main_agent_log" in payload_text
    assert secret not in payload_text


def test_append_user_message_endpoint_stores_artifact_and_event(
    api_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/tasks/{task_id}/messages",
            json={"message": "Also include manual mode."},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["task_id"] == task_id
    message_artifact_id = payload["message_artifact_id"]
    assert scheduled_runtime == [("resume", task_id, settings.database_url)]

    with session_factory() as session:
        artifact = ArtifactRepository(session).get_artifact(message_artifact_id)
        events = EventService(session).list_visible_events(task_id)

    assert artifact.type == "misc"
    assert artifact.metadata.tags == ["user_message"]
    assert [event.type for event in events] == ["task.created", "task.updated"]
    assert events[-1].correlation.artifact_ids == [message_artifact_id]


def test_append_user_message_endpoint_errors(
    api_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory)

    with TestClient(create_app(settings)) as client:
        blank = client.post(f"/api/tasks/{task_id}/messages", json={"message": ""})
        missing = client.post(
            "/api/tasks/missing-task/messages",
            json={"message": "hello"},
        )
        client.post(f"/api/tasks/{task_id}/cancel")
        terminal = client.post(
            f"/api/tasks/{task_id}/messages",
            json={"message": "too late"},
        )

    assert blank.status_code == 422
    assert missing.status_code == 404
    assert terminal.status_code == 200
    assert scheduled_runtime == [("followup", task_id, settings.database_url)]


def test_cancel_task_endpoint_updates_state_and_is_idempotent(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory)

    with TestClient(create_app(settings)) as client:
        first = client.post(f"/api/tasks/{task_id}/cancel")
        second = client.post(f"/api/tasks/{task_id}/cancel")

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert first.json()["phase"] == "completed"
    assert first.json()["completed_at"] is not None
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"

    with session_factory() as session:
        events = EventService(session).list_visible_events(task_id)

    assert [event.type for event in events] == ["task.created", "task.cancelled"]


def test_cancel_task_endpoint_terminal_and_missing_errors(
    api_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = api_context
    task_id = create_task(settings, session_factory)
    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        TaskRepository(session).update_task_state(
            task.model_copy(update={"status": "succeeded"})
        )
        session.commit()

    with TestClient(create_app(settings)) as client:
        terminal = client.post(f"/api/tasks/{task_id}/cancel")
        missing = client.post("/api/tasks/missing-task/cancel")

    assert terminal.status_code == 409
    assert missing.status_code == 404
