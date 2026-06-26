"""Agent session persistence repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryConflictError, RepositoryNotFoundError
from app.models.db_models import AgentRunRow, AgentSessionEventRow, AgentSessionRow
from app.models.router_schema import (
    AgentSession,
    AgentSessionRunRef,
    AgentSessionStatus,
    EventVisibility,
    RouterEvent,
)
from app.repositories._helpers import (
    dump_model,
    enum_value,
    flush_or_raise_conflict,
    sanitize_legacy_agent_session_payload,
)


class AgentSessionRepository:
    """Repository for persistent Codex-like conversation sessions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_session(self, agent_session: AgentSession) -> AgentSession:
        if self.session.get(AgentSessionRow, agent_session.session_id) is not None:
            raise RepositoryConflictError(
                f"agent session already exists: {agent_session.session_id}"
            )
        row = AgentSessionRow(id=agent_session.session_id)
        self._apply_session(row, agent_session)
        self.session.add(row)
        flush_or_raise_conflict(
            self.session,
            f"agent session already exists: {agent_session.session_id}",
        )
        return agent_session

    def ensure_session(self, agent_session: AgentSession) -> AgentSession:
        existing = self.session.get(AgentSessionRow, agent_session.session_id)
        if existing is not None:
            return self.get_session(agent_session.session_id)
        return self.create_session(agent_session)

    def get_session(self, session_id: str) -> AgentSession:
        row = self.session.get(AgentSessionRow, session_id)
        if row is None:
            raise RepositoryNotFoundError(f"agent session not found: {session_id}")
        return self._session_from_row(row)

    def get_session_for_update(self, session_id: str) -> AgentSession:
        row = self.session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise RepositoryNotFoundError(f"agent session not found: {session_id}")
        return self._session_from_row(row)

    def list_sessions(self, *, limit: int = 50) -> list[AgentSession]:
        if limit < 1:
            raise ValueError("session query limit must be greater than zero")
        rows = self.session.execute(
            select(AgentSessionRow)
            .order_by(AgentSessionRow.updated_at.desc(), AgentSessionRow.id)
            .limit(limit)
        ).scalars()
        return [self._session_from_row(row) for row in rows]

    def update_session(self, agent_session: AgentSession) -> AgentSession:
        row = self.session.get(AgentSessionRow, agent_session.session_id)
        if row is None:
            raise RepositoryNotFoundError(
                f"agent session not found: {agent_session.session_id}"
            )
        event_seq = max(row.event_seq, agent_session.event_seq)
        if event_seq != agent_session.event_seq:
            agent_session = agent_session.model_copy(update={"event_seq": event_seq})
        self._apply_session(row, agent_session)
        flush_or_raise_conflict(
            self.session,
            f"agent session update conflicts: {agent_session.session_id}",
        )
        return agent_session

    def delete_session(self, session_id: str) -> None:
        row = self.session.get(AgentSessionRow, session_id)
        if row is None:
            raise RepositoryNotFoundError(f"agent session not found: {session_id}")
        self.session.execute(
            delete(AgentSessionEventRow).where(
                AgentSessionEventRow.session_id == session_id
            )
        )
        self.session.execute(
            delete(AgentRunRow).where(AgentRunRow.session_id == session_id)
        )
        self.session.delete(row)
        self.session.flush()

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        task_id: str,
        status: str,
        user_message: str,
        created_at: datetime,
    ) -> AgentSessionRunRef:
        if self.session.get(AgentRunRow, run_id) is not None:
            raise RepositoryConflictError(f"agent run already exists: {run_id}")
        run = AgentSessionRunRef(
            run_id=run_id,
            task_id=task_id,
            status=status,
            user_message=user_message,
            created_at=created_at,
            updated_at=created_at,
        )
        self.session.add(
            AgentRunRow(
                id=run_id,
                session_id=session_id,
                task_id=task_id,
                status=status,
                user_message=user_message,
                final_response=None,
                run_json=run.model_dump(mode="json"),
                created_at=created_at,
                updated_at=created_at,
                completed_at=None,
            )
        )
        flush_or_raise_conflict(self.session, f"agent run already exists: {run_id}")
        return run

    def update_run_from_event(self, session_id: str, event: RouterEvent) -> None:
        run_id = str(event.payload.get("run_id") or event.task_id)
        row = self.session.get(AgentRunRow, run_id)
        if row is None or row.session_id != session_id:
            return

        status = row.status
        completed_at = row.completed_at
        final_response = row.final_response
        event_type = enum_value(event.type)
        if event_type == "agent.started":
            status = "running"
        elif event_type == "agent.final_response":
            final_response = str(event.payload.get("content") or event.message or "")
        elif event_type == "task.succeeded":
            status = "succeeded"
            completed_at = event.created_at
        elif event_type == "task.partial_failed":
            status = "partial_failed"
            completed_at = event.created_at
        elif event_type == "task.failed":
            status = "failed"
            completed_at = event.created_at
        elif event_type == "task.cancelled":
            status = "cancelled"
            completed_at = event.created_at
        else:
            return

        updated = AgentSessionRunRef(
            run_id=row.id,
            task_id=row.task_id,
            status=status,
            user_message=row.user_message,
            final_response=final_response,
            created_at=row.created_at,
            updated_at=event.created_at,
            completed_at=completed_at,
        )
        row.status = status
        row.final_response = final_response
        row.completed_at = completed_at
        row.updated_at = event.created_at
        row.run_json = updated.model_dump(mode="json")

    def append_session_event(
        self,
        *,
        session_id: str,
        event: RouterEvent,
    ) -> RouterEvent | None:
        row = self.session.execute(
            select(AgentSessionRow)
            .where(AgentSessionRow.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            return None

        next_seq = row.event_seq + 1
        payload = dict(event.payload)
        payload.setdefault("session_id", session_id)
        payload.setdefault("run_id", event.task_id)
        session_event = event.model_copy(
            deep=True,
            update={
                "seq": next_seq,
                "payload": payload,
                "correlation": event.correlation.model_copy(
                    update={
                        "session_id": session_id,
                        "run_id": str(payload["run_id"]),
                    }
                ),
            },
        )

        row.event_seq = next_seq
        row.updated_at = event.created_at
        session_payload = dict(row.session_json)
        session_payload["event_seq"] = next_seq
        session_payload["updated_at"] = event.created_at.isoformat()
        row.session_json = session_payload

        self.update_run_from_event(session_id, session_event)
        self.session.add(
            AgentSessionEventRow(
                id=event.event_id,
                session_id=session_id,
                seq=session_event.seq,
                task_id=event.task_id,
                type=enum_value(event.type),
                severity=enum_value(event.severity),
                visibility=enum_value(event.visibility),
                event_json=session_event.model_dump(mode="json"),
                created_at=event.created_at,
            )
        )
        flush_or_raise_conflict(
            self.session,
            f"session event sequence conflicts for session: {session_id}",
        )
        return session_event

    def list_session_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        visibility: EventVisibility | str | None = None,
        limit: int | None = None,
    ) -> list[RouterEvent]:
        if limit is not None and limit < 1:
            raise ValueError("event query limit must be greater than zero")
        statement = select(AgentSessionEventRow).where(
            AgentSessionEventRow.session_id == session_id,
            AgentSessionEventRow.seq > after_seq,
        )
        if visibility is not None:
            statement = statement.where(
                AgentSessionEventRow.visibility == enum_value(visibility)
            )
        statement = statement.order_by(AgentSessionEventRow.seq)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.execute(statement).scalars()
        return [RouterEvent.model_validate(row.event_json) for row in rows]

    def _session_from_row(self, row: AgentSessionRow) -> AgentSession:
        runs = self.session.execute(
            select(AgentRunRow)
            .where(AgentRunRow.session_id == row.id)
            .order_by(AgentRunRow.created_at, AgentRunRow.id)
        ).scalars()
        payload = dict(row.session_json)
        payload["runs"] = [
            AgentSessionRunRef.model_validate(run.run_json).model_dump(mode="json")
            for run in runs
        ]
        payload["event_seq"] = row.event_seq
        payload["latest_run_id"] = row.latest_run_id
        payload["latest_task_id"] = row.latest_task_id
        return AgentSession.model_validate(sanitize_legacy_agent_session_payload(payload))

    @staticmethod
    def _apply_session(row: AgentSessionRow, agent_session: AgentSession) -> None:
        row.user_id = agent_session.user_id
        row.status = enum_value(agent_session.status)
        row.title = agent_session.title
        row.latest_task_id = agent_session.latest_task_id
        row.latest_run_id = agent_session.latest_run_id
        row.event_seq = agent_session.event_seq
        row.summary = agent_session.summary
        row.session_json = dump_model(agent_session)
        row.created_at = agent_session.created_at
        row.updated_at = agent_session.updated_at
