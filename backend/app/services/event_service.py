"""Router event service and SSE formatting helpers."""

from __future__ import annotations

from collections.abc import Iterator
import time

from sqlalchemy.orm import Session, sessionmaker

from app.models.router_schema import EventVisibility, RouterEvent
from app.repositories._helpers import enum_value
from app.repositories.event_repo import EventRepository
from app.repositories.task_repo import TaskRepository


DEFAULT_EVENT_BATCH_LIMIT = 100
DEFAULT_EVENT_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_EVENT_HEARTBEAT_INTERVAL_SECONDS = 15.0
SSE_HEARTBEAT_FRAME = ": keepalive\n\n"


class EventService:
    """Service boundary for persisted Router events."""

    def __init__(self, session: Session) -> None:
        self.event_repository = EventRepository(session)
        self.task_repository = TaskRepository(session)

    def append_event(self, event: RouterEvent) -> RouterEvent:
        return self.event_repository.append_event(event)

    def ensure_task_exists(self, task_id: str) -> None:
        self.task_repository.get_task(task_id)

    def list_events(
        self,
        task_id: str,
        *,
        after_seq: int = 0,
        include_internal: bool = False,
        limit: int | None = None,
    ) -> list[RouterEvent]:
        self.ensure_task_exists(task_id)
        visibility = None if include_internal else EventVisibility.USER
        return self.event_repository.list_events(
            task_id,
            after_seq=after_seq,
            visibility=visibility,
            limit=limit,
        )

    def list_visible_events(
        self,
        task_id: str,
        *,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> list[RouterEvent]:
        return self.list_events(
            task_id,
            after_seq=after_seq,
            include_internal=False,
            limit=limit,
        )


def normalize_event_cursor(
    *,
    after_seq: int | None,
    last_event_id: str | None,
) -> int:
    """Resolve an SSE resume cursor from explicit query or Last-Event-ID."""

    if after_seq is not None:
        if after_seq < 0:
            raise ValueError("after_seq must be greater than or equal to zero")
        return after_seq

    if last_event_id is None or last_event_id == "":
        return 0

    try:
        cursor = int(last_event_id)
    except ValueError as exc:
        raise ValueError("Last-Event-ID must be an integer sequence") from exc
    if cursor < 0:
        raise ValueError("Last-Event-ID must be greater than or equal to zero")
    return cursor


def format_sse_event(event: RouterEvent) -> str:
    """Serialize a Router event as one SSE frame."""

    return (
        f"id: {event.seq}\n"
        f"event: {enum_value(event.type)}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


def iter_event_stream(
    session_factory: sessionmaker[Session],
    task_id: str,
    *,
    after_seq: int = 0,
    poll_interval_seconds: float = DEFAULT_EVENT_POLL_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = DEFAULT_EVENT_HEARTBEAT_INTERVAL_SECONDS,
    batch_limit: int = DEFAULT_EVENT_BATCH_LIMIT,
    stop_after_idle_heartbeats: int | None = None,
) -> Iterator[str]:
    """Yield frontend-visible events for a task as SSE frames."""

    cursor = after_seq
    last_heartbeat_at = time.monotonic()
    idle_heartbeat_count = 0

    while True:
        with session_factory() as session:
            events = EventService(session).list_visible_events(
                task_id,
                after_seq=cursor,
                limit=batch_limit,
            )

        if events:
            idle_heartbeat_count = 0
            for event in events:
                yield format_sse_event(event)
                cursor = event.seq
            continue

        now = time.monotonic()
        if now - last_heartbeat_at >= heartbeat_interval_seconds:
            yield SSE_HEARTBEAT_FRAME
            last_heartbeat_at = now
            idle_heartbeat_count += 1
            if (
                stop_after_idle_heartbeats is not None
                and idle_heartbeat_count >= stop_after_idle_heartbeats
            ):
                return

        time.sleep(poll_interval_seconds)
