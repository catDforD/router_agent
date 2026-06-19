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
from app.core.time import utc_now
from app.main import create_app
from app.models.db_models import Base
from app.models.router_schema import (
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    RouterEvent,
    TaskState,
)
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


def main_agent_event(
    event_id: str,
    *,
    event_type: EventType | str,
    payload: dict[str, Any] | None = None,
    visibility: EventVisibility | str = EventVisibility.USER,
) -> RouterEvent:
    return RouterEvent(
        schema_version="router.v1",
        event_id=event_id,
        task_id="task-001",
        seq=0,
        type=event_type,
        source=EventSource(
            type=EventSourceType.MAIN_AGENT,
            id="main-agent-run-001",
        ),
        severity=EventSeverity.INFO,
        visibility=visibility,
        title=f"{event_type}",
        message=None,
        correlation=EventCorrelation(
            openai_trace_id="trace-001",
            main_agent_run_id="main-agent-run-001",
            artifact_ids=(
                [
                    payload["final_report_artifact_id"],
                    payload["main_agent_log_artifact_id"],
                ]
                if payload
                and event_type == EventType.MAIN_AGENT_COMPLETED
                else None
            ),
        ),
        payload=payload or {"task_id": "task-001"},
        created_at=utc_now(),
    )


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


def test_event_stream_replays_main_agent_observability_events(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-turn",
            event_type=EventType.MAIN_AGENT_TURN_STARTED,
            payload={"task_id": task.task_id, "turn_index": 1, "phase": "orchestration"},
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-message",
            event_type=EventType.MAIN_AGENT_MESSAGE,
            payload={
                "task_id": task.task_id,
                "turn_index": 1,
                "phase": "orchestration",
                "visibility": "public",
                "content": "I am preparing the worker call.",
            },
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-call",
            event_type=EventType.MAIN_AGENT_TOOL_CALLED,
            payload={
                "task_id": task.task_id,
                "turn_index": 1,
                "tool_name": "call_plc_dev",
                "rationale_summary": "No current code exists.",
                "arguments": {"task_id": task.task_id},
                "input_artifact_ids": ["artifact-raw-user-request"],
            },
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-result",
            event_type=EventType.MAIN_AGENT_TOOL_RESULT,
            payload={
                "task_id": task.task_id,
                "turn_index": 1,
                "tool_name": "call_plc_dev",
                "status": "applied",
                "summary": "PLC development completed.",
                "artifact_ids": ["artifact-plc-code"],
                "failure_ids": [],
            },
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-completed",
            event_type=EventType.MAIN_AGENT_COMPLETED,
            payload={
                "task_id": task.task_id,
                "main_agent_run_id": "main-agent-run-001",
                "final_task_status": "succeeded",
                "summary": "Task completed.",
                "final_report_artifact_id": "artifact-final-report",
                "main_agent_log_artifact_id": "artifact-main-agent-log",
            },
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task.task_id}/events")

    assert response.status_code == 200
    body = response.text
    assert "event: main_agent.message\n" in body
    assert "event: main_agent.tool_called\n" in body
    assert "event: main_agent.tool_result\n" in body
    assert "event: main_agent.completed\n" in body
    assert "event-main-agent-message" in body
    assert "event-main-agent-call" in body
    assert "event-main-agent-result" in body
    assert "event-main-agent-completed" in body


def test_event_stream_last_event_id_resumes_across_main_agent_events(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    turn = append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-turn",
            event_type=EventType.MAIN_AGENT_TURN_STARTED,
            payload={"task_id": task.task_id, "turn_index": 1, "phase": "orchestration"},
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-call",
            event_type=EventType.MAIN_AGENT_TOOL_CALLED,
            payload={
                "task_id": task.task_id,
                "turn_index": 1,
                "tool_name": "call_plc_test",
                "rationale_summary": "Current code is ready for validation.",
                "arguments": {"task_id": task.task_id},
                "input_artifact_ids": ["artifact-plc-code"],
            },
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-result",
            event_type=EventType.MAIN_AGENT_TOOL_RESULT,
            payload={
                "task_id": task.task_id,
                "turn_index": 1,
                "tool_name": "call_plc_test",
                "status": "applied",
                "summary": "PLC tests passed.",
                "artifact_ids": ["artifact-test-report"],
                "failure_ids": [],
            },
        ),
    )
    append_event(
        session_factory,
        main_agent_event(
            "event-main-agent-completed",
            event_type=EventType.MAIN_AGENT_COMPLETED,
            payload={
                "task_id": task.task_id,
                "main_agent_run_id": "main-agent-run-001",
                "final_task_status": "succeeded",
                "summary": "Task completed.",
                "final_report_artifact_id": "artifact-final-report",
                "main_agent_log_artifact_id": "artifact-main-agent-log",
            },
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/tasks/{task.task_id}/events",
            headers={"Last-Event-ID": str(turn.seq)},
        )

    assert response.status_code == 200
    body = response.text
    assert "event-main-agent-turn" not in body
    assert "event-main-agent-call" in body
    assert "event-main-agent-result" in body
    assert "event-main-agent-completed" in body


def test_event_stream_missing_task_returns_not_found(
    api_context: tuple[Settings, sessionmaker[Session]],
    limited_event_stream: None,
) -> None:
    settings, _ = api_context

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/tasks/missing-task/events")

    assert response.status_code == 404
