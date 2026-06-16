import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.db_models import Base
from app.models.router_schema import RouterEvent, TaskState
from app.repositories.task_repo import TaskRepository
from app.services.event_service import (
    EventService,
    SSE_HEARTBEAT_FRAME,
    iter_event_stream,
    normalize_event_cursor,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def task_state() -> TaskState:
    return TaskState.model_validate(load_fixture("task_state.valid.json"))


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


def create_task(session: Session) -> TaskState:
    task = task_state()
    TaskRepository(session).create_task(task)
    return task


def test_event_service_appends_events_with_sequences_and_reads_in_order(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task = create_task(session)
        service = EventService(session)

        first = service.append_event(router_event("event-service-001"))
        second = service.append_event(
            router_event("event-service-002", event_type="worker.completed")
        )
        listed = service.list_events(task.task_id, include_internal=True)

    assert first.seq == 1
    assert second.seq == 2
    assert [event.event_id for event in listed] == [
        "event-service-001",
        "event-service-002",
    ]
    assert [event.seq for event in listed] == [1, 2]


def test_event_service_hides_internal_events_by_default(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task = create_task(session)
        service = EventService(session)
        service.append_event(router_event("event-visible", visibility="user"))
        service.append_event(router_event("event-internal", visibility="internal"))

        visible = service.list_visible_events(task.task_id)
        all_events = service.list_events(task.task_id, include_internal=True)

    assert [event.event_id for event in visible] == ["event-visible"]
    assert [event.event_id for event in all_events] == [
        "event-visible",
        "event-internal",
    ]


def test_event_service_reads_after_sequence_cursor(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task = create_task(session)
        service = EventService(session)
        service.append_event(router_event("event-001"))
        service.append_event(router_event("event-002", event_type="artifact.created"))
        service.append_event(router_event("event-003", event_type="worker.completed"))

        visible = service.list_visible_events(task.task_id, after_seq=1)

    assert [event.seq for event in visible] == [2, 3]
    assert [event.event_id for event in visible] == ["event-002", "event-003"]


def test_normalize_event_cursor_prefers_explicit_after_seq() -> None:
    cursor = normalize_event_cursor(after_seq=3, last_event_id="1")

    assert cursor == 3


def test_event_stream_emits_idle_heartbeat(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task = create_task(session)
        session.commit()

    frames = list(
        iter_event_stream(
            session_factory,
            task.task_id,
            poll_interval_seconds=0,
            heartbeat_interval_seconds=0,
            stop_after_idle_heartbeats=1,
        )
    )

    assert frames == [SSE_HEARTBEAT_FRAME]
