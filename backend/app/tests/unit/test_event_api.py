import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import events as event_api
from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.main import create_app
from app.models.db_models import Base
from app.models.router_schema import RouterEvent, TaskState
from app.repositories.task_repo import TaskRepository
from app.services import event_service as event_service_module
from app.services.event_service import EventService


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def create_task(session_factory: sessionmaker[Session]) -> TaskState:
    task = TaskState.model_validate(load_fixture("task_state.valid.json"))
    with session_factory() as session:
        TaskRepository(session).create_task(task)
        session.commit()
    return task


def router_event(
    event_id: str,
    *,
    event_type: str = "worker.started",
    visibility: str = "user",
) -> RouterEvent:
    payload = deepcopy(load_fixture("event.worker_started.valid.json"))
    payload["event_id"] = event_id
    payload["seq"] = 0
    payload["type"] = event_type
    payload["visibility"] = visibility
    payload["title"] = f"{event_type} {visibility}"
    return RouterEvent.model_validate(payload)


def append_event(
    session_factory: sessionmaker[Session],
    event: RouterEvent,
) -> RouterEvent:
    with session_factory() as session:
        appended = EventService(session).append_event(event)
        session.commit()
        return appended


def test_event_stream_endpoint_returns_event_stream_content_type(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task.task_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_event_stream_endpoint_replays_existing_visible_events(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    append_event(session_factory, router_event("event-visible-001"))
    append_event(session_factory, router_event("event-internal-001", visibility="internal"))
    append_event(
        session_factory,
        router_event("event-visible-002", event_type="worker.completed"),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task.task_id}/events")

    assert response.status_code == 200
    body = response.text
    assert "event-visible-001" in body
    assert "event-visible-002" in body
    assert "event-internal-001" not in body


def test_event_stream_frame_contains_id_event_and_data(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    appended = append_event(session_factory, router_event("event-frame-001"))

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task.task_id}/events")

    assert response.status_code == 200
    body = response.text
    assert f"id: {appended.seq}\n" in body
    assert "event: worker.started\n" in body
    data_line = next(line for line in body.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["event_id"] == "event-frame-001"
    assert payload["seq"] == appended.seq


def test_event_stream_resumes_from_last_event_id(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    append_event(session_factory, router_event("event-001"))
    append_event(session_factory, router_event("event-002", event_type="artifact.created"))
    append_event(session_factory, router_event("event-003", event_type="worker.completed"))

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/tasks/{task.task_id}/events",
            headers={"Last-Event-ID": "1"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event-001" not in body
    assert "event-002" in body
    assert "event-003" in body


def test_event_stream_after_seq_overrides_last_event_id(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    append_event(session_factory, router_event("event-001"))
    append_event(session_factory, router_event("event-002", event_type="artifact.created"))
    append_event(session_factory, router_event("event-003", event_type="worker.completed"))

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/tasks/{task.task_id}/events?after_seq=2",
            headers={"Last-Event-ID": "1"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event-001" not in body
    assert "event-002" not in body
    assert "event-003" in body


def test_event_stream_missing_task_returns_not_found(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, _ = api_context

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/tasks/missing-task/events")

    assert response.status_code == 404
