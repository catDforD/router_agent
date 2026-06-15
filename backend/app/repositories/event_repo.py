"""Router event persistence repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryConflictError, RepositoryNotFoundError
from app.models.db_models import EventRow, TaskRow
from app.models.router_schema import RouterEvent
from app.repositories._helpers import dump_model, enum_value, flush_or_raise_conflict


class EventRepository:
    """Repository for append-only Router events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append_event(self, event: RouterEvent) -> RouterEvent:
        if self.session.get(EventRow, event.event_id) is not None:
            raise RepositoryConflictError(f"event already exists: {event.event_id}")

        task_row = self.session.execute(
            select(TaskRow)
            .where(TaskRow.id == event.task_id)
            .with_for_update()
        ).scalar_one_or_none()
        if task_row is None:
            raise RepositoryNotFoundError(f"task not found: {event.task_id}")

        next_seq = task_row.event_seq + 1
        persisted_event = event.model_copy(update={"seq": next_seq})
        event_payload = dump_model(persisted_event)

        task_state_payload = dict(task_row.state_json)
        task_state_payload["event_seq"] = next_seq
        task_row.event_seq = next_seq
        task_row.state_json = task_state_payload

        row = EventRow(
            id=persisted_event.event_id,
            task_id=persisted_event.task_id,
            seq=persisted_event.seq,
            type=enum_value(persisted_event.type),
            severity=enum_value(persisted_event.severity),
            visibility=enum_value(persisted_event.visibility),
            event_json=event_payload,
            created_at=persisted_event.created_at,
        )
        self.session.add(row)
        flush_or_raise_conflict(
            self.session,
            f"event sequence conflicts for task: {event.task_id}",
        )
        return persisted_event

    def list_events(self, task_id: str) -> list[RouterEvent]:
        rows = self.session.execute(
            select(EventRow)
            .where(EventRow.task_id == task_id)
            .order_by(EventRow.seq)
        ).scalars()
        return [RouterEvent.model_validate(row.event_json) for row in rows]
